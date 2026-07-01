## Context

The long-poll coordinator now uses one background poller with local fan-out and gives queued foreground operations priority between poll cycles. That prevents the poller from repeatedly winning the pipe, but it does not help a read or write that arrives while the device is already holding a native `/datastore` long-poll. In the idle case, that foreground operation can still wait for the device's full hold window before the USB pipe is available.

The hard part is not noticing that a foreground operation is waiting; it is safely stopping or isolating the in-flight USB read. The transport currently uses synchronous usbfs bulk reads. Simply shortening the read timeout is attractive, but it risks a stale long-poll reply arriving after the coordinator has moved on to a later foreground request.

## Goals / Non-Goals

**Goals:**

- Let ordinary datastore reads and writes proceed promptly even when a native long-poll read is already active.
- Define a measurable foreground preemption budget that is substantially below the native long-poll hold timeout.
- Preserve single-owner sequencing and ETag/history correctness.
- Keep local HTTP long-poll fan-out behavior intact.
- Prove the behavior with fake-transport tests and a live MOTU 624 validation.

**Non-Goals:**

- Do not open or claim ALSA audio interfaces.
- Do not introduce a second independent writer to the same USB control pipe.
- Do not depend on undocumented mixer or control paths for automated tests.
- Do not implement a full typed mixer/control-surface layer in this change.

## Decisions

### Add a cancellable poll-read path instead of relying on short timeouts

The coordinator should be able to ask the active poll read to stop when a foreground operation is queued. The poller acknowledges cancellation, cleans up any read completion state, and releases coordinator ownership before the foreground request is sent.

For the Linux usbfs transport, the selected implementation is an explicit cancellable bulk-read operation for long-poll reads using asynchronous usbfs bulk URBs. The poller submits the bulk IN read with `USBDEVFS_SUBMITURB`, and foreground preemption calls `USBDEVFS_DISCARDURB` on the active URB before the poller reaps the completion through `USBDEVFS_REAPURB`/`USBDEVFS_REAPURBNDELAY`. Ordinary datastore reads and writes continue to use the existing synchronous `USBDEVFS_BULK` path.

This primitive gives the coordinator a bounded hand-off point: a discarded URB is either reaped as cancelled, or it has already completed with a poll response that the poller can publish before releasing the USB owner. If a host transport does not expose this cancellable read abstraction, the coordinator selects degraded refresh mode and does not start native-hold background polling.

Alternative considered: reduce the poller's timeout to 1-2 seconds. Rejected as the primary design because the device-side hold can still complete after the host-side timeout. Without a robust stale-response quarantine, the late response can be mistaken for a later request or corrupt message sequencing.

### Keep foreground operations serialized through the coordinator

Foreground reads and writes still enter one coordinator path for sequence numbers, USB I/O, ETag state, and shutdown. The change is only that a queued foreground operation can preempt the active poll read before it sends its own USB frame.

Alternative considered: let foreground operations open a separate USB session. Rejected unless live validation proves it safe, because claiming the same vendor interface twice is likely to fail or create device-level ambiguity.

### Use a configured foreground preemption budget

The initial foreground preemption budget is 500 ms, measured from the coordinator registering a foreground waiter while the poller is inside a native long-poll read to either submitting the foreground USB request or entering/reporting explicit degraded behavior. This budget is intentionally far below the 15-16 second native hold window and is represented as a coordinator configuration value so unit tests and live validation share the same threshold.

The budget applies to dispatch/preemption latency, not the device's normal response body latency after the foreground request is sent. Existing response timeouts continue to govern response collection once the foreground request is on the USB pipe.

### Quarantine or discard cancelled-poll completions by sequence

If the cancellation path can still surface a poll response, the coordinator must either publish it as a valid poll change before the foreground operation starts, or discard it as belonging to the cancelled poll epoch. Later foreground response collection must not raise on or consume a stale poll reply as if it belonged to the foreground request. Response collection already filters mismatched MOTU message sequence values; cancelled poll completions keep relying on that sequence quarantine when a late packet is drained by the foreground collector.

### Use refresh-based degraded mode for unsupported transports

If the host platform cannot support interrupting the active poll read within the configured foreground preemption budget, the coordinator enters a degraded refresh mode instead of silently falling back to 15-second foreground waits. In this mode, native-hold background polling is disabled for that transport, so no background operation occupies the USB pipe for the full native hold window.

HTTP long-poll callers remain local waiters. The coordinator satisfies them from coordinated datastore refresh reads, including refreshes after foreground writes and refreshes used to decide whether a waiter receives a changed payload or `304 Not Modified`. Degraded mode must be visible through configuration/status/error reporting as unavailable foreground-preemptive native long-poll behavior, not presented as equivalent to the interruptible native-hold mode.

## Risks / Trade-offs

- USB cancellation semantics vary by kernel/driver path -> Mitigation: isolate cancellation support behind transport methods and cover fake transports plus live MOTU 624 validation.
- Late poll replies can corrupt foreground response collection -> Mitigation: track request message sequence/epoch and explicitly drain, publish, or discard cancelled-poll completions before foreground I/O.
- More transport complexity -> Mitigation: keep the existing synchronous read path for ordinary operations and add cancellation only for native long-poll reads.
- Foreground preemption may reduce idle long-poll efficiency -> Mitigation: restart the poller immediately after foreground work drains and continue using local fan-out/history.

## Migration Plan

1. Add an interruptible long-poll read abstraction to the datastore/transport boundary.
2. Add a foreground preemption budget setting and teach the coordinator to request cancellation when foreground work queues behind an active poll.
3. Add stale-response quarantine using message sequence or poll epoch tracking.
4. Preserve refresh-based degraded mode for hosts without cancellation support.
5. Validate against the live MOTU 624 with an active held long-poll, prompt foreground write/read, and resumed long-poll changes.

## Live Characterization

On 2026-07-01, the Linux usbfs async URB path was validated against the live MOTU 624 at `10.0.8.104` (`07fd:0005`, serial `0001f2fffe00c719`). The validator held a native `/datastore` long-poll, preempted it with three foreground `/datastore/uid` reads, and then preempted it with an idempotent foreground write to `/datastore/host/os` using body `{"value":"win"}`.

Observed foreground dispatch latency stayed well below the configured 500 ms budget: max read dispatch was 2.4 ms, and the idempotent write dispatched in 1.7 ms. The background `/datastore` poller resumed after each foreground operation. Three repeated preemption cycles returned the expected UID without stale cancelled-poll responses corrupting foreground collection. The existing live response-frame validator also passed afterward with `PASS: paths=3 frames=58`.

## Open Questions

- Should the 500 ms default foreground preemption budget be tuned after live MOTU validation?
