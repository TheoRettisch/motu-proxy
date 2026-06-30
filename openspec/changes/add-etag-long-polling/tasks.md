## 1. Reply ETag Plumbing

- [ ] 1.1 Consume the reply ETag parser from `add-datastore-http-api-compat` in the datastore layer.
- [ ] 1.2 Track the latest known ETag per datastore read.

## 2. Long-Poll Read Path

- [ ] 2.1 Add a datastore long-poll read that forwards a client ETag in the GET frame.
- [ ] 2.2 Use a dedicated USB read timeout greater than the device hold and distinguish "held" from "quiet".
- [ ] 2.3 Apply a bounded maximum wait so a held read does not block the USB pipe indefinitely.

## 3. HTTP Mapping

- [ ] 3.1 Read `If-None-Match` and `client` from the HTTP request.
- [ ] 3.2 Return `304 Not Modified` with the same ETag on a no-change timeout.
- [ ] 3.3 Return `200` with the changed payload and new ETag on change.

## 4. Tests And Validation

- [ ] 4.1 Unit-test long-poll GET frame construction with a non-default ETag.
- [ ] 4.2 Unit-test timeout-to-304 and change-to-200 mapping with a fake transport.
- [ ] 4.3 Validate prompt long-poll return on a live MOTU 624 after a parameter change.
