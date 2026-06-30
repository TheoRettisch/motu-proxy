# Spike: metering investigation — findings

**Date:** 2026-06-30
**Host:** `root@10.0.8.104` (Ubuntu 24.04), MOTU 624, USB `07fd:0005`, serial `0001f2fffe00c719`
**Scope:** read-only validation over the vendor datastore interface (interface 3, bulk IN `0x83` / OUT `0x03`). ALSA audio interfaces (0–2) untouched.

## Verdict

**CONFIRMED working over USB.** Live meter data was read from the 624 over the vendor pipe. The `/meters` resource is reachable, the request format is known, and the ETag long-poll pattern works. **Go** — the proxy-side work is scaffolded as `add-meters-passthrough`; it is a small, well-scoped protocol generalization in the proxy (below), not reverse-engineering.

## Confirmed protocol

- **Endpoint:** `/meters` — a top-level resource, NOT under `/datastore`.
- **Request:** `GET /meters` with a **query field** `meters=<group>[:<group>…]`. Over the USB vendor pipe, query params are structured query fields in the GET frame (the same slot the proxy already uses for `?client=N` via `protocol.query_fields`), **not** a literal `?…` suffix on the path. The native network form `GET /meters?meters=mix/level` maps to USB path=`/meters` + query-field `meters=mix/level`.
- **Response:** `200 OK` + JSON, keyed by meter group:
  - `meters=mix/level` → `{"mix/level/1":[…30 floats…], "mix/level/2":[…], …}` (per-mix arrays; etag 3197889, ~1160 B)
  - `meters=ext/input` → `{"ext/input":[…52 floats…]}` (flat array; etag 3197890, ~119 B)
  - `meters=mix/level:ext/input` → both groups in one object (colon-separated multi-group; ~1279 B)
- **Long-poll:** `If-None-Match: <meter-etag>` works; a re-poll returns current values + a new etag. The **meter etag is a separate, fast-advancing counter** (~3.19M, vs the datastore etag ~6095) — it advances at the device meter-frame rate (~83/s, see Performance). An `If-None-Match` request whose etag is *behind* current returns immediately, but a tight loop passing the *latest* etag is throttled by the device to ~104 ms (~10 Hz) — so for low-latency metering prefer plain no-`If-None-Match` polling (see "Meter tuning" below).
- **Capabilities:** the datastore advertises `ext/caps/meters` = `0.0.0` and `ext/caps/activityMeters` = `0.0.0`.
- All values were `0` because the idle device had no signal; the format is nonetheless proven.

Credit: the request format came from public controller code — `delbeke/motujs` (separate meter ETag, `metersWatched=['mix','level']`, builds `/meters?meters=mix/level`, applies `If-None-Match`), `hackverket/swedish-embassy-broadcasting` (same request, parses JSON float arrays + returns ETag), and `ChristopherJohnston/MOTU-AVB-Controller-App` (colon-separated groups `mix/level:ext/input`). None of this is in `motu_avb_web_api.pdf` (which has no meters section).

## Method

Baseline first (all known-good checks passed): `/datastore/uid` = `0001f2fffe00c719`, `/datastore/host/mode` = 319 bytes, `/datastore` = 223833 bytes, response-frame validator `PASS: paths=3 frames=58`. Then probes (scripts in `probes/`, copied to `/tmp/motu-proxy-live/`): USB descriptor dump, passive listen, datastore meter-key grep, raw-path GET probes, and the query-field meter probe.

## Evidence

### 1. No dedicated USB meter endpoint — only the datastore bulk pipe
`lsusb -v -d 07fd:0005`: iface 0 (Audio) interrupt IN `0x81` (ALSA-owned); iface 1/2 (Audio) isochronous audio; **iface 3 (Vendor) bulk IN `0x83` / OUT `0x03`** (the datastore pipe); iface 4/5 (Vendor) isochronous alternates. No separate bulk/interrupt meter endpoint — meters ride interface 3.

### 2. No unsolicited push
Passive listen on `0x83` for 4 s, idle and after `init`: only the 8-byte device ack. Meters are **pull**, not push.

### 3. Device advertises meter capabilities
`ext/caps/meters` and `ext/caps/activityMeters` both `{"value":"0.0.0"}`. (The `mix/chan/*/comp/peak` keys are the compressor RMS/Peak enum — config, not levels.)

### 4. `/meters` resource over USB; subscription required
Bare `GET /meters` → `204 No Content` (valid endpoint, no `meters=` subscription). `GET /datastore/meters` → no response (meters are not under `/datastore`).

### 5. Query params are USB query-fields, not a path suffix
`GET /meters` + query-field `meters=mix/level` → **200 + data**, but the literal-path control `GET /meters?meters=mix/level` (query in the path string, no query-field) → **404**. The device matches the path literally and reads `meters` from the structured query field — same mechanism as `?client=N`.

### 6. Live meter read (the confirmation)
`meters=mix/level` → 200, etag 3197889, `{"mix/level/1":[0×30],…}`. `meters=ext/input` → 200, `{"ext/input":[0×52]}`. `meters=mix/level:ext/input` → 200, combined. Re-poll with the returned etag → 200 + new etag + fresh data. (`meters=ext/output` returned no response within 2 s — group name TBD; not blocking.)

## Implementation — scaffolded as `add-meters-passthrough`

The proxy-side work is captured in `openspec/changes/add-meters-passthrough/` (strict-valid; modifies `motu-usb-datastore-proxy`). It stays in the schema-aware-proxy tier — three small, byte-faithful additions:
1. **Generalized query fields.** `protocol.query_fields` / `build_get_frame` currently hardcode the `client` field. Generalize to encode arbitrary `(name, value)` query fields so `meters=<group>` can be sent (keeping the `client` encoding byte-identical).
2. **`/meters` path handling.** `paths.normalize_path` forces a `/datastore` prefix on most paths; `/meters` must be routed as a top-level resource (like the existing `/apiversion` passthrough), with the query param sent as a USB query field (a `?`-suffixed USB path 404s).
3. **Meter ETag exposure.** The meter response carries its own fast-advancing etag; the proxy echoes it via the existing `ETag` machinery.

**Out of scope for the proxy (a separate consumer project):** the meter poll loop, any typed meter model, channel mapping, and value interpretation (meter values are device-scaled integers, not 0–1).

## Architectural implication (important)

Meter polling is effectively continuous (the meter etag changes every poll) and shares the **single** vendor bulk pipe with datastore reads/writes and the long-poll coordinator, which already serializes on one `_io_lock` and can stall foreground ops up to ~15 s behind an idle datastore long-poll (see the `add-etag-long-polling` review). A continuous meter poll on the same pipe makes that contention materially worse. **Resolve the poller's lock-hold bound before adding metering**, and design metering as a deliberately rate-limited consumer (it competes with control writes for the one pipe).

## Performance (measured on the live host, which is itself an Intel N100)

The validation host `10.0.8.104` is an **Intel N100** (4 cores @ 2.8/3.4 GHz), so these are measured on the target class, not extrapolated. Tight poll loop, current unoptimized Python proxy, idle device:

| meter group | resp bytes | ms/poll | polls/sec | CPU/poll | % of 1 core @ max rate |
|---|---|---|---|---|---|
| `ext/input` (52 floats) | 119 | 12.1 | 83 | 2.1 ms | 17% |
| `mix/level` (~30/bus) | 1160 | 14.9 | 67 | 3.1 ms | 21% |

Per poll the proxy does 3 USB reads + 2 device-acks (a GET out, the meter frame in, a host ACK, plus the datastore handshake). A 6-output-channel group is smaller than `ext/input`, so it lands at ~12 ms/poll (latency-bound). At realtime refresh: **30 Hz ≈ 6% of one core + ~36% pipe occupancy; 60 Hz ≈ 13% of one core + ~72% pipe**. The bottleneck is the single synchronous USB round-trip + ack handshake, **not** CPU — a faster CPU barely raises the ceiling. Tuning levers: request only the needed group, trim the per-poll handshake/quiet-detection reads, and async/pipeline the USB submission. Bench: `probes/meters_bench.py`.

## Transport comparison: USB bulk pipe vs native Ethernet HTTP

The 624's AVB Ethernet port serves the same datastore + meters API natively over HTTP (`http://10.0.8.98`). Measured from a LAN host (`10.0.8.200`, ~0.7 ms RTT) for comparison with the USB bulk pipe:

| | USB bulk (N100) | Ethernet HTTP (keep-alive) |
|---|---|---|
| ms/poll (`ext/input`) | 12.1 | 8.8 |
| polls/sec (1 conn) | 83 | 114 |
| `?meters=` form | query-FIELD in GET frame | native query string |
| concurrency model | single pipe + `_io_lock` | independent TCP connections |

- The native `?meters=mix/level` **query string works directly over HTTP** — the USB query-field translation is only a vendor-pipe quirk.
- Per-poll latency is similar (~8–12 ms), but that's **not** the real limit. The device generates meter frames at a fixed **~83 Hz (~12 ms/frame), device-global and not configurable** — measured: the etag-advance rate stays ~83/s whether polling at 38 or 114 polls/s (so 4 concurrent connections don't raise throughput; at 115 polls/s only 21% of responses were new). No meter-rate datastore key exists (only `ext/caps/meters`, `ext/caps/activityMeters`, `mix/meterStripOrder`). So USB at ~12 ms/poll already sits on the device ceiling — there is no transport latency to reduce.
- **Meter tuning:** poll with **no** `If-None-Match` at ~80–90 Hz. The meter `If-None-Match` long-poll throttles to ~104 ms (~10 Hz), batching ~9 internal frames per response (a low-bandwidth web-UI mode) — so for responsiveness, plain fast polling beats long-polling here (the opposite of the datastore).
- **Confirmed over USB and under real audio (2026-07-01, N100 + USB 624):** idle USB cadence held ~82–89/s across poll rates (matching HTTP). With a full-scale 1 kHz tone played to the 624 via ALSA (card 0), `mix/level` peaked ~800 and `ext/input` ~204 (idle = 0) while the etag rate stayed **~82–86/s** — so the ~83 Hz frame rate holds under real signal too. (Meter values are device-scaled integers, not 0–1.) The tone streamed on USB interfaces 1/2 while meters polled on vendor interface 3 with zero interference — a live confirmation that audio and datastore/meter control are independent. USB cadence probe: `probes/meter_cadence_usb.py`.
- **Decoupling (the real win):** with a datastore long-poll held ~10 s on one connection, 30 meter polls on a second connection ran unaffected (avg 8.8 ms, all within 0.26 s). Over Ethernet a held long-poll does **not** block meters/control — dissolving exactly the single-USB-pipe `_io_lock` contention (and the ~15 s poller stall) that plagues the USB transport.

**Implication:** if the deployment can put the 624's AVB port on a reachable network, run control + meters over HTTP directly (the proxy is a USB stand-in for that native API) and the single-pipe contention disappears. Reserve the USB proxy for USB-only deployments; a hybrid (audio over USB/ALSA, control + meters over Ethernet) is the best of both.

## Reproduction

```sh
scp -r motu_proxy tools root@10.0.8.104:/tmp/motu-proxy-live/
scp research/spike-metering/probes/*.py root@10.0.8.104:/tmp/motu-proxy-live/
# one USB claim at a time:
ssh root@10.0.8.104 'cd /tmp/motu-proxy-live && PYTHONPATH=. python3 raw_get_probe.py'        # caps + bare /meters (204)
ssh root@10.0.8.104 'cd /tmp/motu-proxy-live && PYTHONPATH=. python3 meters_query_probe.py'    # live meter read (200)
```

Probe scripts (in `probes/`): `meter_probe.py` (passive listen), `raw_get_probe.py` (caps + raw paths), `meters_detail.py` (response dump + variants), `meters_query_probe.py` (confirmed query-field meter read).
