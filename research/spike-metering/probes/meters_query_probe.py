"""Spike probe v2: request meters via the USB query-field mechanism.

Public controller code (delbeke/motujs, hackverket/swedish-embassy-broadcasting,
ChristopherJohnston/MOTU-AVB-Controller-App) shows the network request is
    GET /meters?meters=mix/level   [+ If-None-Match for long-poll]
Over the USB vendor pipe, query params travel as structured query fields in the
GET frame (the same slot the proxy uses for ?client=N), NOT as a literal '?...'
suffix on the path (which 404s). This probe sends the query-field form, plus the
literal-path form as a control, then re-polls with any returned ETag.

Read-only (GET only).
"""

from motu_proxy.datastore import MotuUsbDatastore
from motu_proxy.device import find_motu_device
from motu_proxy.parser import datastore_payload, extract_response_etag, response_status_code
from motu_proxy.protocol import MOTU_AVB_PID, MOTU_VID, build_motu_frame, sized_word, u32
from motu_proxy.transports.usbfs import UsbFsTransport

SERIAL = "0001f2fffe00c719"


def build_query_get(seq, message_seq, path, queries, etag="0", header="NREK"):
    qf = u32(len(queries))
    for name, value in queries:
        qf += sized_word(name) + sized_word(value)
    request = (
        sized_word("GET")
        + sized_word(path)
        + u32(1)
        + sized_word("If-None-Match")
        + sized_word(etag)
        + qf
    )
    motu_payload = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(request)) + request
    return build_motu_frame(seq, header, message_seq, motu_payload)


def do(ds, path, queries, etag="0", timeout_ms=2000, label=""):
    seq = ds._next_host_seq()
    msg = ds.message_seq
    frame = build_query_get(seq, msg, path, queries, etag=etag)
    ds._write_frame(frame)
    ds.message_seq += 1
    try:
        resp = ds._collect_response(msg, timeout_ms=timeout_ms)
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] path={path!r} q={queries} etag_in={etag} -> ERROR {type(e).__name__}: {e}")
        return None
    status = response_status_code(resp)
    et = extract_response_etag(resp)
    body = datastore_payload(resp).body
    try:
        txt = body.decode("utf-8")
    except UnicodeDecodeError:
        txt = "(binary) " + body[:80].hex(" ")
    print(f"[{label}] path={path!r} q={queries} etag_in={etag} -> status={status} etag_out={et} body_len={len(body)}")
    print(f"    body[:300]: {txt[:300]}")
    return et


def main():
    dev = find_motu_device(MOTU_VID, MOTU_AVB_PID, serial=SERIAL)
    with UsbFsTransport(dev, timeout_ms=2500) as t:
        ds = MotuUsbDatastore(t)
        ds.init()
        et = do(ds, "/meters", [("meters", "mix/level")], label="qfield mix/level")
        do(ds, "/meters?meters=mix/level", [], label="literal-path control")
        do(ds, "/meters", [("meters", "ext/input")], label="qfield ext/input")
        do(ds, "/meters", [("meters", "ext/output")], label="qfield ext/output")
        do(ds, "/meters", [("meters", "mix/level:ext/input")], label="qfield multi-group")
        if et:
            do(ds, "/meters", [("meters", "mix/level")], etag=et, timeout_ms=3000, label="repoll-with-etag")


main()
