## ADDED Requirements

### Requirement: Datastore ETag exposure
The system SHALL expose the device datastore ETag on HTTP GET responses so that datastore clients can detect change versions.

#### Scenario: ETag returned on read
- **WHEN** a client issues an HTTP GET for a datastore path and the device reply carries a datastore ETag
- **THEN** the proxy includes that ETag value in the HTTP `ETag` response header

#### Scenario: Cache headers match native API
- **WHEN** the proxy returns a datastore GET response
- **THEN** the response includes `Cache-Control: no-cache` consistent with the documented MOTU datastore API

### Requirement: Datastore response shape fidelity
The system SHALL return datastore GET responses in the shapes defined by the MOTU datastore API: a single key as a `value` object, a subtree as a nested object, and the full datastore as a single object.

#### Scenario: Single key read
- **WHEN** a client reads a single datastore key such as `/datastore/uid`
- **THEN** the response body is a JSON object of the form `{"value": ...}`

#### Scenario: Subtree read
- **WHEN** a client reads a datastore subtree such as `/datastore/mix/chan/0/gate`
- **THEN** the response body is the nested JSON object for that subtree

### Requirement: HTTP client identifier passthrough
The system SHALL accept the `client` query-string parameter on HTTP reads and writes and forward it to the datastore operation.

#### Scenario: Client identifier forwarded
- **WHEN** a client issues a datastore request with a `client=<number>` query parameter
- **THEN** the proxy forwards that client identifier to the underlying datastore read or write
