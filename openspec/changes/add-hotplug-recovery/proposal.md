## Why

The HTTP proxy currently opens one USB datastore session for the server lifetime, so a MOTU disconnect, USB reset, or power-cycle can leave the service unable to recover until it is restarted. In a car or embedded deployment, the proxy should tolerate device availability changes and resume once the same target device returns.

## What Changes

- Add automatic datastore reopen/retry behavior around discovery, usbfs transport open, and datastore initialization.
- Return a clear temporary-unavailable error for foreground HTTP operations while the device is absent or reconnecting.
- Keep read/write safety guarantees intact during reconnect: no parallel USB sessions, no ALSA interface claiming, and no writes replayed implicitly.
- Preserve existing single-session behavior while the device remains healthy.

## Capabilities

### New Capabilities

### Modified Capabilities
- `motu-usb-datastore-proxy`: Add hotplug and power-cycle recovery requirements for the long-running HTTP proxy.

## Impact

- Affected code: `motu_proxy/datastore.py`, `motu_proxy/cli.py`, and HTTP error mapping in `motu_proxy/http_server.py`.
- Affected behavior: long-running `serve` mode will recover from USB device loss/reappearance instead of requiring process restart.
- Dependencies: no new runtime dependencies are expected; recovery should use existing sysfs discovery and usbfs transport primitives.
- Hardware validation: should include a live MOTU unplug/replug or power-cycle validation path using the vendor-specific datastore interface only.
