## Why

The MOTU datastore API is versioned: a global `apiversion` lives outside the datastore, and each section carries its own semver at `ext/caps/avb`, `ext/caps/router`, and `ext/caps/mixer`. A section's paths only exist if its capability entry is present. The API explicitly advises clients to read the datastore and check versions before assuming a given section's paths are available. Device identity (`uid`, `model_name`, `firmware_version`, `serial_number`) is likewise exposed in the datastore.

`motu-proxy` already passes `/apiversion` through path normalization, but it offers no command to report what a connected device supports. A discovery command gives users and downstream tools a single call to learn the API version, which sections (mixer / router / avb) are present and at what version, and the device identity — the natural first step before driving mixer or routing paths.

## What Changes

- Add a `motu-proxy info` command that reports `apiversion`, the per-section capability versions (`ext/caps/avb`, `ext/caps/router`, `ext/caps/mixer`), and device identity (`uid`, `model_name`, `firmware_version`, `serial_number`).
- Read these from the documented datastore paths over USB.
- Present the result as human-readable text and as JSON (`--json`).
- Add tests for capability assembly using a fake transport.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: Adds a capability and version discovery command.

## Impact

- Affected code: `motu_proxy/cli.py` (new `info` command), `motu_proxy/datastore.py` (capability assembly helper).
- Affected APIs: a new CLI command; no change to existing commands.
- Affected systems: operators and tools that need to discover device capabilities before driving the datastore.
- Dependencies: standard library only.
