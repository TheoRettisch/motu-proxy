## Why

HTTP write mode currently requires a generated security token for every POST/PATCH once `--allow-writes` is enabled, even for loopback-only local automation. That extra mandatory credential makes simple trusted-local integrations harder than necessary; write-token protection should be available when an operator wants it, while explicit write-mode opt-in remains the primary safety gate.

## What Changes

- Keep HTTP writes disabled by default; `motu-proxy serve` still requires `--allow-writes` before POST/PATCH can reach USB.
- Make HTTP write-token enforcement opt-in instead of automatic when writes are enabled.
- Add an explicit serve option to require a generated write token, preserving support for token files and debug/no-file token display.
- When token protection is not enabled, accept otherwise-valid writes without `X-Motu-Proxy-Token` or `Authorization: Bearer` credentials.
- Update CLI help, service/deployment documentation, and tests to distinguish write-mode enablement from optional token protection.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: Change HTTP write-token enforcement from mandatory for all enabled HTTP writes to an explicit opt-in protection.

## Impact

- Affected code: `motu_proxy/cli.py`, `motu_proxy/http_server.py`, HTTP/CLI tests, and operational documentation or service examples that mention write-token behavior.
- Affected APIs: HTTP POST/PATCH requests no longer require a token unless the server was started with the new token-protection option; existing token headers remain accepted when token protection is enabled.
- Affected systems: local automation and any service/unit configuration that enables HTTP writes.
- Dependencies: no new runtime dependency is expected.
