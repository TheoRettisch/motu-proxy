"""Spike probe: read meter capability values and try a raw /meters request.

Read-only. Sends raw (un-normalized) GET paths over the vendor pipe so we can
reach non-/datastore resources like /meters that the native HTTP API exposes.
"""

import sys

from motu_proxy.datastore import MotuUsbDatastore
from motu_proxy.device import find_motu_device
from motu_proxy.parser import datastore_payload, extract_response_etag, response_status_code
from motu_proxy.protocol import MOTU_AVB_PID, MOTU_VID
from motu_proxy.transports.usbfs import UsbFsTransport

SERIAL = "0001f2fffe00c719"

DEFAULT_PATHS = [
    "/apiversion",
    "/datastore/ext/caps/meters",
    "/datastore/ext/caps/activityMeters",
    "/meters",
    "/datastore/meters",
]


def main() -> None:
    paths = sys.argv[1:] or DEFAULT_PATHS
    dev = find_motu_device(MOTU_VID, MOTU_AVB_PID, serial=SERIAL)
    with UsbFsTransport(dev, timeout_ms=800) as t:
        ds = MotuUsbDatastore(t)
        ds.init()
        for p in paths:
            try:
                resp = ds.get(p)
                status = response_status_code(resp)
                etag = extract_response_etag(resp)
                body = datastore_payload(resp).body
                try:
                    preview_txt = body[:96].decode("utf-8")
                except UnicodeDecodeError:
                    preview_txt = "(non-utf8 / binary)"
                print(f"{p}: resp_len={len(resp)} status={status} etag={etag} body_len={len(body)}")
                print(f"    body.hex[0:48]={body[:48].hex(' ')}")
                print(f"    body.txt[0:96]={preview_txt!r}")
            except Exception as e:  # noqa: BLE001 - probe wants to keep going
                print(f"{p}: ERROR {type(e).__name__}: {e}")


main()
