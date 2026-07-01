## Why

The HTTP proxy currently inherits `BaseHTTPRequestHandler`'s HTTP/1.0 default, so each request closes its TCP connection even when clients would otherwise reuse it. Mixer, meter, and long-poll clients can issue frequent datastore requests, and avoiding repeated TCP handshakes is a low-level compatibility and latency improvement, especially when the proxy is accessed over a LAN.

## What Changes

- Serve datastore HTTP responses using HTTP/1.1 so clients can reuse connections when request and response framing make that safe.
- Keep explicit `Content-Length` headers on normal, `304 Not Modified`, and JSON error responses so persistent connections remain well framed.
- Continue closing write/error connections in paths where the request body or backend state may be unsafe to reuse.
- Add tests that verify the handler advertises HTTP/1.1, preserves response framing, and emits `Connection: close` where the proxy intentionally terminates a connection.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: The localhost HTTP proxy gains an HTTP/1.1 persistent-connection contract while preserving safe close behavior for rejected or unsafe requests.

## Impact

- Affected code: `motu_proxy/http_server.py` and related HTTP handler tests.
- Affected APIs: HTTP response protocol version changes from HTTP/1.0 to HTTP/1.1. Existing response bodies, status codes, datastore paths, write gating, and token behavior remain unchanged.
- Affected systems: browser tabs, mixer/meter clients, scripts, and remote LAN clients that can reuse proxy TCP connections.
- Dependencies: standard library only; no new runtime dependency.
