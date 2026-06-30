## Context

`motu-proxy serve` currently discovers one MOTU USB device, opens one `UsbFsTransport`, initializes one `MotuUsbDatastore`, and keeps that object for the server lifetime. The `DatastoreCoordinator` serializes foreground operations against a background `/datastore` long-poller, but it does not own discovery or reopening. If the MOTU is unplugged, power-cycled, or reset by the host, reads and writes can fail until the whole process is restarted.

The target deployment needs a long-running service that can survive transient USB availability changes while continuing to avoid ALSA audio interfaces and the vendor interface single-owner constraint.

## Goals / Non-Goals

**Goals:**
- Recover automatically after MOTU disconnect, power-cycle, USB reset, or transient discovery/open/init failure.
- Return temporary-unavailable responses for foreground HTTP requests while the device is absent.
- Preserve the existing single USB datastore control session model whenever the device is healthy.
- Avoid replaying writes after a reconnect; each client request is attempted at most once unless the client retries.
- Keep the implementation dependency-free and compatible with the existing sysfs/usbfs transport.

**Non-Goals:**
- No multi-device load balancing or automatic failover between different MOTU units.
- No attempt to claim ALSA-owned audio interfaces.
- No persistence or merge of pending datastore writes across disconnects.
- No change to the MOTU USB frame format or response parser.

## Decisions

1. Introduce a datastore manager around discovery/open/init.

   The manager should own `DatastoreConfig`, the current transport/datastore context, reconnect state, and a small retry/backoff policy. It should expose `get()` and `post()` methods compatible with the coordinator's current call shape so `DatastoreCoordinator` can continue to serialize operations. On discovery, open, init, read, or write failures that indicate device loss, the manager closes the current session, marks the device unavailable, and allows a later operation or poll cycle to attempt reopen.

   Alternative considered: keep opening in `command_serve()` and restart the coordinator on failure. That spreads recovery responsibility across CLI/server/coordinator code and makes it harder to keep one USB owner.

2. Report temporary unavailability as a domain error.

   Add a specific error such as `DatastoreDeviceUnavailable` for "no currently usable MOTU datastore session". HTTP handling should map it to `503`. The CLI `serve` command should keep running; one-shot CLI commands can continue to fail normally because they do not have a long-lived recovery loop.

   Alternative considered: surface raw `OSError`, `NoDeviceFound`, or `NoControlInterfaceFound`. That leaks implementation detail to clients and makes it difficult to distinguish temporary outage from malformed responses.

3. Let the poller drive background reconnect attempts, with foreground opportunistic retry.

   The background poll loop should continue after device-unavailable failures and use a bounded sleep/backoff before retrying. A foreground read/write may try to reopen if no session exists, but it should either complete promptly or return `503`; it should not hold HTTP workers through long unbounded reconnect loops.

   Alternative considered: a separate reconnect thread. The existing poller already wakes regularly and owns long-poll state, so a second background thread is unnecessary unless implementation proves otherwise.

4. Do not replay writes across reconnect.

   If a write loses the device before a valid datastore response is collected, the proxy should return an error and let the client decide whether to retry. This avoids duplicating a non-idempotent datastore mutation after a reconnect.

## Risks / Trade-offs

- Ambiguous mid-write failure state -> Return an error without replay; document that clients must decide whether to retry.
- Reconnect loop could spam sysfs or logs -> Use bounded backoff and update `last_poller_error` without noisy repeated tracebacks in normal mode.
- Multiple foreground requests during outage could stampede discovery -> Guard open attempts with a manager lock and share the unavailable state.
- Device identity drift after reconnect -> Reuse existing `DatastoreConfig` matching, including configured serial. If multiple devices match, recovery remains unavailable until selection is unambiguous.
