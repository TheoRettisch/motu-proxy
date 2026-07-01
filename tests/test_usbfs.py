import ctypes
import errno
import importlib
import os
import time
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

    def test_cancellable_reap_polls_usbfs_fd_until_reapable(self) -> None:
        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        poll_timeouts: list[int | None] = []

        calls = 0

        def ioctl(_fd, request, arg):
            nonlocal calls
            if request != usbfs.USBDEVFS_REAPURBNDELAY:
                return 0
            calls += 1
            if calls == 1:
                raise OSError(errno.EAGAIN, "try again")
            ctypes.cast(arg, ctypes.POINTER(ctypes.c_void_p)).contents.value = ctypes.addressof(read._urb)
            return 0

        def poll_reap_ready(timeout_ms: int | None) -> None:
            poll_timeouts.append(timeout_ms)

        read._poll_reap_ready = poll_reap_ready
        with patch("motu_proxy.transports.usbfs._ioctl", side_effect=ioctl):
            read._reap(time.monotonic() + 10)

        self.assertEqual(len(poll_timeouts), 1)
        assert poll_timeouts[0] is not None
        self.assertGreaterEqual(poll_timeouts[0], 9000)
        self.assertLessEqual(poll_timeouts[0], 10000)
        self.assertEqual(calls, 2)

    def test_cancellable_reap_blocks_for_cancel_completion_without_idle_wait(self) -> None:
        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        read._cancel_requested = True
        read._discard_submitted = True
        blocking_reaps = 0

        class UnexpectedWaitEvent:
            def wait(self, timeout: float | None = None) -> bool:
                raise AssertionError(f"unexpected idle wait {timeout}")

            def set(self) -> None:
                return None

        def ioctl(_fd, request, _arg):
            if request == usbfs.USBDEVFS_REAPURBNDELAY:
                raise OSError(errno.EAGAIN, "try again")
            return 0

        def reap_blocking() -> None:
            nonlocal blocking_reaps
            blocking_reaps += 1

        read._cancel_event = UnexpectedWaitEvent()
        read._reap_blocking = reap_blocking
        with patch("motu_proxy.transports.usbfs._ioctl", side_effect=ioctl):
            read._reap(time.monotonic() + 10)

        self.assertEqual(blocking_reaps, 1)

    def test_cancellable_reap_skips_reap_after_disconnect_discard_error(self) -> None:
        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        read._submitted = True
        calls: list[int] = []

        def ioctl(_fd, request, _arg):
            calls.append(request)
            if request == usbfs.USBDEVFS_REAPURBNDELAY:
                raise OSError(errno.EAGAIN, "try again")
            if request == usbfs.USBDEVFS_DISCARDURB:
                raise OSError(errno.ENODEV, "device disconnected")
            if request == usbfs.USBDEVFS_REAPURB:
                raise AssertionError("blocking reap should not be used")
            return 0

        with patch("motu_proxy.transports.usbfs._ioctl", side_effect=ioctl):
            read._reap(time.monotonic() - 1)

        self.assertTrue(read._timed_out)
        self.assertTrue(read._reaped)
        self.assertEqual(
            calls,
            [usbfs.USBDEVFS_REAPURBNDELAY, usbfs.USBDEVFS_DISCARDURB],
        )

    def test_cancellable_reap_tries_nonblocking_reap_after_completed_discard_race(self) -> None:
        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        read._submitted = True
        calls: list[int] = []

        def ioctl(_fd, request, arg):
            calls.append(request)
            if request == usbfs.USBDEVFS_REAPURBNDELAY:
                if calls.count(usbfs.USBDEVFS_REAPURBNDELAY) == 1:
                    raise OSError(errno.EAGAIN, "try again")
                ctypes.cast(arg, ctypes.POINTER(ctypes.c_void_p)).contents.value = ctypes.addressof(read._urb)
                return 0
            if request == usbfs.USBDEVFS_DISCARDURB:
                raise OSError(errno.EINVAL, "invalid urb")
            if request == usbfs.USBDEVFS_REAPURB:
                raise AssertionError("blocking reap should not be used")
            return 0

        with patch("motu_proxy.transports.usbfs._ioctl", side_effect=ioctl):
            read._reap(time.monotonic() - 1)

        self.assertTrue(read._timed_out)
        self.assertTrue(read._reaped)
        self.assertEqual(
            calls,
            [
                usbfs.USBDEVFS_REAPURBNDELAY,
                usbfs.USBDEVFS_DISCARDURB,
                usbfs.USBDEVFS_REAPURBNDELAY,
            ],
        )

    def test_cancellable_reap_does_not_block_after_empty_completed_discard_race(self) -> None:
        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        read._submitted = True
        calls: list[int] = []

        def ioctl(_fd, request, _arg):
            calls.append(request)
            if request == usbfs.USBDEVFS_REAPURBNDELAY:
                raise OSError(errno.EAGAIN, "try again")
            if request == usbfs.USBDEVFS_DISCARDURB:
                raise OSError(errno.ENOENT, "no such urb")
            if request == usbfs.USBDEVFS_REAPURB:
                raise AssertionError("blocking reap should not be used")
            return 0

        with patch("motu_proxy.transports.usbfs._ioctl", side_effect=ioctl):
            read._reap(time.monotonic() - 1)

        self.assertTrue(read._timed_out)
        self.assertTrue(read._reaped)
        self.assertEqual(
            calls,
            [
                usbfs.USBDEVFS_REAPURBNDELAY,
                usbfs.USBDEVFS_DISCARDURB,
                usbfs.USBDEVFS_REAPURBNDELAY,
            ],
        )

    def test_cancellable_cancel_wakes_reap_wait(self) -> None:
        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        wakeups = 0

        class RecordingEvent:
            def wait(self, timeout: float | None = None) -> bool:
                return False

            def set(self) -> None:
                nonlocal wakeups
                wakeups += 1

        read._cancel_event = RecordingEvent()
        read.cancel()

        self.assertEqual(wakeups, 1)

    def test_cancellable_cancel_writes_poll_wakeup_pipe(self) -> None:
        read = usbfs.UsbFsCancellableBulkRead(77, 0x83, 64, None)
        read._open_cancel_wakeup()
        assert read._cancel_wakeup_reader is not None
        try:
            read.cancel()

            self.assertEqual(os.read(read._cancel_wakeup_reader, 1), b"\x00")
        finally:
            read._close_cancel_wakeup()
