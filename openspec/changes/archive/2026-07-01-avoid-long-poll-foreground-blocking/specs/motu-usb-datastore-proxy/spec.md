## MODIFIED Requirements

### Requirement: Long-poll pipe safety
The system SHALL serve long-poll reads through a single background poller with local fan-out so HTTP long-poll clients do not each hold the single USB control pipe, and SHALL either allow ordinary datastore reads and writes to be dispatched within the configured foreground preemption budget while a native long-poll is active, or enter an explicit degraded refresh mode when the selected transport cannot safely preempt an active native hold.

#### Scenario: Multiple long-poll clients share one held USB read
- **WHEN** multiple HTTP clients are waiting for datastore changes
- **THEN** the proxy uses one background USB long-poll and wakes matching local waiters when a change arrives

#### Scenario: Ordinary request proceeds while poller is active
- **WHEN** the background poller is active and an ordinary datastore read or write is requested
- **THEN** the proxy serializes that operation through the USB coordinator without corrupting the poller state or opening an additional USB session

#### Scenario: Foreground request preempts active native hold
- **WHEN** an ordinary datastore read or write is requested while the background poller is inside a native long-poll read held by the device
- **THEN** the proxy interrupts, cancels, or safely isolates the active long-poll read and sends the foreground operation within the configured foreground preemption budget instead of waiting for the native long-poll hold window to expire

#### Scenario: Cancelled poll response cannot corrupt foreground request
- **WHEN** a long-poll read is interrupted or cancelled to serve a foreground operation
- **THEN** any response or completion belonging to the interrupted long-poll is published, drained, or discarded before it can be interpreted as the foreground operation's response

#### Scenario: Unsupported transport enters explicit degraded mode
- **WHEN** the selected transport cannot safely interrupt, cancel, or isolate an active native long-poll read
- **THEN** the proxy disables native-hold background polling for that transport and keeps HTTP long-poll clients as local waiters served by coordinated datastore refresh reads
- **AND** the proxy reports foreground-preemptive native long-poll behavior as unavailable instead of silently allowing foreground requests to wait for the full native hold window

#### Scenario: Long-poll stream resumes after foreground operation
- **WHEN** a foreground datastore operation completes after preempting an active long-poll
- **THEN** the background poller resumes from the latest coordinated ETag and continues waking local long-poll waiters on later changes
