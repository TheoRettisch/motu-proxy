## ADDED Requirements

### Requirement: Device discovery
The system SHALL discover a MOTU AVB USB device by VID:PID and optional serial number, and SHALL identify a vendor-specific bulk control interface without claiming ALSA audio streaming interfaces.

#### Scenario: Single MOTU 624 is attached
- **WHEN** the host exposes a MOTU device with VID:PID `07fd:0005` and one unbound vendor-specific interface with bulk IN and OUT endpoints
- **THEN** the system selects that device and uses the discovered bulk endpoints for datastore transport

#### Scenario: Multiple MOTU devices match
- **WHEN** more than one MOTU USB device matches the configured VID:PID and no serial is provided
- **THEN** the system refuses to choose implicitly and reports that `--serial` is required

### Requirement: USB datastore protocol frames
The system SHALL build MOTU USB datastore init, ACK, GET, and POST frames compatible with the handover MVP and the captured MOTU protocol fixtures.

#### Scenario: GET frame fixture
- **WHEN** a GET frame is built for `/datastore` with the captured sequence and message sequence values
- **THEN** the resulting bytes match the known handover fixture exactly

#### Scenario: POST frame fixture
- **WHEN** a POST frame is built for `/datastore/host/os` with body `{"value": "win"}` and the captured sequence and message sequence values
- **THEN** the resulting bytes match the known handover fixture exactly

#### Scenario: CRC32 calculation
- **WHEN** the CRC32 is calculated for `123456789`
- **THEN** the result is `0xcbf43926`

### Requirement: CLI datastore operations
The system SHALL provide command-line operations equivalent to the handover MVP for self-test, reads, probing, and explicit writes.

#### Scenario: Self-test succeeds
- **WHEN** the user runs the `selftest` command
- **THEN** the system validates protocol fixtures and CRC32 behavior without requiring a connected MOTU device

#### Scenario: Read datastore path
- **WHEN** the user runs `get /datastore/uid` against a connected MOTU 624
- **THEN** the system returns the datastore response body for the UID path

#### Scenario: Probe baseline paths
- **WHEN** the user runs the `probe` command against a connected MOTU 624
- **THEN** the system attempts the same harmless baseline reads as the handover MVP and reports each response independently

#### Scenario: Explicit CLI POST
- **WHEN** the user runs the `post` command with a datastore path and JSON body
- **THEN** the system sends a POST datastore operation over USB and returns the response body

### Requirement: HTTP localhost proxy
The system SHALL provide a localhost HTTP proxy compatible with the handover MVP for datastore GET requests and gated POST/PATCH requests.

#### Scenario: Read-only proxy GET
- **WHEN** the proxy is running with default options and a client requests `GET /datastore/uid` on `127.0.0.1`
- **THEN** the proxy reads the datastore path over USB and returns an `application/json` response

#### Scenario: Writes disabled by default
- **WHEN** the proxy is running without `--allow-writes` and a client sends POST or PATCH
- **THEN** the proxy rejects the request without sending a USB write operation

#### Scenario: Writes enabled explicitly
- **WHEN** the proxy is running with `--allow-writes` and a client sends POST or PATCH with a `json` form field or raw JSON body
- **THEN** the proxy sends a POST datastore operation over USB and returns the response body

### Requirement: Path compatibility
The system SHALL normalize datastore paths using the same compatibility behavior as the handover MVP.

#### Scenario: Bare datastore path
- **WHEN** the user requests `/uid`
- **THEN** the system normalizes the path to `/datastore/uid`

#### Scenario: UID-prefixed datastore path
- **WHEN** the user requests `/<16-hex-character-uid>/datastore/uid`
- **THEN** the system strips the UID prefix and uses `/datastore/uid`

#### Scenario: Root path
- **WHEN** the user requests `/`
- **THEN** the system normalizes the path to `/datastore`

### Requirement: Test coverage
The system SHALL include automated tests for protocol compatibility, path normalization, response extraction, sequence behavior, and HTTP write gating.

#### Scenario: Unit tests run without MOTU hardware
- **WHEN** the automated test suite is run on a development machine without a connected MOTU device
- **THEN** tests that do not require live USB hardware run and validate the same-functionality contract
