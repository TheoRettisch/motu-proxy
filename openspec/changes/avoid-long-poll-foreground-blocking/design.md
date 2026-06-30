## Context

The long-poll coordinator now uses one background poller with local fan-out and gives queued foreground operations priority between poll cycles. That prevents the poller from repeatedly winning the pipe, but it does not help a read or write that arrives while the device is already holding a native `/datastore` long-poll. In the idle case, that foreground operation can still wait for the device's full hold window before the USB pipe is available.

The hard part is not noticing that a foreground operation is waiting; it is safely stopping or isolating the in-flight USB read. The transport currently uses synchronous usbfs bulk reads. Simply shortening the read timeout is attractive, but it risks a stale long-poll reply arriving after the coordinator has moved on to a later foreground request.

## Goals / Non-Goals

**Goals:**

- Let ordinary datastore reads and writes proceed promptly even when a native long-poll read is already active.
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

For the Linux usbfs transport, the preferred implementation is an explicit cancellable bulk-read operation for long-poll reads. Candidate approaches include asynchronous usbfs URBs with discard/reap semantics, or another Linux-supported cancellation mechanism that guarantees the coordinator knows whether the poll read produced a response or was cancelled before the foreground request proceeds.

Alternative considered: reduce the poller's timeout to 1-2 seconds. Rejected as the primary design because the device-side hold can still complete after the host-side timeout. Without a robust stale-response quarantine, the late response can be mistaken for a later request or corrupt message sequencing.

### Keep foreground operations serialized through the coordinator

Foreground reads and writes still enter one coordinator path for sequence numbers, USB I/O, ETag state, and shutdown. The change is only that a queued foreground operation can preempt the active poll read before it sends its own USB frame.

Alternative considered: let foreground operations open a separate USB session. Rejected unless live validation proves it safe, because claiming the same vendor interface twice is likely to fail or create device-level ambiguity.

### Quarantine or discard cancelled-poll completions by sequence

If the cancellation path can still surface a poll response, the coordinator must either publish it as a valid poll change before the foreground operation starts, or discard it as belonging to the cancelled poll epoch. Later foreground response collection must not raise on or consume a stale poll reply as if it belonged to the foreground request.

### Make fallback behavior explicit

If the host platform cannot support interrupting the active poll read, the coordinator should expose that limitation explicitly instead of silently falling back to 15-second foreground waits. The implementation can choose a degraded mode, such as disabling native-hold background polling and using local waiters plus refreshes, but it must not claim zero-wait foreground behavior without an interruptible path.

## Risks / Trade-offs

- USB cancellation semantics vary by kernel/driver path -> Mitigation: isolate cancellation support behind transport methods and cover fake transports plus live MOTU 624 validation.
- Late poll replies can corrupt foreground response collection -> Mitigation: track request message sequence/epoch and explicitly drain, publish, or discard cancelled-poll completions before foreground I/O.
- More transport complexity -> Mitigation: keep the existing synchronous read path for ordinary operations and add cancellation only for native long-poll reads.
- Foreground preemption may reduce idle long-poll efficiency -> Mitigation: restart the poller immediately after foreground work drains and continue using local fan-out/history.

## Migration Plan

1. Add an interruptible long-poll read abstraction to the datastore/transport boundary.
2. Teach the coordinator to request cancellation when foreground work queues behind an active poll.
3. Add stale-response quarantine using message sequence or poll epoch tracking.
4. Preserve a safe fallback for hosts without cancellation support.
5. Validate against the live MOTU 624 with an active held long-poll, prompt foreground write/read, and resumed long-poll changes.

## Open Questions

- Which usbfs cancellation primitive behaves best with this device: async URB discard/reap, closing/reopening the fd, or another kernel-supported path?
- Does a cancelled native long-poll produce a later device reply that must be drained, or does the device fully abandon it?
- What foreground preemption budget should be asserted in live validation?
