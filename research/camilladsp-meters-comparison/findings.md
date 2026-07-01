# CamillaDSP `levels` vs MOTU `/meters` — metering comparison

**Date:** 2026-07-01
**Host:** `root@10.0.8.104` (Intel N100, Ubuntu 24.04), MOTU 624 on USB.
**Question:** can software metering from CamillaDSP (via pyCamillaDSP, localhost WebSocket) match the MOTU's ~83 Hz hardware `/meters`, and how does performance compare? See [../spike-metering/findings.md](../spike-metering/findings.md) for the MOTU `/meters` numbers.

## Setup

- CamillaDSP **4.1.3** (prebuilt linux-amd64) + pycamilladsp **4.0.0** (from GitHub, in a venv).
- Config `cdsp.yml`: 24-channel **passthrough through the 624** — Alsa `hw:CARD=D624`, `S24_3_LE`, 48000 Hz, **chunksize 576** (→ chunk rate 83.3 Hz, matched to the MOTU), empty pipeline.
- WebSocket on `127.0.0.1:1234`; pyCamillaDSP queries from the same host.
- No inputs patched → capture is silence (levels at noise floor). Query rate/CPU are signal-independent, so this does not affect the perf numbers.
- All read-only/passthrough on the ALSA audio interfaces (1/2); the vendor datastore interface (3) is untouched.

## Measured (N100, 83.3 Hz chunk rate)

Engine cost (the cost of *running* CamillaDSP): **processing_load ~4–6%** of one core, RSS 8 MB, for the 24ch passthrough.

| level query | q/s (max) | ms/query | CPU/query | updates/s | est. CPU @ 83 Hz |
|---|---|---|---|---|---|
| `playback_peak` (24ch) | 10,681 | 0.094 | 0.090 ms | 83 | 0.75% |
| `levels` (all 4×24 in one call) | 4,439 | 0.225 | 0.218 ms | 123 | 1.82% |
| `peak_since_last` (24ch) | 19,410 | 0.052 | 0.049 ms | 165 | 0.41% |

`playback_peak`'s **83 updates/s = the chunk rate** — the meaningful "distinct meter frames" number. (`levels` aggregates 4 arrays so it changes more often; `peak_since_last` resets each query, so its update count reflects query-time noise, not a frame rate.) In all cases the distinct frame rate is capped by the chunk rate; querying faster just returns repeats — the same behavior as the MOTU above its frame rate.

## Comparison with MOTU `/meters` (USB)

| | MOTU `/meters` (USB proxy) | CamillaDSP `levels` (localhost WS) |
|---|---|---|
| update rate | fixed ~83 Hz | ~83 Hz @ chunksize 576 (**configurable**) |
| max query rate | ~83/s (USB-bound) | 10,700–19,400/s |
| latency / query | ~12 ms | ~0.05–0.09 ms |
| client CPU @ 83 Hz | ~17% of a core | ~0.4–0.8% of a core (peak); ~1.8% for all-levels |
| engine cost | 0 (MOTU hardware computes for free) | ~4–6% of a core (passthrough running) |
| transport | USB bulk (shared control pipe) | localhost WS (independent, no contention) |
| peaks between polls | latest frame only | `peak_since_last` catches them |
| footprint | — | RSS 8 MB |

## Analysis — two separate costs

1. **The query** is ~20–40× cheaper on CamillaDSP (~0.5–0.8% of a core at 83 Hz vs the MOTU's ~17% over USB), with ~100× lower latency and no USB-pipe contention.
2. **The engine** must be running (~4–6% of a core for this 24ch passthrough). That cost only exists if CamillaDSP is doing your DSP anyway.

## Bottom line

- **CamillaDSP already in the signal path** → take meters from it: ~free on top (+~0.8%), lower latency, off the USB pipe, and `peak_since_last` lets you poll *slower* than 83 Hz without missing transients. Strictly better.
- **No DSP otherwise** → adding CamillaDSP solely for meters costs ~5% + a whole audio engine (latency / complexity / xrun risk) to undercut the MOTU's ~17%; the `/meters` proxy read is simpler and fine for ≤83 Hz.
- They meter **different points**: MOTU `/meters` = hardware I/O + internal mixer (authoritative for the device); CamillaDSP = the signal around its DSP. Not interchangeable.
- Rate is **configurable** on CamillaDSP (chunksize), **fixed** on the MOTU (~83 Hz).

## Reproduction

On the N100 (CamillaDSP 4.x + pycamilladsp 4.x in a venv):

```sh
# binary + pycamilladsp
curl -fsSL -o /tmp/camilladsp.tar.gz "$(curl -s https://api.github.com/repos/HEnquist/camilladsp/releases/latest | grep browser_download_url | grep linux-amd64 | cut -d'\"' -f4)"
mkdir -p /tmp/cdsp && tar xzf /tmp/camilladsp.tar.gz -C /tmp/cdsp
apt-get install -y python3.12-venv git && python3 -m venv /tmp/cdsp-venv
/tmp/cdsp-venv/bin/pip install "git+https://github.com/HEnquist/pycamilladsp.git"
# run passthrough + benchmark
/tmp/cdsp/camilladsp -p 1234 cdsp.yml &        # 24ch passthrough through the 624
/tmp/cdsp-venv/bin/python cdsp_bench.py         # levels query benchmark
```

Files here: `cdsp.yml` (config), `cdsp_bench.py` (benchmark), `cdsp_introspect.py` (levels API probe).
