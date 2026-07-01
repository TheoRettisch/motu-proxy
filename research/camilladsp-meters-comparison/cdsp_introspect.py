"""Probe the pyCamillaDSP 4.x levels/status API against a running CamillaDSP.

Connects to 127.0.0.1:1234, prints the available level/status methods, and
samples a few level reads so the benchmark calls the right methods. Read-only.
"""

from camilladsp import CamillaClient

c = CamillaClient("127.0.0.1", 1234)
c.connect()
print("state:", c.general.state())
print("levels methods:", [m for m in dir(c.levels) if not m.startswith("_")])
print("status methods:", [m for m in dir(c.status) if not m.startswith("_")])
for name in ["playback_peak", "capture_peak", "playback_rms", "capture_rms"]:
    f = getattr(c.levels, name, None)
    if f:
        try:
            v = f()
            print(name, "-> len", len(v) if hasattr(v, "__len__") else v,
                  "sample", (v[:3] if hasattr(v, "__getitem__") else v))
        except Exception as e:
            print(name, "ERR", type(e).__name__, e)
for name in ["processing_load", "buffer_level", "clipped_samples"]:
    f = getattr(c.status, name, None)
    if f:
        try:
            print("status." + name, "->", f())
        except Exception as e:
            print("status." + name, "ERR", e)
