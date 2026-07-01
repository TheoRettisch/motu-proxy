## 1. Cancellation Strategy

- [x] 1.1 Characterize Linux usbfs options for interrupting an active bulk IN long-poll read on the live MOTU 624.
- [x] 1.2 Select and document the cancellation primitive or fallback mode and confirm the foreground preemption budget in `design.md` before implementing coordinator behavior.
- [x] 1.3 Add a transport-level abstraction for cancellable long-poll reads without changing ordinary synchronous read/write behavior.

## 2. Coordinator Preemption

- [x] 2.1 Track active poll-read state so foreground reads and writes can request preemption while the poller is inside a native hold.
- [x] 2.2 Cancel, interrupt, or isolate the active poll read and dispatch the queued foreground USB operation within the configured foreground preemption budget.
- [x] 2.3 Quarantine cancelled-poll completions by message sequence or poll epoch so stale poll responses cannot corrupt foreground response collection.
- [x] 2.4 Resume the background poller from the latest coordinated ETag after foreground work completes.
- [x] 2.5 Preserve explicit degraded refresh mode for hosts/transports that cannot safely interrupt active poll reads by disabling native-hold background polling while serving local HTTP waiters through coordinated refreshes.

## 3. Tests

- [x] 3.1 Unit-test that a foreground read preempts an active held poll within the configured foreground preemption budget instead of waiting for the poll timeout.
- [x] 3.2 Unit-test that a foreground write preempts an active held poll within the configured foreground preemption budget and publishes refreshed datastore state to other waiters.
- [x] 3.3 Unit-test that a cancelled poll response is drained, published, or discarded without being mistaken for the foreground response.
- [x] 3.4 Unit-test that local long-poll waiters continue receiving changes after a preempted foreground operation.
- [x] 3.5 Unit-test that the unsupported/degraded transport path does not start native-hold background polling, serves local waiters through coordinated refreshes, and reports foreground-preemptive native long-poll behavior as unavailable.

## 4. Live Validation

- [x] 4.1 Validate on the live MOTU 624 that a foreground read is dispatched within the configured foreground preemption budget while a native long-poll is actively held.
- [x] 4.2 Validate on the live MOTU 624 that a foreground write is dispatched within the configured foreground preemption budget while a native long-poll is actively held and that `/datastore` long-polling resumes afterward.
- [x] 4.3 Confirm no stale cancelled-poll response appears in subsequent foreground reads after repeated preemption cycles.
