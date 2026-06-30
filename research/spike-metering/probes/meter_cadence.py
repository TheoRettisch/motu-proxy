"""Characterize the device's meter-frame generation rate (read-only).

A) max-rate poll (no If-None-Match): is every response a NEW etag, and what's the
   etag-advance rate (= device frames/sec)?
B) If-None-Match long-poll: inter-frame interval (device frame period) + whether
   we catch every frame (etag delta == 1).
C) poll-interval sweep: does the etag rate plateau regardless of how fast we poll?
   The plateau = the hard device meter-frame rate.
"""

import http.client
import os
import statistics
import time

HOST = os.environ.get("MOTU", "10.0.8.98")
GROUP = os.environ.get("GROUP", "ext/input")


def conn():
    return http.client.HTTPConnection(HOST, 80, timeout=20)


def get(c, etag=None):
    h = {"If-None-Match": etag} if etag else {}
    t0 = time.monotonic()
    c.request("GET", f"/meters?meters={GROUP}", headers=h)
    r = c.getresponse()
    r.read()
    et = r.getheader("ETag")
    return time.monotonic() - t0, r.status, (int(et) if et and et.isdigit() else None)


# A: max-rate, no INM, 2 s
c = conn()
for _ in range(3):
    get(c)
samples = []
end = time.monotonic() + 2.0
while time.monotonic() < end:
    dt, st, et = get(c)
    samples.append((time.monotonic(), et))
c.close()
ts = [s[0] for s in samples]
ets = [s[1] for s in samples if s[1] is not None]
dur = ts[-1] - ts[0]
polls = len(samples)
uniq = len(set(ets))
span = (max(ets) - min(ets)) if ets else 0
print(f"[A max-rate] polls={polls} dur={dur:.2f}s polls/s={polls/dur:.0f}")
print(f"   etag_span={span} -> etag_rate={span/dur:.0f}/s  unique_etags={uniq} ({uniq/polls*100:.0f}% of polls new)")

# B: INM long-poll cadence, 50 frames
c = conn()
_, _, last = get(c)
intervals, deltas = [], []
for _ in range(50):
    dt, st, et = get(c, etag=str(last))
    intervals.append(dt * 1000)
    if et is not None:
        deltas.append(et - last)
        last = et
c.close()
intervals.sort()
print(f"[B INM long-poll] n={len(intervals)} interval_ms avg={statistics.mean(intervals):.2f} "
      f"p50={intervals[len(intervals)//2]:.2f} min={min(intervals):.2f} max={max(intervals):.2f}")
print(f"   etag_delta/response avg={statistics.mean(deltas):.2f} (1.0 = we catch every frame)")

# C: poll-interval sweep -> etag-rate plateau
print("[C sweep] sleep_ms -> polls/s, etag_rate/s")
for sleep_ms in (0, 2, 5, 10, 20):
    c = conn()
    get(c)
    start = time.monotonic()
    first = last_e = None
    n = 0
    while time.monotonic() < start + 1.0:
        _, _, et = get(c)
        if et is not None:
            first = et if first is None else first
            last_e = et
        n += 1
        if sleep_ms:
            time.sleep(sleep_ms / 1000)
    c.close()
    d = time.monotonic() - start
    rate = (last_e - first) / d if (first is not None and last_e is not None) else 0
    print(f"   sleep={sleep_ms:2d}ms  polls/s={n/d:4.0f}  etag_rate={rate:4.0f}/s")
