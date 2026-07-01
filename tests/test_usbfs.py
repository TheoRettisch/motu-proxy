import ctypes
import errno
import importlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import motu_proxy.transports.usbfs as usbfs
from motu_proxy.device import UsbDeviceInfo
from motu_proxy.transports.usbfs import UsbFsTransport


def device_info() -> UsbDeviceInfo:
    return UsbDeviceInfo(
        sysfs_path=Path("/sys/mock"),
        devfs_path=Path("/dev/bus/usb/003/004"),
        serial="serial",
        product="624",
        interface=3,
        ep_out=0x03,
        ep_in=0x83,
        max_packet_size=512,
    )


class UsbFsTransportTests(TestCase):
    def test_module_import_does_not_load_libc(self) -> None:
        with patch("ctypes.CDLL", side_effect=AssertionError("eager libc load")):
            importlib.reload(usbfs)

    def test_libc_ioctl_signature_is_pinned(self) -> None:
        class FakeIoctl:
            argtypes = None
            restype = None

            def __call__(self, fd, request, arg):
                return 0

        class FakeLibc:
            ioctl = FakeIoctl()

        fake_libc = FakeLibc()
        with patch("ctypes.CDLL", return_value=fake_libc):
            usbfs._LIBC = None
            self.assertIs(usbfs._libc(), fake_libc)

        self.assertEqual(fake_libc.ioctl.argtypes, (ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p))
        self.assertIs(fake_libc.ioctl.restype, ctypes.c_int)

    def test_ioctl_casts_request_to_unsigned_long(self) -> None:
        calls = []

        class FakeIoctl:
            argtypes = None
            restype = None

            def __call__(self, fd, request, arg):
                calls.append((fd, request, arg))
                return 0

        class FakeLibc:
            ioctl = FakeIoctl()

        with patch("ctypes.CDLL", return_value=FakeLibc()):
            usbfs._LIBC = None
            arg = ctypes.c_uint(3)
            usbfs._ioctl(77, usbfs.USBDEVFS_CLAIMINTERFACE, ctypes.byref(arg))

        self.assertEqual(calls[0][0], 77)
        self.assertIsInstance(calls[0][1], ctypes.c_ulong)

    def test_enter_closes_fd_when_claim_fails(self) -> None:
        transport = UsbFsTransport(device_info())
        with (
            patch("motu_proxy.transports.usbfs.os.open", return_value=77),
            patch("motu_proxy.transports.usbfs.claim_interface", side_effect=OSError("claim failed")),
            patch("motu_proxy.transports.usbfs.os.close") as close,
            self.assertRaises(OSError),
        ):
            transport.__enter__()
        close.assert_called_once_with(77)
        self.assertIsNone(transport.fd)

    def test_bulk_write_rejects_short_write(self) -> None:
        transport = UsbFsTransport(device_info())
        transport.fd = 77
        with (
            patch("motu_proxy.transports.usbfs._ioctl", return_value=3),
            self.assertRaisesRegex(OSError, "short USB bulk write"),
        ):
            transport.bulk_write(b"1234")

    def test_cancellable_read_surfaces_device_disconnect(self) -> None:
        def no_submit() -> None:
            return None

        def no_reap(_deadline) -> None:
            return None

        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        read._submit = no_submit
        read._reap = no_reap
        read._urb.status = -errno.ENODEV

        with self.assertRaises(OSError) as raised:
            read.read()
        self.assertEqual(raised.exception.errno, errno.ENODEV)
