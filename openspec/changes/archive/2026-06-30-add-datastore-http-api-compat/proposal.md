## Why

The MOTU AVB datastore API is documented as a JSON-over-HTTP contract (see `motu_avb_web_api.pdf`): single-key reads return `{"value": ...}`, subtree reads return nested JSON, the whole datastore carries an `ETag` header, responses set `Cache-Control: no-cache`, writes are form-encoded with a `json` field, and clients may pass a `client` identifier. Existing MOTU datastore clients — for example the long-polling JavaScript library `alexanderson1993/Motu-Control` and the control-surface bridge `ixnas/Mackie-of-the-Unicorn` — are written against that exact contract.

`motu-proxy` already bridges the same datastore over USB and returns `application/json`, but it does not echo the datastore `ETag`, does not set cache headers, and ignores the `client` query parameter. As a result the proxy is not yet a faithful drop-in for the device's native HTTP datastore API, and those clients cannot point at the proxy unchanged.

## What Changes

- Echo the datastore `ETag` returned by the device on HTTP GET responses when the USB reply carries one.
- Set `Cache-Control: no-cache` on datastore responses to match the documented API.
- Accept and forward the `client` query-string parameter on reads and writes.
- Formally specify the GET response shapes the proxy already produces: single-key `{"value": ...}`, subtree nested object, and full `/datastore` object.
- Keep the existing safety posture unchanged: localhost bind, writes disabled unless `--allow-writes`, PATCH as a POST alias.
- Add tests that lock the response headers and shapes against the documented `curl` examples.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: The localhost HTTP proxy gains native datastore API response fidelity (ETag exposure, cache headers, client identifier passthrough, documented response shapes).

## Impact

- Affected code: `motu_proxy/http_server.py`, `motu_proxy/parser.py` (extract the datastore ETag from replies), `motu_proxy/datastore.py`.
- Affected APIs: HTTP GET responses gain `Cache-Control` headers and include `ETag` when available; the `client` query parameter is now recognized. No breaking change to existing behavior.
- Affected systems: any HTTP datastore client pointed at the proxy, including the documented `curl` examples and the reference projects.
- Dependencies: standard library only; no new runtime dependency. Prerequisite for `add-etag-long-polling`.
