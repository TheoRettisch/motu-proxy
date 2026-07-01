## Why

`motu-proxy serve` is now close to useful as a long-running process, but there is no first-class way to install, configure, supervise, and validate it as production service infrastructure. A car or embedded host needs predictable systemd integration, conservative hardening, clean shutdown, and operator documentation before relying on it outside manual development sessions.

## What Changes

- Add systemd-oriented service packaging artifacts for running the HTTP proxy as a read-only service by default.
- Provide a configurable environment-file pattern for listen address, port, serial/device selection, validation/debug flags, and explicit write-mode opt-in.
- Use service-manager runtime directory handling for optional write-token files so tokens are not printed to journald by default when token protection is enabled and are removed on shutdown.
- Add clean signal/shutdown behavior for supervised `serve` mode so the coordinator closes and runtime token cleanup runs on service stop.
- Add service hardening guidance/directives that preserve access to sysfs and the vendor-specific USB device while avoiding ALSA interface ownership changes.
- Document installation, configuration, status checks, logs, and live MOTU validation workflow.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: Add production Linux service packaging, lifecycle, configuration, hardening, and operational validation requirements for `serve` mode.

## Impact

- Affected code: `motu_proxy/cli.py`, `motu_proxy/http_server.py`, package metadata, repository packaging/deploy artifacts, README or operations documentation, and tests.
- Affected behavior: supervised `serve` mode gains clean signal handling and documented systemd defaults; normal CLI reads/writes remain unchanged.
- Affected systems: Linux hosts running systemd, especially embedded/car deployments with a MOTU USB datastore interface.
- Dependencies: no new runtime Python dependency is expected. Packaging should prefer standard systemd units and existing Python entry points.
