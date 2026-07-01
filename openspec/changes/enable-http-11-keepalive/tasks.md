## 1. Test Coverage

- [ ] 1.1 Add HTTP handler tests asserting `MotuProxyHandler.protocol_version` is `HTTP/1.1`.
- [ ] 1.2 Add or extend success-response tests to assert accurate `Content-Length` and no unconditional `Connection: close`.
- [ ] 1.3 Add or extend `304 Not Modified` tests to assert `Content-Length: 0` and an empty body.
- [ ] 1.4 Add or extend rejected-write and request-body failure tests to assert `Connection: close` is emitted when the handler intentionally closes the connection.

## 2. Implementation

- [ ] 2.1 Set the HTTP handler protocol version to `HTTP/1.1`.
- [ ] 2.2 Audit normal and error response paths to ensure every HTTP/1.1 response sends an explicit `Content-Length`.
- [ ] 2.3 Preserve conservative connection closure for write validation failures, unsupported transfer encodings, short bodies, and body read timeouts.

## 3. Verification

- [ ] 3.1 Run `.venv/bin/python -m pytest tests/test_http_server.py`.
- [ ] 3.2 Run `.venv/bin/python -m ruff check .`.
- [ ] 3.3 Run `.venv/bin/python -m pytest`.
