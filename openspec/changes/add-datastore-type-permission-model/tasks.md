## 1. Schema

- [ ] 1.1 Generate a datastore path schema (type, permission, range, enum) from the documented MOTU API.
- [ ] 1.2 Embed the schema as a data module in `motu_proxy/schema.py`.

## 2. Validation Layer

- [ ] 2.1 Implement longest-prefix path matching with numeric placeholder segments.
- [ ] 2.2 Reject writes to read-only paths.
- [ ] 2.3 Validate value type, numeric range, and enum membership for known paths.
- [ ] 2.4 Forward undocumented paths by default, with an optional warning.

## 3. Integration

- [ ] 3.1 Map permission denial to HTTP `403` and validation failure to HTTP `422`.
- [ ] 3.2 Add a `--no-validate` flag to forward writes unchecked.

## 4. Tests

- [ ] 4.1 Test read-only path denial without USB I/O.
- [ ] 4.2 Test range, type, and enum violations.
- [ ] 4.3 Test undocumented-path passthrough and `--no-validate`.
