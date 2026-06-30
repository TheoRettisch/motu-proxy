## ADDED Requirements

### Requirement: Datastore long-polling
The system SHALL support datastore long-polling by accepting a client ETag via HTTP `If-None-Match`, comparing it against coordinated datastore state maintained by USB long-polling, and returning either the changed datastore payload with the new ETag or `304 Not Modified` when no change occurs within the wait window.

#### Scenario: No change within the wait window
- **WHEN** a client issues a long-poll GET with an `If-None-Match` ETag equal to the current datastore ETag and no datastore change occurs within the wait window
- **THEN** the proxy returns `304 Not Modified` with the unchanged ETag

#### Scenario: Change during the wait window
- **WHEN** a client issues a long-poll GET with an `If-None-Match` ETag and the datastore changes during the wait window
- **THEN** the proxy returns the changed datastore payload and the new ETag

### Requirement: Long-poll pipe safety
The system SHALL serve long-poll reads through a single background poller with local fan-out so HTTP long-poll clients do not each hold the single USB control pipe.

#### Scenario: Multiple long-poll clients share one held USB read
- **WHEN** multiple HTTP clients are waiting for datastore changes
- **THEN** the proxy uses one background USB long-poll and wakes matching local waiters when a change arrives

#### Scenario: Ordinary request proceeds while poller is active
- **WHEN** the background poller is active and an ordinary datastore read or write is requested
- **THEN** the proxy serializes that operation through the USB coordinator without corrupting the poller state or opening an additional USB session

### Requirement: Long-poll delta history
The system SHALL forward device delta payloads verbatim for adjacent ETag transitions, retain a bounded 64-entry transition history, and refresh directly when a client ETag cannot be satisfied safely from that history.

#### Scenario: Adjacent transition is returned verbatim
- **WHEN** a client waits from an ETag that directly precedes a change observed by the coordinator
- **THEN** the proxy returns the device delta payload verbatim with the new ETag

#### Scenario: Stale ETag falls back to refresh
- **WHEN** a client waits from an ETag that is missing from the coordinator's 64-entry transition history
- **THEN** the proxy performs a direct refresh or full datastore read rather than synthesizing a merged delta

### Requirement: Long-poll client filtering
The system SHALL honor the client identifier for proxy-originated writes where origin is known, so a long-poll client can avoid receiving its own changes back from the proxy.

#### Scenario: Proxy-originated own change is filtered
- **WHEN** a client issues a write with `client=<number>` and then waits with a long-poll GET using the same client identifier
- **THEN** the proxy does not wake that client's waiter solely for the write it can identify as originating from the same client

#### Scenario: Unknown-origin change is delivered
- **WHEN** the background poller observes a datastore change whose origin is unknown
- **THEN** the proxy wakes matching long-poll waiters regardless of their client identifier
