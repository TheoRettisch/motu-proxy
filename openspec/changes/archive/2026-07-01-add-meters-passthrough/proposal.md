## Why

The metering spike (`research/spike-metering/findings.md`) confirmed on the live 624 that the device exposes meters over the **same vendor USB pipe** the proxy already uses, via a top-level `/meters` resource: `GET /meters?meters=<group>` (e.g. `mix/level`, `ext/input`) returns a JSON meter frame with its own ETag. Two proxy-level gaps block a client from reaching it through the proxy today:

1. The USB GET frame only encodes the `client` query parameter — there is no way to send `meters=<group>`.
2. `/meters` is forced under a `/datastore` prefix by path normalization, so it never routes to the device's meters handler. (Over USB the device matches the path literally and `404`s on a `?`-suffixed path, so the meter group must travel as a USB query field, not a path query string — a confirmed spike finding.)

Closing those two gaps lets any meter client issue `GET /meters?meters=mix/level` through the proxy and receive the device's meter frames, exactly as against the device's native HTTP API.

Scope is deliberately narrow. This keeps `motu-proxy` a **schema-aware proxy** — a faithful, byte-level transport for the `/meters` resource. It does **not** add meter polling, a meter value model, channel mapping, or value interpretation. Continuous polling and any typed meter model belong to a **separate consumer project** that builds on this byte-faithful bridge.

## What Changes

- Generalize USB query-field encoding so a GET frame can carry ordered `(name, value)` query parameters, not only `client`. Existing single-`client` behavior and frame fixtures stay byte-identical.
- Route `/meters` as a top-level resource in path normalization (like `/apiversion`), with no `/datastore` prefix.
- Forward HTTP GET query parameters (e.g. `?meters=mix/level`) as USB query fields, since the device `404`s on a `?`-suffixed USB path. Existing write requests keep their current `client` query behavior.
- Treat meter `If-None-Match` as a one-shot device request header, not as datastore long-poll coordination.
- Return the device's meter response unchanged, exposing the meter ETag via the existing `ETag` header machinery.
- Add a single-shot, read-only `meters` CLI command for validation — one request per invocation, explicitly not a poller.
- Do **not** interpret meter values, map channels, or run any background poll loop.

## Capabilities

### New Capabilities
- None. Metering stays within the proxy; a typed meter model and polling are out of scope and belong to a separate consumer project.

### Modified Capabilities
- `motu-usb-datastore-proxy`: Gains faithful pass-through of the device's `/meters` resource (generalized query fields, top-level routing, ETag exposure) without interpreting meter data.

## Impact

- Affected code: `motu_proxy/protocol.py` (generalized GET query fields), `motu_proxy/paths.py` (route `/meters`), `motu_proxy/http_server.py` (forward GET query params and meter ETags correctly), `motu_proxy/datastore.py` (pass GET query params through), `motu_proxy/cli.py` (one-shot `meters` command).
- Affected APIs: `GET /meters?meters=<group>` now works through the proxy; existing datastore and `client` behavior are unchanged.
- Affected systems: meter clients (the separate polling project) that point at the proxy instead of the device's network port; validated against the live 624 over USB.
- Dependencies: builds on the already-implemented `add-datastore-http-api-compat` (ETag and `client` plumbing). Standard library only.
- Operational sequencing: this change must not introduce polling; land `avoid-long-poll-foreground-blocking` before recommending high-rate meter consumers through the proxy.
