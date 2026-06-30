import importlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import motu_proxy.transports.usbfs as usbfs
from motu_proxy.device import UsbDeviceInfo
from motu_proxy.transports.usbfs import UsbFsTransport


class UsbFsTransportTests(TestCase):
    def test_module_import_does_not_load_libc(self) -> None:
        with patch("ctypes.CDLL", side_effect=AssertionError("eager libc load")):
            importlib.reload(usbfs)

    def test_enter_closes_fd_when_claim_fails(self) -> None:
        device = UsbDeviceInfo(
            sysfs_path=Path("/sys/mock"),
            devfs_path=Path("/dev/bus/usb/003/004"),
            serial="serial",
            product="624",
            interface=3,
            ep_out=0x03,
            ep_in=0x83,
            max_packet_size=512,
        )
        transport = UsbFsTransport(device)
        with (
            patch("motu_proxy.transports.usbfs.os.open", return_value=77),
            patch("motu_proxy.transports.usbfs.claim_interface", side_effect=OSError("claim failed")),
            patch("motu_proxy.transports.usbfs.os.close") as close,
        ):
            with self.assertRaises(OSError):
                transport.__enter__()
        close.assert_called_once_with(77)
        self.assertIsNone(transport.fd)
