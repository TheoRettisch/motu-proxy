## Why

Every datastore path in the MOTU AVB API has an assigned type, a permission (`r` or `rw`), and — for many mixer and routing parameters — a documented value range (for example a channel fader is `0`–`4` linear, a pan is `-1`–`1`, an EQ frequency is `20`–`20000` Hz, a compressor ratio is `1`–`10`). The API states that clients can only change parameters marked `rw` and that each write must match the path's type.

`motu-proxy` currently forwards any write straight to the device over USB with no awareness of types, permissions, or ranges. A typo or an out-of-range value reaches the hardware before any check. Adding a documented-path model lets the proxy reject writes to read-only paths and reject malformed or out-of-range values before they reach USB, with clear error messages — a strict improvement to the existing `--allow-writes` safety posture.

## What Changes

- Embed a datastore schema (path, type, permission, range, enum values) generated from the documented MOTU datastore API.
- Validate writes before sending over USB: reject writes to `r` (read-only) paths, and reject values that do not match the documented type, range, or enum.
- Return appropriate HTTP errors: `403` for read-only paths, `422` for type or range violations.
- Treat undocumented paths as forward-compatible: forward them by default with an optional warning, so newer firmware paths are not blocked.
- Provide an escape hatch (`--no-validate`) to forward writes unchecked.
- Add tests for permission denial, range/type/enum checks, and undocumented-path passthrough.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: Writes gain type, permission, and range validation derived from the documented datastore API.

## Impact

- Affected code: new `motu_proxy/schema.py` (embedded path table), `motu_proxy/datastore.py`, `motu_proxy/http_server.py`, `motu_proxy/cli.py` (the `--no-validate` flag).
- Affected APIs: HTTP writes may now return `403` or `422`; previously such writes were forwarded.
- Affected systems: any client that writes to the datastore through the proxy.
- Dependencies: standard library only; the schema is generated once from the API documentation and embedded as data.
