"""Spike probe: inspect /meters response headers and try request variants.

Read-only GETs over the vendor pipe. The full ASCII dump exposes the UTOM
metadata headers (Content-Type, Allow, etc.) that may hint at the required
meter request format.
"""

from motu_proxy.datastore import MotuUsbDatastore
from motu_proxy.device import find_motu_device
from motu_proxy.parser import datastore_payload, response_status_code
from motu_proxy.protocol import MOTU_AVB_PID, MOTU_VID
from motu_proxy.transports.usbfs import UsbFsTransport

SERIAL = "0001f2fffe00c719"


def printable(b: bytes) -> str:
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def dump(ds: MotuUsbDatastore, path: str) -> None:
    try:
        resp = ds.get(path)
    except Exception as e:  # noqa: BLE001
        print(f"[GET {path}] ERROR {type(e).__name__}: {e}")
        return
    status = response_status_code(resp)
    body = datastore_payload(resp).body
    print(f"[GET {path}] resp_len={len(resp)} status={status} body_len={len(body)}")
    print(f"    ascii: {printable(resp)}")


def main() -> None:
    dev = find_motu_device(MOTU_VID, MOTU_AVB_PID, serial=SERIAL)
    with UsbFsTransport(dev, timeout_ms=800) as t:
        ds = MotuUsbDatastore(t)
        ds.init()
        dump(ds, "/meters")
        dump(ds, "/meters")  # second call: does the first one arm it?
        for q in (
            "/activityMeters",
            "/meters?meterFormat=0",
            "/meters?format=short",
            "/meters?meters=0",
            "/meters/0",
            "/meters?client=1",
        ):
            dump(ds, q)


main()
