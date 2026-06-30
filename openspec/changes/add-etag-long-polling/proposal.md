## Why

Long-polling is the mechanism the MOTU datastore API provides for low-latency change notification (see `motu_avb_web_api.pdf`, "ETags and Long Polling"): a client sends `If-None-Match: <etag>`; if the datastore has changed the device responds immediately with the changes since that ETag; if not, the device holds the request for up to 15 seconds and then returns `304 Not Modified`. A `client` identifier lets a client filter out its own changes. This is the backbone of live state in real datastore clients — `alexanderson1993/Motu-Control` is built entirely around it, and any bidirectional control surface such as `ixnas/Mackie-of-the-Unicorn` needs it to stay in sync.

`motu-proxy` already sends an `If-None-Match` header inside every USB GET frame (`build_get_frame` accepts an `etag` argument), but the feature is unrealized end to end: the response ETag is never parsed, the HTTP layer never reads a client `If-None-Match`, and the USB read loop gives up after roughly 600 ms — far short of the device's 15-second hold. Without long-polling, clients must brute-force poll and cannot receive timely updates.

## What Changes

- Parse the datastore ETag from USB replies (provided by `add-datastore-http-api-compat`).
- Add a datastore long-poll read that forwards a client ETag and waits long enough for the device's held response (greater than the 15-second device hold).
- Map HTTP `If-None-Match` and `client` to the datastore long-poll, returning `304 Not Modified` on a no-change timeout and the changed payload with the new ETag otherwise.
- Decide and implement how a single USB control pipe serves held requests without starving other requests (see design).
- Add tests for long-poll request construction, timeout-to-304 mapping, and change-to-response mapping using fake transports.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: The proxy gains datastore long-polling / change-subscription support over USB and HTTP.

## Impact

- Affected code: `motu_proxy/datastore.py`, `motu_proxy/parser.py`, `motu_proxy/protocol.py` (read timeout), `motu_proxy/http_server.py`.
- Affected APIs: HTTP GET honors `If-None-Match` and may hold the connection and return `304`.
- Affected systems: live-state datastore clients and control surfaces pointed at the proxy.
- Dependencies: standard library only. Depends on `add-datastore-http-api-compat`.
