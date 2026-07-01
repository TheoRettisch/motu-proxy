## Why

The current long-poll coordinator prevents the poller from repeatedly winning the USB pipe, but a foreground read or write that arrives during an already-active native long-poll can still wait for the device's full hold window before it is sent. Mixer and control-surface workflows need prompt writes even when the datastore is otherwise idle and a long-poll is being held.

## What Changes

- Add a foreground-responsive long-poll mode so ordinary datastore reads and writes do not wait behind an active native long-poll hold.
- Define a safe interruption, cancellation, or isolation strategy for the active long-poll without letting stale poll responses corrupt later request sequencing.
- Keep long-poll fan-out semantics for HTTP waiters: one shared change stream, ETag history, local client filtering, and `304` mapping.
- Add fake-transport and live-device validation that foreground writes/read requests complete promptly while the long-poll stream remains healthy.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `motu-usb-datastore-proxy`: Long-poll pipe safety gains a stronger responsiveness contract for foreground datastore reads and writes while a native long-poll is active.

## Impact

- Affected code: `motu_proxy/datastore.py`, possibly `motu_proxy/transports/usbfs.py`, and `motu_proxy/cli.py` if configuration knobs are needed.
- Affected tests: coordinator concurrency tests, response sequencing tests, and live MOTU 624 validation.
- Affected systems: mixer/control-surface clients that issue writes while HTTP long-poll clients are connected.
- Dependencies: standard library only unless the selected design requires a Linux USB cancellation primitive already available through `ioctl`.
