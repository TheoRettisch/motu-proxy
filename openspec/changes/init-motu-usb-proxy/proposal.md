## Why

The handover MVP proves that Linux can access the MOTU 624 datastore over the vendor USB control interface, but the implementation is a single dependency-free prototype with protocol, USB transport, CLI, and HTTP serving all intertwined. Rebuilding it in a cleaner state creates a maintainable baseline while preserving the known-good functionality already validated against a live Ubuntu 24.04 host with an attached MOTU 624.

## What Changes

- Rebuild the MOTU USB datastore MVP as a small Python package with separated protocol, transport, datastore, CLI, and HTTP proxy layers.
- Preserve the existing user-facing functionality: `selftest`, `get`, `probe`, explicit CLI `post`, read-only localhost `serve`, and write-enabled POST/PATCH only behind an explicit `--allow-writes` flag.
- Preserve the current safety posture: bind to `127.0.0.1` by default, keep writes disabled by default, and avoid disrupting the class-compliant ALSA audio interfaces.
- Keep the Linux usbfs transport as the baseline implementation because it works on the current Ubuntu 24.04 host and remains compatible with tiny deployment environments.
- Evaluate PyUSB only if it provides a concrete benefit, such as simpler descriptor discovery, better diagnostics, or cleaner cross-host development behavior. Do not make PyUSB a required dependency for the initial same-functionality rebuild unless that benefit is demonstrated.
- Add automated tests around frame construction, CRC32, path normalization, write gating, and response extraction so the rebuild can stay equivalent to the handover MVP.

## Capabilities

### New Capabilities
- `motu-usb-datastore-proxy`: Provides a localhost HTTP and CLI proxy for MOTU AVB datastore operations over the Linux USB vendor control interface.

### Modified Capabilities
- None.

## Impact

- Affected code: new Python package and tests replacing the handover prototype as the maintained implementation.
- Affected APIs: CLI commands and localhost HTTP behavior should remain compatible with the handover MVP for the currently validated paths.
- Affected systems: Ubuntu 24.04 development host with MOTU 624 attached over USB, and future Buildroot-style deployment environments that benefit from a dependency-light usbfs backend.
- Dependencies: standard-library Python is the default. PyUSB remains optional and must be justified by measured development or reliability benefits before inclusion.
