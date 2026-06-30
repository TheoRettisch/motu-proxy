"""Linux usbfs bulk transport."""

from __future__ import annotations

import ctypes
import errno
import os
import sys

from ..device import UsbDeviceInfo
from ..protocol import DEFAULT_MAX_USB_CHUNK, DEFAULT_TIMEOUT_MS


def _ioc(direction: int, type_char: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | number


_IOC_READ = 2
_IOC_WRITE = 1


class UsbdevfsBulkTransfer(ctypes.Structure):
    _fields_ = [
        ("ep", ctypes.c_uint),
        ("len", ctypes.c_uint),
        ("timeout", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    ]


USBDEVFS_BULK = _ioc(_IOC_READ | _IOC_WRITE, "U", 2, ctypes.sizeof(UsbdevfsBulkTransfer))
USBDEVFS_CLAIMINTERFACE = _ioc(_IOC_READ, "U", 15, ctypes.sizeof(ctypes.c_uint))
USBDEVFS_RELEASEINTERFACE = _ioc(_IOC_READ, "U", 16, ctypes.sizeof(ctypes.c_uint))


_LIBC = None


def _libc():
    global _LIBC
    if _LIBC is None:
        _LIBC = ctypes.CDLL(None, use_errno=True)
    return _LIBC


def _ioctl(fd: int, request: int, arg) -> int:
    ret = _libc().ioctl(fd, request, arg)
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

    def __enter__(self) -> "UsbFsTransport":
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
        transfer = UsbdevfsBulkTransfer(self.device.ep_out, len(data), self.timeout_ms, ctypes.cast(buf, ctypes.c_void_p))
        ret = _ioctl(self.fd, USBDEVFS_BULK, ctypes.byref(transfer))
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
