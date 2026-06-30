import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf

from motu_proxy.device import DeviceDiscoveryError, NoDeviceFound, find_motu_device


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="ascii")


def add_device(root: Path, name: str, serial: str, bus: int, dev: int, with_control: bool = True) -> None:
    device = root / name
    device.mkdir(parents=True)
    write(device / "idVendor", "07fd")
    write(device / "idProduct", "0005")
    write(device / "serial", serial)
    write(device / "product", "624")
    write(device / "busnum", str(bus))
    write(device / "devnum", str(dev))

    audio = root / f"{name}:1.0"
    audio.mkdir()
    write(audio / "bInterfaceClass", "01")
    write(audio / "bInterfaceNumber", "00")
    (audio / "driver").mkdir()

    if with_control:
        control = root / f"{name}:1.3"
        control.mkdir()
        write(control / "bInterfaceClass", "ff")
        write(control / "bInterfaceNumber", "03")
        write(control / "ep_03" / "type", "Bulk")
        write(control / "ep_03" / "wMaxPacketSize", "0200")
        write(control / "ep_83" / "type", "Bulk")
        write(control / "ep_83" / "wMaxPacketSize", "0200")


@skipIf(os.name == "nt", "fake sysfs interface names use ':' like Linux")
class DeviceDiscoveryTests(TestCase):
    def test_single_device_discovers_control_endpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_device(root, "3-3", "0001f2fffe00c719", 3, 4)
            device = find_motu_device(0x07FD, 0x0005, sysfs_root=root, devfs_root=root / "dev")
            self.assertEqual(device.serial, "0001f2fffe00c719")
            self.assertEqual(device.interface, 3)
            self.assertEqual(device.ep_out, 0x03)
            self.assertEqual(device.ep_in, 0x83)
            self.assertEqual(device.max_packet_size, 512)
            self.assertEqual(device.devfs_path, root / "dev" / "003" / "004")

    def test_serial_selects_one_device(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_device(root, "3-3", "aaa", 3, 4)
            add_device(root, "3-4", "bbb", 3, 5)
            device = find_motu_device(0x07FD, 0x0005, serial="bbb", sysfs_root=root, devfs_root=root / "dev")
            self.assertEqual(device.serial, "bbb")
            self.assertEqual(device.devfs_path, root / "dev" / "003" / "005")

    def test_multiple_devices_require_serial(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_device(root, "3-3", "aaa", 3, 4)
            add_device(root, "3-4", "bbb", 3, 5)
            with self.assertRaisesRegex(RuntimeError, "choose --serial"):
                find_motu_device(0x07FD, 0x0005, sysfs_root=root, devfs_root=root / "dev")

    def test_bound_audio_interface_is_ignored(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_device(root, "3-3", "aaa", 3, 4)
            device = find_motu_device(0x07FD, 0x0005, sysfs_root=root, devfs_root=root / "dev")
            self.assertNotEqual(device.interface, 0)

    def test_missing_control_interface_raises_without_manual_override(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_device(root, "3-3", "aaa", 3, 4, with_control=False)
            with self.assertRaisesRegex(RuntimeError, "no unbound vendor-specific bulk control interface"):
                find_motu_device(0x07FD, 0x0005, sysfs_root=root, devfs_root=root / "dev")

    def test_missing_control_interface_allows_explicit_manual_override(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_device(root, "3-3", "aaa", 3, 4, with_control=False)
            device = find_motu_device(
                0x07FD,
                0x0005,
                sysfs_root=root,
                devfs_root=root / "dev",
                interface=3,
                ep_out=0x03,
                ep_in=0x83,
            )
            self.assertEqual(device.interface, 3)
            self.assertEqual(device.ep_out, 0x03)
            self.assertEqual(device.ep_in, 0x83)

    def test_missing_sysfs_root_reports_discovery_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing"
            with self.assertRaisesRegex(DeviceDiscoveryError, "sysfs root"):
                find_motu_device(0x07FD, 0x0005, sysfs_root=root, devfs_root=root / "dev")

    def test_disappearing_candidate_is_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            device = root / "3-3"
            device.mkdir()
            write(device / "idVendor", "07fd")
            write(device / "idProduct", "0005")
            write(device / "serial", "aaa")

            with self.assertRaises(NoDeviceFound):
                find_motu_device(0x07FD, 0x0005, sysfs_root=root, devfs_root=root / "dev")
