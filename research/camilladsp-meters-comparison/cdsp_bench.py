"""Benchmark pyCamillaDSP level-query rate, CPU, and distinct-update rate.

Connects to a running CamillaDSP WebSocket (default 127.0.0.1:1234), polls level
queries in a tight loop, and reports queries/sec, ms/query, CPU/query, the
distinct-value update rate (= chunk rate), and the estimated CPU at the chunk
rate. Also prints CamillaDSP's own processing_load (the engine cost). Read-only.
"""

import os
import time

from camilladsp import CamillaClient

c = CamillaClient("127.0.0.1", 1234)
c.connect()
try:
    cfg = c.config.active()
    sr = cfg["devices"]["samplerate"]
    cs = cfg["devices"]["chunksize"]
except Exception:
    sr, cs = 48000, 576
cr = sr / cs
print(f"state={c.general.state()} sr={sr} chunksize={cs} chunk_rate={cr:.1f}Hz "
      f"proc_load={c.status.processing_load()*100:.1f}%")


def bench(fn, label, secs=2.0):
    for _ in range(5):
        fn()
    c0 = os.times()
    w0 = time.monotonic()
    n = 0
    last = None
    ch = 0
    while time.monotonic() < w0 + secs:
        v = fn()
        n += 1
        if v != last:
            ch += 1
            last = v
    c1 = os.times()
    dt = time.monotonic() - w0
    cpu = (c1.user - c0.user) + (c1.system - c0.system)
    cpuq = cpu / n * 1000
    print(f"[{label}] q/s={n/dt:.0f} ms/q={dt/n*1000:.3f} cpu/q={cpuq:.3f}ms "
          f"cpu@max={cpu/dt*100:.1f}% updates/s={ch/dt:.0f} "
          f"est_cpu@{cr:.0f}Hz={cpuq*cr/1000*100:.2f}%")


bench(c.levels.playback_peak, "playback_peak(24ch)")
bench(c.levels.levels, "levels(all 4x24)")
bench(c.levels.playback_peak_since_last, "peak_since_last(24ch)")
print(f"final proc_load={c.status.processing_load()*100:.1f}%")
