## ADDED Requirements

### Requirement: HTTP/1.1 persistent connections
The system SHALL serve datastore proxy responses using HTTP/1.1 and SHALL keep connections eligible for reuse when the request and response are safely framed.

#### Scenario: Handler advertises HTTP/1.1
- **WHEN** the proxy sends a datastore HTTP response
- **THEN** the response protocol version is HTTP/1.1

#### Scenario: Successful datastore response is length framed
- **WHEN** a client issues a successful datastore GET request over HTTP/1.1
- **THEN** the proxy response includes an accurate `Content-Length` header
- **AND** the proxy does not send `Connection: close` solely because the request completed successfully

#### Scenario: Client-requested close is respected
- **WHEN** a client issues an HTTP/1.1 datastore request with `Connection: close`
- **THEN** the proxy marks the connection for closure after the response
- **AND** this close behavior does not depend on the datastore request failing

#### Scenario: Not modified response is bodyless
- **WHEN** a long-poll datastore GET returns `304 Not Modified`
- **THEN** the proxy response omits `Content-Length` unless the selected representation length is known and correct
- **AND** the proxy does not send a response body

#### Scenario: Unsafe write rejection closes the connection
- **WHEN** a POST or PATCH request is rejected before or during request-body handling
- **THEN** the proxy response includes `Connection: close`
- **AND** the connection is not reused for another request

#### Scenario: Request body read failure closes the connection
- **WHEN** a write request body times out, ends before `Content-Length` bytes, or uses an unsupported transfer encoding
- **THEN** the proxy marks the connection for closure
- **AND** the response includes `Connection: close`

#### Scenario: Unsupported methods are length framed
- **WHEN** a client sends an unsupported HTTP method over HTTP/1.1
- **THEN** the proxy error response includes an explicit `Content-Length`
- **AND** the response can be parsed without relying on connection close as the frame boundary
