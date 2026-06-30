"""Benchmark the MOTU native HTTP meters API (Ethernet path), read-only.

Compares keep-alive steady-state latency, new-connection cost, and concurrent
throughput against the USB bulk-pipe numbers (~12 ms/poll, single serialized pipe).
"""

import http.client
import os
import threading
import time

HOST = os.environ.get("MOTU", "10.0.8.98")
PORT = 80
GROUP = os.environ.get("GROUP", "ext/input")
N = int(os.environ.get("N", "300"))


def bench_keepalive():
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)

    def poll():
        conn.request("GET", f"/meters?meters={GROUP}")
        r = conn.getresponse()
        body = r.read()
        return r.status, r.getheader("ETag"), len(body)

    for _ in range(5):
        poll()
    c0 = os.times()
    w0 = time.monotonic()
    st = etag = ln = None
    for _ in range(N):
        st, etag, ln = poll()
    w1 = time.monotonic()
    c1 = os.times()
    dt = w1 - w0
    cpu = (c1.user - c0.user) + (c1.system - c0.system)
    print(f"[keep-alive] group={GROUP} N={N} status={st} bytes={ln} etag={etag}")
    print(f"  ms/poll={dt/N*1000:.2f}  polls/sec={N/dt:.0f}  cpu/poll={cpu/N*1000:.3f}ms  cpu_util@max={cpu/dt*100:.1f}%")
    conn.close()


def bench_newconn(m=100):
    w0 = time.monotonic()
    for _ in range(m):
        conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
        conn.request("GET", f"/meters?meters={GROUP}")
        conn.getresponse().read()
        conn.close()
    dt = time.monotonic() - w0
    print(f"[new-conn each] M={m}  ms/req={dt/m*1000:.2f}  req/sec={m/dt:.0f}")


def bench_concurrent(threads=4, per=150):
    results = []

    def worker():
        conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
        for _ in range(per):
            conn.request("GET", f"/meters?meters={GROUP}")
            conn.getresponse().read()
        conn.close()
        results.append(per)

    w0 = time.monotonic()
    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    dt = time.monotonic() - w0
    total = sum(results)
    print(f"[concurrent x{threads}] total={total}  agg_polls/sec={total/dt:.0f}  per-conn_ms/poll={dt/per*1000:.2f}")


bench_keepalive()
bench_newconn()
bench_concurrent()
