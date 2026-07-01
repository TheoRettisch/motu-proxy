## 1. Generalized Query Fields

- [ ] 1.1 Generalize `protocol.query_fields` and `build_get_frame` to encode an ordered list of `(name, value)` query parameters.
- [ ] 1.2 Keep the single-`client` GET and POST encodings byte-identical (existing fixtures unchanged); add tests asserting both.
- [ ] 1.3 Test multi-field GET encoding (`meters` + `client` together) preserves request order.
- [ ] 1.4 Preserve repeated non-empty GET query field names and blank values in parsed order; reject empty query field names with HTTP `400` before issuing a USB request.

## 2. Meters Resource Routing

- [ ] 2.1 Route `/meters` as a top-level resource in `paths.normalize_path` (no `/datastore` prefix), like `/apiversion`.
- [ ] 2.2 Test that `/meters` and datastore-path normalization do not affect each other.

## 3. HTTP And Datastore Bridging

- [ ] 3.1 Forward HTTP GET query parameters for datastore and `/meters` requests (e.g. `?meters=mix/level`) as USB query fields through the datastore GET path; include an unknown non-`client` datastore query passthrough test.
- [ ] 3.2 Return the device meters response unchanged and expose the meter ETag via the existing `ETag` header.
- [ ] 3.3 Forward unrecognized meter group values unmodified (forward-compatible; no validation or interpretation).
- [ ] 3.4 Route `/meters` requests with `If-None-Match` as one-shot device reads that forward the ETag, not as datastore long-poll waits; assert one read to `/meters`, the forwarded ETag argument, and no coordinator wait call.
- [ ] 3.5 Keep write query behavior unchanged except for existing `client` passthrough.
- [ ] 3.6 Preserve existing HTTP GET `client` 32-bit unsigned integer validation before forwarding as a USB query field.
- [ ] 3.7 Forward device meter no-change responses (for meter `If-None-Match`) unchanged, including status/ETag/body semantics, without consulting datastore long-poll state or synthesizing meter data.

## 4. CLI (single-shot)

- [ ] 4.1 Add a read-only `meters` CLI command that issues one `/meters?meters=<group>` request and prints the response (no loop/watch).

## 5. Scope Guards And Validation

- [ ] 5.1 Assert the proxy issues exactly one device request per meters request (no background poll loop).
- [ ] 5.2 Assert meter response bodies/values are returned unchanged (no interpretation or mapping).
- [ ] 5.3 Assert meter `If-None-Match` is forwarded to the device and does not trigger datastore long-poll history.
- [ ] 5.4 Assert `GET /meters?meters=mix/level` uses USB path `/meters` and encodes `meters=mix/level` only as a USB query field.
- [ ] 5.5 Validate on the live 624 over USB: `GET /meters?meters=mix/level` via the proxy returns the device meter JSON + ETag.
- [ ] 5.6 Document that high-rate meter consumers should wait for foreground-safe long-poll coordination / `avoid-long-poll-foreground-blocking`.
