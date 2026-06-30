"""Characterize the device meter-frame rate over the USB vendor pipe (read-only).

Mirrors the HTTP meter_cadence.py: max-rate etag-advance + poll-interval sweep,
and reports the max abs meter value so we can tell idle (all-zero) from a live
signal. Confirms whether the ~83 Hz device frame rate holds over USB and under
real audio.
"""

import json
import os
import time

from motu_proxy.datastore import MotuUsbDatastore
from motu_proxy.device import find_motu_device
from motu_proxy.parser import datastore_payload, extract_response_etag
from motu_proxy.protocol import MOTU_AVB_PID, MOTU_VID, build_motu_frame, sized_word, u32
from motu_proxy.transports.usbfs import UsbFsTransport

SERIAL = os.environ.get("SERIAL", "0001f2fffe00c719")
GROUP = os.environ.get("GROUP", "ext/input")


def build_query_get(seq, msg, path, queries, etag="0"):
    qf = u32(len(queries))
    for n, v in queries:
        qf += sized_word(n) + sized_word(v)
    req = sized_word("GET") + sized_word(path) + u32(1) + sized_word("If-None-Match") + sized_word(etag) + qf
    payload = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(req)) + req
    return build_motu_frame(seq, "NREK", msg, payload)


def poll(ds, etag="0"):
    seq = ds._next_host_seq()
    msg = ds.message_seq
    ds._write_frame(build_query_get(seq, msg, "/meters", [("meters", GROUP)], etag=etag))
    ds.message_seq += 1
    resp = ds._collect_response(msg, timeout_ms=2000)
    et = extract_response_etag(resp)
    body = datastore_payload(resp).body
    return (int(et) if et and et.isdigit() else None), body


def max_abs(body):
    try:
        obj = json.loads(body.decode("utf-8"))
        vals = []
        for v in obj.values():
            if isinstance(v, list):
                vals += [abs(float(x)) for x in v]
        return max(vals) if vals else 0.0
    except Exception:
        return -1.0


def main():
    dev = find_motu_device(MOTU_VID, MOTU_AVB_PID, serial=SERIAL)
    with UsbFsTransport(dev, timeout_ms=2000) as t:
        ds = MotuUsbDatastore(t)
        ds.init()
        for _ in range(3):
            poll(ds)
        _, body = poll(ds)
        print(f"group={GROUP} sample_max_abs={max_abs(body):.4f}  (0.0 = idle/silence; >0 = signal present)")

        samples = []
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            e, _ = poll(ds)
            samples.append((time.monotonic(), e))
        ts = [s[0] for s in samples]
        ets = [s[1] for s in samples if s[1] is not None]
        dur = ts[-1] - ts[0]
        polls = len(samples)
        uniq = len(set(ets))
        span = (max(ets) - min(ets)) if ets else 0
        print(f"[A max-rate USB] polls={polls} dur={dur:.2f}s polls/s={polls/dur:.0f} "
              f"etag_rate={span/dur:.0f}/s unique={uniq} ({uniq/polls*100:.0f}% new)")

        print("[C sweep USB] sleep_ms -> polls/s, etag_rate/s")
        for sleep_ms in (0, 5, 10, 20):
            poll(ds)
            start = time.monotonic()
            first = last = None
            n = 0
            while time.monotonic() < start + 1.0:
                e, _ = poll(ds)
                if e is not None:
                    first = e if first is None else first
                    last = e
                n += 1
                if sleep_ms:
                    time.sleep(sleep_ms / 1000)
            d = time.monotonic() - start
            rate = (last - first) / d if (first is not None and last is not None) else 0
            print(f"   sleep={sleep_ms:2d}ms polls/s={n/d:4.0f} etag_rate={rate:4.0f}/s")


main()
