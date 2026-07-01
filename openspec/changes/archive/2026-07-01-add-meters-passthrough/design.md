## Context

The spike (`research/spike-metering/findings.md`) established the full `/meters` protocol on a live 624: a top-level `/meters` resource (not under `/datastore`), requested as `GET /meters?meters=<group>[:<group>]`, returning JSON meter frames with a separate, fast-advancing meter ETag. Over the USB vendor pipe, query parameters must be sent as **query fields** in the GET frame (the slot the proxy already uses for `client`); a literal `?…` suffix on the USB path returns `404`. The device generates meter frames at a fixed ~83 Hz — confirmed over USB and under live audio, and not configurable.

`motu-proxy` already bridges the datastore and `/apiversion` over USB. Two small, proxy-level additions let it bridge `/meters` too, without leaving the schema-aware-proxy tier.

## Goals / Non-Goals

**Goals:**

- Faithfully transport the device's `/meters` resource over USB and HTTP: issue the right request (path + `meters` query field) and return the device's response and ETag unchanged.
- Generalize query-field encoding so meters — and any future GET query parameter — work without special-casing.
- Keep `/meters` routing consistent with the device (top-level, no datastore prefix).
- Preserve HTTP query ordering so the USB frame is a faithful representation of the incoming request.
- Forward meter `If-None-Match` to the device as a one-shot request rather than using datastore long-poll state.
- Stay byte-faithful: no interpretation.

**Non-Goals (these belong to the separate consumer/polling project):**

- No meter polling, watch loop, or scheduler in the proxy.
- No typed meter model, channel mapping, or value interpretation. (Meter values are device-scaled integers, not 0–1 — the consumer maps them.)
- No meter-specific long-poll coordinator. (The device throttles meter `If-None-Match` to ~10 Hz, so a consumer should poll plain — but that is the consumer's concern.)
- No attempt to raise the device meter rate (it is a fixed, non-configurable ~83 Hz).
- No new arbitrary query-parameter passthrough for writes beyond the existing `client` query behavior.

## Decisions

### Generalize query fields rather than special-case `meters`

`protocol.query_fields` currently hardcodes `client`. Generalize the helper to encode an ordered list of `(name, value)` pairs, and teach GET frame construction to use it. The existing single-`client` path remains byte-identical, including POST frames that carry `client`, but arbitrary write-query passthrough is not part of this change.

Alternative considered: add a dedicated `meters` parameter to the frame builders. Rejected — it bakes meter-specific knowledge into the transport, drifting toward the domain layer the proxy must stay out of.

### Route `/meters` as a top-level resource

Add `/meters` to the same normalization passthrough that already exempts `/apiversion`, so it is never prefixed with `/datastore`.

Alternative considered: a generic "don't prefix any unknown top-level resource" rule. Rejected as too loose; the device has a small, known set of non-datastore resources, and explicit is safer.

The proxy should accept the device-native form, `GET /meters?meters=<group>`, only. Friendlier aliases such as path-based meter group routing are out of scope for this byte-faithful pass-through change.

### Forward HTTP query parameters as USB query fields

The HTTP layer already extracts `client` from the query string; extend GET dispatch to preserve the parsed query pair order and forward query parameters to the datastore request, which encodes them as USB query fields. This applies to datastore and `/meters` GET requests. It bridges the network form `GET /meters?meters=mix/level` to the USB query-field form. The proxy never places the query string in the USB path (the device `404`s on that).

Existing `client` validation should remain in force where it exists today, including the current 32-bit unsigned integer bounds check before forwarding. For non-`client` GET query parameters, the proxy should decode and forward the field name/value without validation or interpretation.

Repeated GET query parameter names are valid and should be preserved as repeated USB query fields in parsed request order. Blank values for non-empty names should be forwarded as empty values. Empty query field names are ambiguous and should be rejected with an HTTP `400` before issuing a USB request.

### Use a one-shot meters read path, not datastore long-poll history

Meters are GET-only reads, but they must not use datastore long-poll fan-out. The current datastore HTTP path interprets `If-None-Match` as a request to wait against the background `/datastore` ETag history. Meter ETags are independent and fast-advancing, so `/meters` should instead perform exactly one serialized device read, forwarding the incoming `If-None-Match` value as the USB `If-None-Match` header when present.

The HTTP response can still reuse normal read response behavior: ETag exposure, `Cache-Control: no-cache`, content type detection, and read-only behavior. No write gating is involved.

### Single-shot CLI only

A `meters` CLI command issues exactly one `/meters?meters=<group>` read and prints the response — a convenience for validation, mirroring `get`. It deliberately has no `--watch`/loop; polling is the consumer project's job.

## Risks / Trade-offs

- **Single-pipe contention (cross-reference, not solved here):** a consumer polling meters shares the one USB bulk pipe with datastore reads/writes and the long-poll coordinator (whose ~15 s lock-hold is a known issue). The proxy only transports; this change must not introduce any meter polling. The consumer must budget its meter rate, and `avoid-long-poll-foreground-blocking` should land before recommending a high-rate meter consumer through the proxy.
- **Fixtures:** generalizing `query_fields` must keep the single-`client` encoding byte-identical so existing protocol fixtures and `add-datastore-http-api-compat` behavior do not change. Locked with a test.
- **Forward-compatibility:** the proxy forwards any `meters=` group value unmodified (it does not validate group names), so new meter groups in future firmware work without a proxy change — consistent with the datastore passthrough philosophy.

## Migration Plan

1. Generalize `query_fields` and `build_get_frame` to accept ordered `(name, value)` query parameters; keep single-`client` GET and POST frames byte-identical.
2. Add `/meters` to `paths.normalize_path` passthrough.
3. In the HTTP layer, forward parsed GET query parameters (incl. `meters`) as query fields to the device read; keep write query behavior unchanged except for existing `client`.
4. Route `/meters` reads with `If-None-Match` as one-shot device reads rather than local datastore long-polls; return body/status + meter ETag.
5. Add the single-shot `meters` CLI read.
6. Tests: query-field encoding (incl. unchanged `client` fixture), preserved `client` validation, non-`client` datastore GET query passthrough, query ordering, `/meters` routing, HTTP `?meters=` -> USB query field with USB path `/meters`, meter `If-None-Match` one-shot behavior with no coordinator wait, ETag exposure, no-poll/no-interpretation.
7. Validate on the live 624 over USB: `GET /meters?meters=mix/level` through the proxy returns the device's meter JSON and ETag.

## Follow-up Questions

- Should a follow-up change extend `info` capability discovery to surface `ext/caps/meters` / `ext/caps/activityMeters` if present?
