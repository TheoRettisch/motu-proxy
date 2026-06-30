## Context

The spike (`research/spike-metering/findings.md`) established the full `/meters` protocol on a live 624: a top-level `/meters` resource (not under `/datastore`), requested as `GET /meters?meters=<group>[:<group>]`, returning JSON meter frames with a separate, fast-advancing meter ETag. Over the USB vendor pipe, query parameters must be sent as **query fields** in the GET frame (the slot the proxy already uses for `client`); a literal `?…` suffix on the USB path returns `404`. The device generates meter frames at a fixed ~83 Hz — confirmed over USB and under live audio, and not configurable.

`motu-proxy` already bridges the datastore and `/apiversion` over USB. Two small, proxy-level additions let it bridge `/meters` too, without leaving the schema-aware-proxy tier.

## Goals / Non-Goals

**Goals:**

- Faithfully transport the device's `/meters` resource over USB and HTTP: issue the right request (path + `meters` query field) and return the device's response and ETag unchanged.
- Generalize query-field encoding so meters — and any future query parameter — work without special-casing.
- Keep `/meters` routing consistent with the device (top-level, no datastore prefix).
- Stay byte-faithful: no interpretation.

**Non-Goals (these belong to the separate consumer/polling project):**

- No meter polling, watch loop, or scheduler in the proxy.
- No typed meter model, channel mapping, or value interpretation. (Meter values are device-scaled integers, not 0–1 — the consumer maps them.)
- No meter-specific long-poll coordinator. (The device throttles meter `If-None-Match` to ~10 Hz, so a consumer should poll plain — but that is the consumer's concern.)
- No attempt to raise the device meter rate (it is a fixed, non-configurable ~83 Hz).

## Decisions

### Generalize query fields rather than special-case `meters`

`protocol.query_fields` currently hardcodes `client`. Generalize it to encode an ordered list of `(name, value)` pairs; `client` becomes one caller of the same path. The proxy stays parameter-agnostic — it forwards whatever query parameters arrive, just as it forwards arbitrary datastore paths.

Alternative considered: add a dedicated `meters` parameter to the frame builders. Rejected — it bakes meter-specific knowledge into the transport, drifting toward the domain layer the proxy must stay out of.

### Route `/meters` as a top-level resource

Add `/meters` to the same normalization passthrough that already exempts `/apiversion`, so it is never prefixed with `/datastore`.

Alternative considered: a generic "don't prefix any unknown top-level resource" rule. Rejected as too loose; the device has a small, known set of non-datastore resources, and explicit is safer.

### Forward HTTP query parameters as USB query fields

The HTTP layer already extracts `client` from the query string; extend it to forward query parameters generally to the datastore request, which encodes them as USB query fields. This bridges the network form `GET /meters?meters=mix/level` to the USB query-field form. The proxy never places the query string in the USB path (the device `404`s on that).

### Reuse the existing read path (no new HTTP semantics)

Meters are GET-only reads, so they ride the existing HTTP GET path: ETag exposure, `Cache-Control: no-cache`, CORS, and read-only behavior all come from `add-datastore-http-api-compat`. No write gating is involved.

### Single-shot CLI only

A `meters` CLI command issues exactly one `/meters?meters=<group>` read and prints the response — a convenience for validation, mirroring `get`. It deliberately has no `--watch`/loop; polling is the consumer project's job.

## Risks / Trade-offs

- **Single-pipe contention (cross-reference, not solved here):** a consumer polling meters shares the one USB bulk pipe with datastore reads/writes and the long-poll coordinator (whose ~15 s lock-hold is a known issue). The proxy only transports; the consumer must budget its meter rate, and the coordinator lock-hold should be fixed independently.
- **Fixtures:** generalizing `query_fields` must keep the single-`client` encoding byte-identical so existing protocol fixtures and `add-datastore-http-api-compat` behavior do not change. Locked with a test.
- **Forward-compatibility:** the proxy forwards any `meters=` group value unmodified (it does not validate group names), so new meter groups in future firmware work without a proxy change — consistent with the datastore passthrough philosophy.

## Migration Plan

1. Generalize `query_fields` / `build_get_frame` / `build_post_frame` to accept ordered `(name, value)` query parameters; keep `client` byte-identical.
2. Add `/meters` to `paths.normalize_path` passthrough.
3. In the HTTP layer, forward parsed query parameters (incl. `meters`) as query fields to the datastore GET; return body + meter ETag.
4. Add the single-shot `meters` CLI read.
5. Tests: query-field encoding (incl. unchanged `client` fixture), `/meters` routing, HTTP `?meters=` → USB query field, ETag exposure, no-poll/no-interpretation.
6. Validate on the live 624 over USB: `GET /meters?meters=mix/level` through the proxy returns the device's meter JSON and ETag.

## Open Questions

- Should capability discovery (`add-capability-discovery`) surface `ext/caps/meters` / `ext/caps/activityMeters` so a consumer can detect meter support before requesting it? Natural tie-in; better folded into that change than here.
- Should the proxy accept a meter group via a cleaner path form for ergonomics, or only the device-native `?meters=` query? Lean: device-native only, to stay a faithful bridge.
