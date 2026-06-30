## 1. Reply ETag Plumbing

- [ ] 1.1 Consume the reply ETag parser from `add-datastore-http-api-compat` in the datastore layer.
- [ ] 1.2 Track the latest known ETag per datastore read.

## 2. Long-Poll Read Path

- [ ] 2.1 Add a datastore coordinator that owns USB reads, writes, sequence state, ETag state, and shutdown.
- [ ] 2.2 Add a background long-poll worker that forwards the latest ETag in the GET frame and uses a dedicated native-hold USB timeout.
- [ ] 2.3 Publish poller changes to local waiters through shared coordinator state without giving each HTTP request its own held USB read.
- [ ] 2.4 Preserve a 64-entry ETag/change transition history and fall back to a direct refresh when a stale client cannot be satisfied from local history.

## 3. HTTP Mapping

- [ ] 3.1 Read `If-None-Match` and `client` from the HTTP request.
- [ ] 3.2 Wait locally on coordinator state instead of issuing a request-local held USB read.
- [ ] 3.3 Return `304 Not Modified` with the same ETag on a no-change timeout.
- [ ] 3.4 Return `200` with the changed payload and new ETag on change.
- [ ] 3.5 Suppress proxy-originated changes for matching `client` identifiers where origin is known.

## 4. Tests And Validation

- [ ] 4.1 Unit-test long-poll GET frame construction with a non-default ETag.
- [ ] 4.2 Unit-test background poller fan-out to multiple waiters with a fake transport.
- [ ] 4.3 Unit-test timeout-to-304 and change-to-200 mapping with a fake transport.
- [ ] 4.4 Unit-test adjacent delta forwarding and stale-client refresh fallback.
- [ ] 4.5 Unit-test ordinary write/read serialization while the poller is active.
- [ ] 4.6 Validate prompt long-poll return on a live MOTU 624 after a parameter change while ordinary datastore requests still complete.
