"""Linux sysfs discovery for MOTU AVB USB devices."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .protocol import DEFAULT_EP_IN, DEFAULT_EP_OUT, DEFAULT_INTERFACE, DEFAULT_MAX_USB_CHUNK


DEFAULT_SYSFS_ROOT = Path("/sys/bus/usb/devices")
DEFAULT_DEVFS_ROOT = Path("/dev/bus/usb")


@dataclass(frozen=True)
class UsbDeviceInfo:
    sysfs_path: Path
    devfs_path: Path
    serial: str
    product: str
    interface: int
    ep_out: int
    ep_in: int
    max_packet_size: int


@dataclass(frozen=True)
class _Endpoint:
    address: int
    max_packet_size: int


def read_text(path: Path) -> str:
    return path.read_text(encoding="ascii", errors="replace").strip()


def _read_optional_text(path: Path) -> str:
    try:
        return read_text(path)
    except FileNotFoundError:
        return ""


def _parse_hex_file(path: Path) -> int:
    return int(read_text(path), 16)


def _parse_endpoint(path: Path) -> _Endpoint | None:
    if not path.name.startswith("ep_"):
        return None
    try:
        address = int(path.name[3:], 16)
    except ValueError:
        return None
    ep_type = _read_optional_text(path / "type")
    if ep_type and ep_type.lower() != "bulk":
        return None
    try:
        max_packet_size = _parse_hex_file(path / "wMaxPacketSize")
    except (FileNotFoundError, ValueError):
        max_packet_size = DEFAULT_MAX_USB_CHUNK
    return _Endpoint(address=address, max_packet_size=max_packet_size)


def _discover_control_interface(device_path: Path, sysfs_root: Path) -> tuple[int, int, int, int] | None:
    prefix = f"{device_path.name}:1."
    candidates: list[tuple[int, int, int, int]] = []
    for interface_path in sorted(sysfs_root.iterdir()):
        if not interface_path.name.startswith(prefix):
            continue
        try:
            interface_class = _parse_hex_file(interface_path / "bInterfaceClass")
            interface_number = _parse_hex_file(interface_path / "bInterfaceNumber")
        except (FileNotFoundError, ValueError):
            continue
        if interface_class != 0xFF:
            continue
        if (interface_path / "driver").exists():
            continue

        endpoints = [ep for ep in (_parse_endpoint(path) for path in interface_path.iterdir()) if ep is not None]
        bulk_in = sorted((ep for ep in endpoints if ep.address & 0x80), key=lambda ep: ep.address)
        bulk_out = sorted((ep for ep in endpoints if not ep.address & 0x80), key=lambda ep: ep.address)
        if not bulk_in or not bulk_out:
            continue
        max_packet_size = max(bulk_in[0].max_packet_size, bulk_out[0].max_packet_size)
        candidates.append((interface_number, bulk_out[0].address, bulk_in[0].address, max_packet_size))

    if not candidates:
        return None
    return sorted(candidates)[0]


def find_motu_device(
    vid: int,
    pid: int,
    serial: str | None = None,
    sysfs_root: Path | str = DEFAULT_SYSFS_ROOT,
    devfs_root: Path | str = DEFAULT_DEVFS_ROOT,
    interface: int | None = None,
    ep_out: int | None = None,
    ep_in: int | None = None,
) -> UsbDeviceInfo:
    sysfs_root = Path(sysfs_root)
    devfs_root = Path(devfs_root)
    matches: list[UsbDeviceInfo] = []

    for path in sorted(sysfs_root.iterdir()):
        try:
            device_vid = _parse_hex_file(path / "idVendor")
            device_pid = _parse_hex_file(path / "idProduct")
        except (FileNotFoundError, ValueError):
            continue
        if device_vid != vid or device_pid != pid:
            continue

        device_serial = _read_optional_text(path / "serial")
        if serial and device_serial != serial:
            continue
        device_product = _read_optional_text(path / "product")
        bus = int(read_text(path / "busnum"))
        dev = int(read_text(path / "devnum"))

        discovered = _discover_control_interface(path, sysfs_root)
        if discovered is None:
            if interface is None or ep_out is None or ep_in is None:
                raise RuntimeError(
                    f"no unbound vendor-specific bulk control interface found for "
                    f"{device_product or f'{vid:04x}:{pid:04x}'} {device_serial or '(no serial)'}"
                )
            discovered = (DEFAULT_INTERFACE, DEFAULT_EP_OUT, DEFAULT_EP_IN, DEFAULT_MAX_USB_CHUNK)
        discovered_interface, discovered_ep_out, discovered_ep_in, max_packet_size = discovered
        matches.append(
            UsbDeviceInfo(
                sysfs_path=path,
                devfs_path=devfs_root / f"{bus:03d}" / f"{dev:03d}",
                serial=device_serial,
                product=device_product,
                interface=discovered_interface if interface is None else interface,
                ep_out=discovered_ep_out if ep_out is None else ep_out,
                ep_in=discovered_ep_in if ep_in is None else ep_in,
                max_packet_size=max_packet_size,
            )
        )

    if not matches:
        serial_text = f" serial {serial}" if serial else ""
        raise RuntimeError(f"no MOTU USB device found for {vid:04x}:{pid:04x}{serial_text}")
    if len(matches) > 1:
        serials = ", ".join(match.serial or "(no serial)" for match in matches)
        raise RuntimeError(f"multiple MOTU devices matched; choose --serial. Matches: {serials}")
    return matches[0]
