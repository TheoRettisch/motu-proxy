## 1. ETag Extraction

- [x] 1.1 Locate the datastore ETag within the USB GET reply and confirm against a live MOTU 624.
- [x] 1.2 Add an ETag parser in `motu_proxy/parser.py` with unit tests over captured reply bytes.

## 2. HTTP Response Fidelity

- [x] 2.1 Emit the `ETag` header on HTTP GET datastore responses.
- [x] 2.2 Emit `Cache-Control: no-cache` on datastore responses.
- [x] 2.3 Specify and test single-key, subtree, and full-datastore response shapes.

## 3. Client Identifier

- [x] 3.1 Parse the `client` query-string parameter on reads and writes.
- [x] 3.2 Forward the client identifier through the datastore read/write call path.

## 4. Validation

- [x] 4.1 Reproduce the documented `curl` GET examples against the proxy and confirm equivalent headers and bodies.
- [x] 4.2 Confirm writes remain disabled by default and PATCH remains a POST alias.
