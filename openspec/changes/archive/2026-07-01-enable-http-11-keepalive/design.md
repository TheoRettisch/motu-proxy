## Context

`MotuProxyHandler` inherits `BaseHTTPRequestHandler`, whose default `protocol_version` is `HTTP/1.0`. The proxy already sends explicit `Content-Length` headers for normal datastore responses, `304 Not Modified`, and JSON error responses, which is the key framing requirement for HTTP/1.1 persistent connections. Write-request failures already force `close_connection` in several paths to avoid reusing a connection after unsafe or partially-read request bodies.

## Goals / Non-Goals

**Goals:**
- Advertise HTTP/1.1 from the proxy handler so clients can keep TCP connections open across repeated datastore requests.
- Preserve unambiguous response framing on every status path by keeping `Content-Length` explicit.
- Keep conservative connection close behavior for rejected writes and request-body read failures.
- Add tests for protocol version, successful response framing, `304` framing, and explicit close behavior.
- Add tests for client-requested close behavior and unsupported-method/error response framing under HTTP/1.1.

**Non-Goals:**
- Do not add chunked response encoding.
- Do not change datastore request/response bodies, write authorization, token handling, origin checks, or long-poll semantics.
- Do not introduce a new HTTP server dependency.

## Decisions

- Set `MotuProxyHandler.protocol_version = "HTTP/1.1"` instead of replacing `ThreadingHTTPServer`.
  - Rationale: the current handler and server architecture are otherwise adequate, and the standard library already supports persistent connections when response framing is correct.
  - Alternative considered: leave HTTP/1.0 and rely on clients opening new sockets. That avoids behavior change but keeps avoidable latency and connection churn.

- Continue using explicit `Content-Length` rather than chunked transfer encoding.
  - Rationale: datastore responses are already fully buffered before HTTP headers are sent, and explicit lengths are easy to validate in tests.
  - Alternative considered: chunked responses. That would add complexity without improving the current buffered datastore flow.

- Force `304 Not Modified` responses to be bodyless at the handler boundary.
  - Rationale: HTTP `304` must not include a response body, so the handler should send `Content-Length: 0` and write no bytes even if an upstream dispatch result accidentally carries a payload.
  - Alternative considered: trust the datastore dispatch result to keep `304` bodies empty. That leaves the HTTP/1.1 framing contract dependent on every future producer.

- Respect client-requested connection closure.
  - Rationale: HTTP/1.1 clients can still send `Connection: close`; the proxy should not try to keep those sockets alive just because successful responses are reusable by default.
  - Alternative considered: ignore request close hints on successful responses. That is surprising for clients and makes socket-reuse tests less representative.

- Preserve explicit connection closure for write-side validation and body-read failures.
  - Rationale: after rejected writes, short bodies, unsupported transfer encodings, or read timeouts, the safest behavior is to tell HTTP/1.1 clients that the connection is not reusable.
  - Alternative considered: keep rejected write connections alive when no body has been read. That is possible for some cases, but the security-sensitive write path benefits from a simple conservative rule.

## Risks / Trade-offs

- HTTP/1.1 makes connection lifetime stateful across multiple requests -> Mitigation: audit all handler response paths, including unsupported methods and `send_error` paths, for `Content-Length` and add regression tests.
- Some clients may pipeline requests on a persistent connection -> Mitigation: rely on `BaseHTTPRequestHandler`'s sequential request handling and keep each response fully length-framed.
- Conservative write-error closure gives up reuse on some safe failures -> Mitigation: favor correctness and write-path safety over optimizing rejected requests.

## Migration Plan

No data migration is required. Deploy as a handler behavior change; rollback is reverting the protocol version to the standard-library default. Existing HTTP clients that do not reuse connections continue to receive the same datastore bodies and status codes.

## Open Questions

- None.
