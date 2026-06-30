"""Spike: benchmark meter-poll rate and CPU cost over the USB vendor pipe.

Read-only. Polls /meters?meters=<GROUP> in a tight loop and reports wall-clock
ms/poll, polls/sec, and CPU (user+sys) per poll, plus per-response frame stats.
"""

import os
import time

from motu_proxy.datastore import MotuUsbDatastore
from motu_proxy.device import find_motu_device
from motu_proxy.parser import datastore_payload, response_status_code
from motu_proxy.protocol import MOTU_AVB_PID, MOTU_VID, build_motu_frame, sized_word, u32
from motu_proxy.transports.usbfs import UsbFsTransport

SERIAL = "0001f2fffe00c719"
GROUP = os.environ.get("GROUP", "ext/input")
N = int(os.environ.get("N", "300"))


def build_query_get(seq, message_seq, path, queries, etag="0"):
    qf = u32(len(queries))
    for name, value in queries:
        qf += sized_word(name) + sized_word(value)
    request = (sized_word("GET") + sized_word(path) + u32(1)
               + sized_word("If-None-Match") + sized_word(etag) + qf)
    payload = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(request)) + request
    return build_motu_frame(seq, "NREK", message_seq, payload)


def poll(ds, group):
    seq = ds._next_host_seq()
    msg = ds.message_seq
    ds._write_frame(build_query_get(seq, msg, "/meters", [("meters", group)]))
    ds.message_seq += 1
    return ds._collect_response(msg, timeout_ms=2000)


def main():
    dev = find_motu_device(MOTU_VID, MOTU_AVB_PID, serial=SERIAL)
    with UsbFsTransport(dev, timeout_ms=2000) as t:
        ds = MotuUsbDatastore(t)
        ds.init()
        for _ in range(5):
            poll(ds, GROUP)  # warmup
        c0 = os.times()
        w0 = time.monotonic()
        last = None
        for _ in range(N):
            last = poll(ds, GROUP)
        w1 = time.monotonic()
        c1 = os.times()
        dt = w1 - w0
        cpu = (c1.user - c0.user) + (c1.system - c0.system)
        body = datastore_payload(last).body
        st = ds.last_response_stats
        print(f"group={GROUP} N={N} status={response_status_code(last)} resp_body_bytes={len(body)}")
        print(f"  wall={dt*1000:.0f}ms  ms/poll={dt/N*1000:.2f}  polls/sec={N/dt:.0f}")
        print(f"  cpu(user+sys)={cpu*1000:.0f}ms  cpu/poll={cpu/N*1000:.3f}ms  cpu_util@maxrate={cpu/dt*100:.1f}%")
        if st:
            print(f"  per-poll: usb_reads={st.reads} frames={st.accepted_frames} acks={st.ack_packets} bytes={st.response_bytes}")


main()
