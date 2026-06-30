"""Spike probe: does the MOTU vendor bulk pipe carry meter data (push or poll)?

Read-only. Claims vendor interface 3, optionally sends the normal init handshake
or a datastore GET, then passively listens on bulk IN 0x83 and classifies frames.
"""

import sys
import time

from motu_proxy.device import find_motu_device
from motu_proxy.parser import is_device_ack
from motu_proxy.protocol import (
    MOTU_AVB_PID,
    MOTU_VID,
    HostSequencer,
    build_get_frame,
    build_init,
)
from motu_proxy.transports.usbfs import UsbFsTransport

SERIAL = "0001f2fffe00c719"


def classify(pkt: bytes) -> str:
    if is_device_ack(pkt):
        return "ack"
    body = pkt[4:] if len(pkt) >= 4 else pkt
    if body[:4] in (b"NREK", b"PTTH"):
        return "datastore"
    return "OTHER"


def listen(transport, seconds: float):
    end = time.monotonic() + seconds
    counts: dict[str, int] = {}
    samples: list[tuple[int, str]] = []
    per_sec: dict[int, int] = {}
    start = time.monotonic()
    total = 0
    byte_total = 0
    while time.monotonic() < end:
        pkt = transport.bulk_read(512, timeout_ms=200)
        if not pkt:
            continue
        total += 1
        byte_total += len(pkt)
        bucket = int(time.monotonic() - start)
        per_sec[bucket] = per_sec.get(bucket, 0) + 1
        c = classify(pkt)
        counts[c] = counts.get(c, 0) + 1
        if c == "OTHER" and len(samples) < 12:
            samples.append((len(pkt), pkt[:64].hex(" ")))
    return total, byte_total, counts, samples, per_sec


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "noinit"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    path = sys.argv[3] if len(sys.argv) > 3 else "/datastore"
    dev = find_motu_device(MOTU_VID, MOTU_AVB_PID, serial=SERIAL)
    print(f"device={dev.product} serial={dev.serial} iface={dev.interface} "
          f"in=0x{dev.ep_in:02x} out=0x{dev.ep_out:02x} mode={mode} listen={secs}s")
    with UsbFsTransport(dev, timeout_ms=600) as t:
        seq = HostSequencer()
        if mode in ("init", "getthenlisten"):
            t.bulk_write(build_init(seq.take()))
            print("sent init")
        if mode == "getthenlisten":
            # Send a GET and drain its response, then keep listening for any
            # unsolicited follow-up frames (e.g. meter pushes).
            t.bulk_write(build_get_frame(seq.take(), 2, path))
            print(f"sent GET {path}")
        total, byte_total, counts, samples, per_sec = listen(t, secs)
        print(f"RESULT frames={total} bytes={byte_total} counts={counts}")
        print(f"per_second={dict(sorted(per_sec.items()))}")
        for ln, hx in samples:
            print(f"  OTHER len={ln}: {hx}")


main()
