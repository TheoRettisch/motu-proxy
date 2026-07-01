"""Linux usbfs bulk transport."""

from __future__ import annotations

import ctypes
import errno
import os
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
DEFAULT_CANCELLABLE_REAP_POLL_INTERVAL_S = 0.05


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
        reap_poll_interval_s: float = DEFAULT_CANCELLABLE_REAP_POLL_INTERVAL_S,
    ) -> None:
        self.fd = fd
        self.endpoint = endpoint
        self.read_size = read_size
        self.timeout_ms = timeout_ms
        self.debug = debug
        self.reap_poll_interval_s = max(0.001, reap_poll_interval_s)
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
        self._timed_out = False
        self._cancel_event = threading.Event()

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
        self._cancel_event.set()
        if not self._submitted or self._reaped:
            return
        try:
            _ioctl(self.fd, USBDEVFS_DISCARDURB, ctypes.byref(self._urb))
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENODEV, errno.ENOENT):
                raise

    def _submit(self) -> None:
        if self._submitted:
            return
        _ioctl(self.fd, USBDEVFS_SUBMITURB, ctypes.byref(self._urb))
        self._submitted = True

    def _reap(self, deadline: float | None) -> None:
        while True:
            reaped = ctypes.c_void_p()
            try:
                _ioctl(self.fd, USBDEVFS_REAPURBNDELAY, ctypes.byref(reaped))
            except OSError as exc:
                if exc.errno != errno.EAGAIN:
                    raise
                if self._cancel_requested:
                    self._reap_blocking()
                    return
                if deadline is not None and time.monotonic() >= deadline:
                    self._timed_out = True
                    self.cancel()
                    self._reap_blocking()
                    return
                wait_s = self.reap_poll_interval_s
                if deadline is not None:
                    wait_s = min(wait_s, max(0.0, deadline - time.monotonic()))
                self._cancel_event.wait(wait_s)
                continue
            self._accept_reaped_urb(reaped)
            return

    def _reap_blocking(self) -> None:
        reaped = ctypes.c_void_p()
        _ioctl(self.fd, USBDEVFS_REAPURB, ctypes.byref(reaped))
        self._accept_reaped_urb(reaped)

    def _accept_reaped_urb(self, reaped: ctypes.c_void_p) -> None:
        if reaped.value != ctypes.addressof(self._urb):
            raise OSError(errno.EIO, "reaped unexpected USB URB")
        self._reaped = True


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
