## ADDED Requirements

### Requirement: Datastore write permission enforcement
The system SHALL reject writes to datastore paths documented as read-only before performing any USB write.

#### Scenario: Write to a read-only path
- **WHEN** a client attempts to write to a datastore path whose documented permission is read-only
- **THEN** the proxy rejects the request with an HTTP `403` and does not send a USB write

### Requirement: Datastore write value validation
The system SHALL validate write values against the documented type, numeric range, and enum values for known datastore paths, and SHALL reject values that do not conform.

#### Scenario: Out-of-range value
- **WHEN** a client writes a value outside the documented range for a known path, such as a channel fader greater than the documented maximum
- **THEN** the proxy rejects the request with an HTTP `422` and does not send a USB write

#### Scenario: Invalid enum value
- **WHEN** a client writes a value that is not one of the documented enum values for a known path
- **THEN** the proxy rejects the request with an HTTP `422` and does not send a USB write

### Requirement: Forward-compatible passthrough for unknown paths
The system SHALL forward writes to datastore paths that are not present in its embedded schema, so that newer firmware paths are not blocked, and SHALL provide a way to disable validation entirely.

#### Scenario: Undocumented path is forwarded
- **WHEN** a client writes to a datastore path that is not in the embedded schema and writes are enabled
- **THEN** the proxy forwards the write to the device

#### Scenario: Validation disabled
- **WHEN** the proxy is started with validation disabled
- **THEN** the proxy forwards writes without checking type, range, enum, or permission
