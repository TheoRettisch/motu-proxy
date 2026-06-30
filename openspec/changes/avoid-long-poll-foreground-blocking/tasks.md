## 1. Cancellation Strategy

- [ ] 1.1 Characterize Linux usbfs options for interrupting an active bulk IN long-poll read on the live MOTU 624.
- [ ] 1.2 Select and document the cancellation primitive or fallback mode in `design.md` before implementing coordinator behavior.
- [ ] 1.3 Add a transport-level abstraction for cancellable long-poll reads without changing ordinary synchronous read/write behavior.

## 2. Coordinator Preemption

- [ ] 2.1 Track active poll-read state so foreground reads and writes can request preemption while the poller is inside a native hold.
- [ ] 2.2 Cancel, interrupt, or isolate the active poll read before sending a queued foreground USB operation.
- [ ] 2.3 Quarantine cancelled-poll completions by message sequence or poll epoch so stale poll responses cannot corrupt foreground response collection.
- [ ] 2.4 Resume the background poller from the latest coordinated ETag after foreground work completes.
- [ ] 2.5 Preserve an explicit degraded mode for hosts/transports that cannot safely interrupt active poll reads.

## 3. Tests

- [ ] 3.1 Unit-test that a foreground read preempts an active held poll instead of waiting for the poll timeout.
- [ ] 3.2 Unit-test that a foreground write preempts an active held poll and publishes refreshed datastore state to other waiters.
- [ ] 3.3 Unit-test that a cancelled poll response is drained, published, or discarded without being mistaken for the foreground response.
- [ ] 3.4 Unit-test that local long-poll waiters continue receiving changes after a preempted foreground operation.
- [ ] 3.5 Unit-test the unsupported/degraded transport path.

## 4. Live Validation

- [ ] 4.1 Validate on the live MOTU 624 that a foreground read completes promptly while a native long-poll is actively held.
- [ ] 4.2 Validate on the live MOTU 624 that a foreground write completes promptly while a native long-poll is actively held and that `/datastore` long-polling resumes afterward.
- [ ] 4.3 Confirm no stale cancelled-poll response appears in subsequent foreground reads after repeated preemption cycles.
