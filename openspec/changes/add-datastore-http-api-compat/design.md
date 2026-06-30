## Context

`motu-proxy` exposes the MOTU datastore over the USB vendor interface and serves it on localhost via `motu_proxy/http_server.py`. The device's native datastore API is documented in `motu_avb_web_api.pdf` as JSON-over-HTTP. The proxy already builds GET/POST datastore frames and returns `application/json`, so much of the contract is met by construction. The gaps are in HTTP metadata: the proxy does not surface the datastore `ETag`, does not set `Cache-Control`, and drops the `client` parameter.

The motivating consumers are existing datastore clients: `alexanderson1993/Motu-Control` (long-polling, batched writes) and `ixnas/Mackie-of-the-Unicorn` (Mackie control-surface bridge). Both target the documented HTTP contract.

## Goals / Non-Goals

**Goals:**

- Make the proxy's HTTP GET responses carry the same `ETag` and `Cache-Control: no-cache` metadata as a native MOTU device.
- Recognize the `client` query parameter on reads and writes and pass it through to the datastore operation.
- Specify and test the GET response shapes (single value, subtree, full datastore) the proxy already returns.
- Preserve the localhost-only, writes-off-by-default posture.

**Non-Goals:**

- Do not implement long-polling / `If-None-Match` hold behavior here; that is `add-etag-long-polling`, which depends on this change.
- Do not add datastore type or permission validation here; that is `add-datastore-type-permission-model`.
- Do not change the USB framing or the CLI command surface.

## Decisions

### Extract the datastore ETag from the USB reply

The HTTP `ETag` is a monotonic datastore change counter. Over USB the device returns the same datastore reply payload, so the change must locate the ETag within the reply frame and expose it as the HTTP `ETag` header. `motu_proxy/parser.py` gains a function to read the ETag alongside the JSON body.

Alternative considered: synthesize an ETag on the proxy side (for example a hash of the body). Rejected because long-polling (the dependent change) requires the device's real, monotonically increasing ETag; a synthesized value would not interoperate with `If-None-Match`.

### Keep response-shape behavior, add a contract test

The proxy already returns single-key `{"value": ...}` and nested subtree objects because it forwards the device payload and extracts the JSON object. Rather than re-implement, lock the behavior with tests derived from the documented `curl` examples.

Alternative considered: re-parse and re-serialize responses on the proxy. Rejected as unnecessary work that risks diverging from the device's exact bytes.

### Forward the client identifier

The `client` query parameter is parsed from the request URL and forwarded to the datastore read/write so that, once long-polling lands, a client's own writes can be filtered from its long-poll stream.

## Risks / Trade-offs

- The datastore ETag may not be trivially extractable from the USB reply framing. Mitigation: treat ETag location as the first task and confirm it against a live device before specifying header behavior as guaranteed; if the reply does not carry it, omit the header and record the gap.
- Header additions could surprise clients that assumed the previous bare responses. Mitigation: additive headers only; body bytes unchanged.

## Migration Plan

1. Add ETag extraction to the parser with unit tests over captured reply bytes.
2. Add header emission and `client` passthrough in the HTTP handler.
3. Validate against the documented `curl` GET examples on the live host.

## Open Questions

- Exactly where in the USB datastore reply is the ETag encoded, and is it present for single-key reads as well as subtree reads?
- Should the proxy expose the ETag for CLI `get` as well (for example a `--show-etag` flag), or is HTTP sufficient for now?
