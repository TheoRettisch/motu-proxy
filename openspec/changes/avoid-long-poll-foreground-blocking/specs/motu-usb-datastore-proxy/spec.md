## MODIFIED Requirements

### Requirement: Long-poll pipe safety
The system SHALL serve long-poll reads through a single background poller with local fan-out so HTTP long-poll clients do not each hold the single USB control pipe, and SHALL allow ordinary datastore reads and writes to proceed without waiting for the active native long-poll hold window.

#### Scenario: Multiple long-poll clients share one held USB read
- **WHEN** multiple HTTP clients are waiting for datastore changes
- **THEN** the proxy uses one background USB long-poll and wakes matching local waiters when a change arrives

#### Scenario: Ordinary request proceeds while poller is active
- **WHEN** the background poller is active and an ordinary datastore read or write is requested
- **THEN** the proxy serializes that operation through the USB coordinator without corrupting the poller state or opening an additional USB session

#### Scenario: Foreground request preempts active native hold
- **WHEN** an ordinary datastore read or write is requested while the background poller is inside a native long-poll read held by the device
- **THEN** the proxy interrupts, cancels, or safely isolates the active long-poll read and sends the foreground operation without waiting for the native long-poll hold window to expire

#### Scenario: Cancelled poll response cannot corrupt foreground request
- **WHEN** a long-poll read is interrupted or cancelled to serve a foreground operation
- **THEN** any response or completion belonging to the interrupted long-poll is published, drained, or discarded before it can be interpreted as the foreground operation's response

#### Scenario: Long-poll stream resumes after foreground operation
- **WHEN** a foreground datastore operation completes after preempting an active long-poll
- **THEN** the background poller resumes from the latest coordinated ETag and continues waking local long-poll waiters on later changes
