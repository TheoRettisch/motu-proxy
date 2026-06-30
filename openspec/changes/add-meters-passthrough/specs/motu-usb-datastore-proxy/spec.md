## ADDED Requirements

### Requirement: Generalized USB query fields
The system SHALL encode arbitrary GET query parameters as ordered USB query fields in datastore and meters GET frames, not only the `client` parameter, and SHALL keep the single-`client` encoding byte-compatible with prior behavior. Existing `client` validation SHALL remain in force before forwarding `client` as a USB query field.

#### Scenario: Meters query field is encoded
- **WHEN** a meters request carries a `meters=mix/level` query parameter
- **THEN** the system encodes it as a USB query field (name `meters`, value `mix/level`) in the GET frame rather than appending it to the path

#### Scenario: Existing client encoding is unchanged
- **WHEN** a request carries only `client=<number>`
- **THEN** the resulting GET frame bytes are identical to the prior single-`client` encoding

#### Scenario: Client query validation is preserved
- **WHEN** an HTTP GET request includes a `client` query parameter
- **THEN** the proxy applies the existing 32-bit unsigned integer validation before forwarding it as a USB query field

#### Scenario: Non-client datastore GET query fields are forwarded
- **WHEN** a datastore GET request includes an unknown non-`client` query parameter
- **THEN** the proxy forwards that parameter as a USB query field without validation or interpretation

#### Scenario: Multiple query fields
- **WHEN** a request carries both `meters=mix/level` and `client=<number>` in that order
- **THEN** the system encodes both as query fields in the GET frame in that same order

### Requirement: Meters resource routing
The system SHALL treat `/meters` as a top-level resource and SHALL NOT add a `/datastore` prefix to it.

#### Scenario: Meters path pass-through
- **WHEN** a client requests `/meters`
- **THEN** the system uses `/meters` unchanged, without adding a `/datastore` prefix

#### Scenario: Datastore routing is unaffected
- **WHEN** a client requests a bare datastore path such as `/uid`
- **THEN** the system still normalizes it to `/datastore/uid`

### Requirement: Meters request bridging
The system SHALL forward an HTTP `GET /meters?meters=<group>` to the device over USB as a single `/meters` request carrying the `meters` query field, and SHALL return the device's meter response with its ETag exposed in the `ETag` header.

#### Scenario: HTTP meters read returns the device frame
- **WHEN** a client sends `GET /meters?meters=mix/level` to the proxy
- **THEN** the proxy issues the corresponding USB meters request and returns the device's meter response body with the meter ETag in the `ETag` header

#### Scenario: Meters query is not appended to the USB path
- **WHEN** a client sends `GET /meters?meters=mix/level` to the proxy
- **THEN** the USB request path is `/meters`
- **AND** `meters=mix/level` is encoded only as a USB query field

#### Scenario: Unrecognized meter group is forwarded
- **WHEN** a client requests a meter group the proxy does not recognize
- **THEN** the proxy forwards the group value to the device unchanged, without validation or interpretation

#### Scenario: Meter If-None-Match is forwarded to the device
- **WHEN** a client sends `GET /meters?meters=mix/level` with an `If-None-Match` header
- **THEN** the proxy forwards that ETag to the device in a single USB meters request and does not wait on datastore long-poll history
- **AND** the datastore coordinator wait path is not invoked

### Requirement: Meters pass-through without interpretation or polling
The system SHALL pass meter requests and responses through without interpreting meter values, mapping channels, or running a background meter poll loop. Continuous polling and any typed meter model are out of scope for the proxy.

#### Scenario: One device request per meters request
- **WHEN** the proxy serves a meters request
- **THEN** it issues exactly one device meters request and does not start a background poll loop

#### Scenario: Meter values are returned unchanged
- **WHEN** the proxy returns a meters response
- **THEN** it returns the device's meter values unchanged, without converting, normalizing, or channel-mapping them
