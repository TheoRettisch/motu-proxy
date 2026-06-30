## ADDED Requirements

### Requirement: Datastore long-polling
The system SHALL support datastore long-polling by accepting a client ETag via HTTP `If-None-Match`, forwarding it to the device over USB, and returning either the changed datastore payload with the new ETag or `304 Not Modified` when no change occurs within the wait window.

#### Scenario: No change within the wait window
- **WHEN** a client issues a long-poll GET with an `If-None-Match` ETag equal to the current datastore ETag and no datastore change occurs within the wait window
- **THEN** the proxy returns `304 Not Modified` with the unchanged ETag

#### Scenario: Change during the wait window
- **WHEN** a client issues a long-poll GET with an `If-None-Match` ETag and the datastore changes during the wait window
- **THEN** the proxy returns the changed datastore payload and the new ETag

### Requirement: Long-poll pipe safety
The system SHALL serve long-poll reads without permanently blocking other datastore requests on the single USB control pipe.

#### Scenario: Held read does not deadlock the pipe
- **WHEN** a long-poll read is waiting for a datastore change
- **THEN** the read observes a bounded maximum wait so that subsequent datastore requests can proceed

### Requirement: Long-poll client filtering
The system SHALL forward the client identifier on long-poll reads so the device can filter that client's own changes from its long-poll stream.

#### Scenario: Client identifier forwarded on long-poll
- **WHEN** a client issues a long-poll GET with a `client=<number>` parameter
- **THEN** the proxy forwards that client identifier to the device long-poll read
