"""Linux usbfs bulk transport."""

from __future__ import annotations

import ctypes
import errno
import os
import select
import sys
import threading
import time

from ..device import UsbDeviceInfo
from ..protocol import DEFAULT_MAX_USB_CHUNK, DEFAULT_TIMEOUT_MS


def _ioc(direction: int, type_char: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | number


_IOC_READ = 2
_IOC_WRITE = 1
_IOC_NONE = 0

USBDEVFS_URB_TYPE_BULK = 3
_CANCELLED_URB_STATUSES = {
    -errno.ENOENT,
    -errno.ECONNRESET,
    -getattr(errno, "ECANCELED", 125),
}


class UsbdevfsBulkTransfer(ctypes.Structure):
    _fields_ = [
        ("ep", ctypes.c_uint),
        ("len", ctypes.c_uint),
        ("timeout", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    ]


class UsbdevfsUrb(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ubyte),
        ("endpoint", ctypes.c_ubyte),
        ("status", ctypes.c_int),
        ("flags", ctypes.c_uint),
        ("buffer", ctypes.c_void_p),
        ("buffer_length", ctypes.c_int),
        ("actual_length", ctypes.c_int),
        ("start_frame", ctypes.c_int),
        ("number_of_packets", ctypes.c_int),
        ("error_count", ctypes.c_int),
        ("signr", ctypes.c_uint),
        ("usercontext", ctypes.c_void_p),
    ]


USBDEVFS_BULK = _ioc(_IOC_READ | _IOC_WRITE, "U", 2, ctypes.sizeof(UsbdevfsBulkTransfer))
USBDEVFS_SUBMITURB = _ioc(_IOC_READ, "U", 10, ctypes.sizeof(UsbdevfsUrb))
USBDEVFS_DISCARDURB = _ioc(_IOC_NONE, "U", 11, 0)
USBDEVFS_REAPURB = _ioc(_IOC_WRITE, "U", 12, ctypes.sizeof(ctypes.c_void_p))
USBDEVFS_REAPURBNDELAY = _ioc(_IOC_WRITE, "U", 13, ctypes.sizeof(ctypes.c_void_p))
USBDEVFS_CLAIMINTERFACE = _ioc(_IOC_READ, "U", 15, ctypes.sizeof(ctypes.c_uint))
USBDEVFS_RELEASEINTERFACE = _ioc(_IOC_READ, "U", 16, ctypes.sizeof(ctypes.c_uint))


_LIBC = None


def _libc():
    global _LIBC
    if _LIBC is None:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.ioctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p)
        libc.ioctl.restype = ctypes.c_int
        _LIBC = libc
    return _LIBC


def _ioctl(fd: int, request: int, arg) -> int:
    ret = _libc().ioctl(fd, ctypes.c_ulong(request), arg)
    if ret < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return ret


def claim_interface(fd: int, interface: int) -> None:
    arg = ctypes.c_uint(interface)
    _ioctl(fd, USBDEVFS_CLAIMINTERFACE, ctypes.byref(arg))


def release_interface(fd: int, interface: int) -> None:
    arg = ctypes.c_uint(interface)
    _ioctl(fd, USBDEVFS_RELEASEINTERFACE, ctypes.byref(arg))


class UsbFsCancellableBulkRead:
    def __init__(
        self,
        fd: int,
        endpoint: int,
        read_size: int,
        timeout_ms: int | None,
        debug: bool = False,
        reap_poll_interval_s: float | None = None,
    ) -> None:
        self.fd = fd
        self.endpoint = endpoint
        self.read_size = read_size
        self.timeout_ms = timeout_ms
        self.debug = debug
        self._buffer = ctypes.create_string_buffer(read_size)
        self._urb = UsbdevfsUrb(
            USBDEVFS_URB_TYPE_BULK,
            endpoint,
            0,
            0,
            ctypes.cast(self._buffer, ctypes.c_void_p),
            read_size,
            0,
            0,
            0,
            0,
            0,
            None,
        )
        self._submitted = False
        self._reaped = False
        self._cancel_requested = False
        self._discard_submitted = False
        self._discard_failed_without_reap = False
        self._discard_failure_errno: int | None = None
        self._timed_out = False
        self._cancel_event = threading.Event()
        self._cancel_wakeup_lock = threading.Lock()
        self._cancel_wakeup_reader: int | None = None
        self._cancel_wakeup_writer: int | None = None

    def read(self) -> bytes:
        if self._cancel_requested:
            raise InterruptedError("USB bulk read cancelled")
        self._submit()
        if self._cancel_requested:
            self.cancel()
        deadline = None if self.timeout_ms is None else time.monotonic() + (self.timeout_ms / 1000)
        self._reap(deadline)
        status = self._urb.status
        if self._timed_out:
            return b""
        if self._cancel_requested or status in _CANCELLED_URB_STATUSES:
            raise InterruptedError("USB bulk read cancelled")
        if status != 0:
            raise OSError(abs(status), os.strerror(abs(status)))
        data = bytes(self._buffer.raw[: self._urb.actual_length])
        if self.debug and data:
            print(f" IN {len(data):4d}: {data[:32].hex(' ')}", file=sys.stderr)
        return data

    def cancel(self) -> None:
        self._cancel_requested = True
        self._discard_pending_urb()
        self._cancel_event.set()
        self._wake_cancel_waiters()

    def _discard_pending_urb(self) -> None:
        if (
            not self._submitted
            or self._reaped
            or self._discard_submitted
            or self._discard_failed_without_reap
        ):
            return
        try:
            _ioctl(self.fd, USBDEVFS_DISCARDURB, ctypes.byref(self._urb))
            self._discard_submitted = True
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENODEV, errno.ENOENT):
                raise
            self._discard_failed_without_reap = True
            self._discard_failure_errno = exc.errno

    def _submit(self) -> None:
        if self._submitted:
            return
        _ioctl(self.fd, USBDEVFS_SUBMITURB, ctypes.byref(self._urb))
        self._submitted = True

    def _reap(self, deadline: float | None) -> None:
        self._open_cancel_wakeup()
        try:
            while True:
                reaped = ctypes.c_void_p()
                try:
                    _ioctl(self.fd, USBDEVFS_REAPURBNDELAY, ctypes.byref(reaped))
                except OSError as exc:
                    if exc.errno != errno.EAGAIN:
                        raise
                    if self._cancel_requested:
                        self._finish_cancelled_reap()
                        return
                    if deadline is not None and time.monotonic() >= deadline:
                        self._timed_out = True
                        self.cancel()
                        self._finish_cancelled_reap()
                        return
                    self._wait_for_reap_ready(deadline)
                    continue
                self._accept_reaped_urb(reaped)
                return
        finally:
            self._close_cancel_wakeup()

    def _reap_blocking(self) -> None:
        reaped = ctypes.c_void_p()
        _ioctl(self.fd, USBDEVFS_REAPURB, ctypes.byref(reaped))
        self._accept_reaped_urb(reaped)

    def _finish_cancelled_reap(self) -> None:
        if self._discard_failed_without_reap:
            if self._discard_failure_errno in (errno.EINVAL, errno.ENOENT):
                self._reap_discard_race_completion()
                return
            self._reaped = True
            return
        self._reap_blocking()

    def _reap_discard_race_completion(self) -> None:
        reaped = ctypes.c_void_p()
        try:
            _ioctl(self.fd, USBDEVFS_REAPURBNDELAY, ctypes.byref(reaped))
        except OSError as exc:
            if exc.errno != errno.EAGAIN:
                raise
            self._reaped = True
            return
        self._accept_reaped_urb(reaped)

    def _accept_reaped_urb(self, reaped: ctypes.c_void_p) -> None:
        if reaped.value != ctypes.addressof(self._urb):
            raise OSError(errno.EIO, "reaped unexpected USB URB")
        self._reaped = True

    def _wait_for_reap_ready(self, deadline: float | None) -> None:
        timeout_ms = None
        if deadline is not None:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                return
            timeout_ms = max(1, int(remaining_s * 1000))
        self._poll_reap_ready(timeout_ms)

    def _poll_reap_ready(self, timeout_ms: int | None) -> None:
        poller = select.poll()
        poller.register(
            self.fd,
            select.POLLOUT | select.POLLERR | select.POLLHUP | select.POLLNVAL,
        )
        with self._cancel_wakeup_lock:
            cancel_reader = self._cancel_wakeup_reader
        if cancel_reader is not None:
            poller.register(cancel_reader, select.POLLIN | select.POLLERR | select.POLLHUP)
        poller.poll(timeout_ms)
        self._drain_cancel_wakeup()

    def _open_cancel_wakeup(self) -> None:
        reader, writer = _nonblocking_pipe()
        with self._cancel_wakeup_lock:
            self._cancel_wakeup_reader = reader
            self._cancel_wakeup_writer = writer

    def _close_cancel_wakeup(self) -> None:
        with self._cancel_wakeup_lock:
            reader = self._cancel_wakeup_reader
            writer = self._cancel_wakeup_writer
            self._cancel_wakeup_reader = None
            self._cancel_wakeup_writer = None
        for fd in (reader, writer):
            if fd is not None:
                _close_fd_quietly(fd)

    def _wake_cancel_waiters(self) -> None:
        with self._cancel_wakeup_lock:
            writer = self._cancel_wakeup_writer
        if writer is None:
            return
        try:
            os.write(writer, b"\x00")
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EBADF, errno.EPIPE):
                raise

    def _drain_cancel_wakeup(self) -> None:
        with self._cancel_wakeup_lock:
            reader = self._cancel_wakeup_reader
        if reader is None:
            return
        while True:
            try:
                data = os.read(reader, 1024)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EBADF):
                    return
                raise
            if not data:
                return


def _nonblocking_pipe() -> tuple[int, int]:
    pipe2 = getattr(os, "pipe2", None)
    if pipe2 is not None:
        return pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    reader, writer = os.pipe()
    for fd in (reader, writer):
        os.set_blocking(fd, False)
        os.set_inheritable(fd, False)
    return reader, writer


def _close_fd_quietly(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        return


class UsbFsTransport:
    def __init__(
        self,
        device: UsbDeviceInfo,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        debug: bool = False,
    ) -> None:
        self.device = device
        self.timeout_ms = timeout_ms
        self.debug = debug
        self.fd: int | None = None

    def __enter__(self) -> UsbFsTransport:
        flags = os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        self.fd = os.open(self.device.devfs_path, flags)
        try:
            claim_interface(self.fd, self.device.interface)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        if self.debug:
            print(
                f"claimed {self.device.devfs_path} interface {self.device.interface} "
                f"({self.device.product} {self.device.serial})",
                file=sys.stderr,
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is not None:
            try:
                release_interface(self.fd, self.device.interface)
            finally:
                os.close(self.fd)
                self.fd = None

    @property
    def max_packet_size(self) -> int:
        return self.device.max_packet_size or DEFAULT_MAX_USB_CHUNK

    def bulk_write(self, data: bytes) -> int:
        if self.fd is None:
            raise RuntimeError("transport is not open")
        buf = ctypes.create_string_buffer(data, len(data))
        transfer = UsbdevfsBulkTransfer(
            self.device.ep_out,
            len(data),
            self.timeout_ms,
            ctypes.cast(buf, ctypes.c_void_p),
        )
        ret = _ioctl(self.fd, USBDEVFS_BULK, ctypes.byref(transfer))
        if ret != len(data):
            raise OSError(errno.EIO, f"short USB bulk write: wrote {ret} of {len(data)} bytes")
        if self.debug:
            print(f"OUT {ret:4d}: {data[:32].hex(' ')}", file=sys.stderr)
        return ret

    def bulk_read(self, size: int | None = None, timeout_ms: int | None = None) -> bytes:
        if self.fd is None:
            raise RuntimeError("transport is not open")
        read_size = self.max_packet_size if size is None else size
        buf = ctypes.create_string_buffer(read_size)
        transfer = UsbdevfsBulkTransfer(
            self.device.ep_in,
            read_size,
            self.timeout_ms if timeout_ms is None else timeout_ms,
            ctypes.cast(buf, ctypes.c_void_p),
        )
        try:
            ret = _ioctl(self.fd, USBDEVFS_BULK, ctypes.byref(transfer))
        except OSError as exc:
            if exc.errno in (errno.ETIMEDOUT, errno.EAGAIN):
                return b""
            raise
        data = bytes(buf.raw[:ret])
        if self.debug and data:
            print(f" IN {ret:4d}: {data[:32].hex(' ')}", file=sys.stderr)
        return data

    def begin_cancellable_bulk_read(
        self,
        size: int | None = None,
        timeout_ms: int | None = None,
    ) -> UsbFsCancellableBulkRead:
        if self.fd is None:
            raise RuntimeError("transport is not open")
        read_size = self.max_packet_size if size is None else size
        return UsbFsCancellableBulkRead(
            self.fd,
            self.device.ep_in,
            read_size,
            self.timeout_ms if timeout_ms is None else timeout_ms,
            debug=self.debug,
        )
