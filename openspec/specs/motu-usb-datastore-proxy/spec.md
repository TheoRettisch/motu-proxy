# motu-usb-datastore-proxy Specification

## Purpose
Provide a dependency-light CLI and localhost HTTP proxy for MOTU AVB datastore operations over the Linux USB vendor control interface while preserving safe defaults and the class-compliant ALSA audio path.
## Requirements
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

#### Scenario: Serve starts read-only localhost proxy
- **WHEN** the user runs the `serve` command with default options
- **THEN** the system starts the localhost HTTP proxy bound to `127.0.0.1` with HTTP writes disabled

### Requirement: HTTP localhost proxy
The system SHALL provide a localhost HTTP proxy compatible with the handover MVP for datastore GET requests and gated POST/PATCH requests. HTTP PATCH SHALL be treated only as a compatibility alias for the same MOTU datastore POST operation used by HTTP POST, and SHALL NOT imply partial-update semantics.

#### Scenario: Read-only proxy GET
- **WHEN** the proxy is running with default options and a client requests `GET /datastore/uid` on `127.0.0.1`
- **THEN** the proxy reads the datastore path over USB and returns an `application/json` response

#### Scenario: Writes disabled by default
- **WHEN** the proxy is running without `--allow-writes` and a client sends POST or PATCH
- **THEN** the proxy rejects the request without sending a USB write operation

#### Scenario: Writes enabled explicitly
- **WHEN** the proxy is running with `--allow-writes` and a client sends POST or PATCH with a `json` form field or raw JSON body
- **THEN** the proxy sends the same POST datastore operation over USB for either HTTP method and returns the response body

#### Scenario: PATCH does not imply partial update
- **WHEN** the proxy is running with `--allow-writes` and a client sends PATCH with a body
- **THEN** the proxy routes the request through the datastore POST write implementation without applying PATCH-specific merge or partial-update behavior

### Requirement: Optional HTTP write-token protection
The system SHALL enforce HTTP write-token authentication only when token protection is explicitly enabled for `serve` mode. Token protection SHALL be separate from HTTP write enablement: `--allow-writes` SHALL still be required for HTTP POST/PATCH, and Host, Origin, request-body, datastore validation, and frame-size protections SHALL remain active regardless of token configuration.

#### Scenario: Writes enabled without token protection accept local write
- **WHEN** the proxy is running on a loopback address with `--allow-writes` and without token protection enabled
- **AND** a local client sends a valid POST or PATCH without `X-Motu-Proxy-Token` or `Authorization: Bearer`
- **THEN** the proxy sends the datastore POST operation over USB and returns the response body

#### Scenario: Writes remain disabled without allow-writes
- **WHEN** the proxy is running without `--allow-writes`
- **AND** a client sends POST or PATCH, with or without token credentials
- **THEN** the proxy rejects the request without sending a USB write operation

#### Scenario: Token-protected writes reject missing token
- **WHEN** the proxy is running with `--allow-writes` and token protection enabled
- **AND** a client sends POST or PATCH without a valid token credential
- **THEN** the proxy rejects the request with HTTP `403` before sending a USB write operation

#### Scenario: Token-protected writes accept valid token
- **WHEN** the proxy is running with `--allow-writes` and token protection enabled
- **AND** a client sends POST or PATCH with the generated token in `X-Motu-Proxy-Token` or `Authorization: Bearer`
- **THEN** the proxy sends the datastore POST operation over USB and returns the response body

#### Scenario: Token file is generated only for token-protected writes
- **WHEN** `serve` starts with `--allow-writes` and token protection enabled with a token-file path configured
- **THEN** the system writes the generated token to that token file and removes the matching token file during clean shutdown
- **AND** when `serve` starts with `--allow-writes` and token protection is not enabled, the system does not generate or write a token file

#### Scenario: Remote write mode does not imply token protection
- **WHEN** the proxy is running with `--allow-writes` and `--unsafe-allow-remote-writes` without token protection enabled
- **AND** a remote client sends an otherwise-valid POST or PATCH without token credentials
- **THEN** the proxy accepts the request under the explicitly unsafe remote-write mode and sends the datastore POST operation over USB

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

### Requirement: Generalized USB query fields
The system SHALL encode arbitrary GET query parameters with non-empty field names as ordered USB query fields in datastore and meters GET frames, not only the `client` parameter, and SHALL keep the single-`client` GET and POST encodings byte-compatible with prior behavior. Existing `client` validation SHALL remain in force before forwarding `client` as a USB query field.

#### Scenario: Meters query field is encoded
- **WHEN** a meters request carries a `meters=mix/level` query parameter
- **THEN** the system encodes it as a USB query field (name `meters`, value `mix/level`) in the GET frame rather than appending it to the path

#### Scenario: Existing client GET encoding is unchanged
- **WHEN** a request carries only `client=<number>`
- **THEN** the resulting GET frame bytes are identical to the prior single-`client` encoding

#### Scenario: Existing client POST encoding is unchanged
- **WHEN** a POST request carries only `client=<number>`
- **THEN** the resulting POST frame bytes are identical to the prior single-`client` encoding

#### Scenario: Client query validation is preserved
- **WHEN** an HTTP GET request includes a `client` query parameter
- **THEN** the proxy applies the existing 32-bit unsigned integer validation before forwarding it as a USB query field

#### Scenario: Non-client datastore GET query fields are forwarded
- **WHEN** a datastore GET request includes an unknown non-`client` query parameter
- **THEN** the proxy forwards that parameter as a USB query field without validation or interpretation

#### Scenario: Multiple query fields
- **WHEN** a request carries both `meters=mix/level` and `client=<number>` in that order
- **THEN** the system encodes both as query fields in the GET frame in that same order

#### Scenario: Repeated and blank query values are preserved
- **WHEN** a GET request carries repeated non-empty query field names or a blank value, such as `meters=mix/level&meters=ext/input&label=`
- **THEN** the system encodes each query pair as a USB query field in parsed request order, preserving the repeated field names and the blank value

#### Scenario: Empty query field names are rejected
- **WHEN** a GET request includes a query pair with an empty field name, such as `=mix/level`
- **THEN** the proxy rejects the request with an HTTP `400` before issuing a USB request

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

#### Scenario: Meter If-None-Match no-change response is forwarded
- **WHEN** a client sends `GET /meters?meters=mix/level` with an `If-None-Match` header and the device returns no meter change
- **THEN** the proxy returns the device's no-change status and ETag without consulting datastore long-poll state
- **AND** the response body handling matches the device response without synthesizing meter data

### Requirement: Meters pass-through without interpretation or polling
The system SHALL pass meter requests and responses through without interpreting meter values, mapping channels, or running a background meter poll loop. Continuous polling and any typed meter model are out of scope for the proxy.

#### Scenario: One device request per meters request
- **WHEN** the proxy serves a meters request
- **THEN** it issues exactly one device meters request and does not start a background poll loop

#### Scenario: Meter values are returned unchanged
- **WHEN** the proxy returns a meters response
- **THEN** it returns the device's meter values unchanged, without converting, normalizing, or channel-mapping them

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

#### Scenario: CLI post validates before USB write
- **WHEN** the user runs CLI `post` with a value that violates the documented type, range, enum, or permission for a known path
- **THEN** the command fails with a nonzero exit and does not send a USB write

### Requirement: Unknown datastore write protection
The system SHALL reject writes to datastore paths that are not present in its embedded schema while validation is enabled, SHALL provide an explicit option to allow unknown paths while keeping validation for known paths, and SHALL provide a way to disable validation entirely.

#### Scenario: Undocumented path is rejected by default
- **WHEN** a client writes to a datastore path that is not in the embedded schema and writes are enabled
- **THEN** the proxy rejects the request with an HTTP `422` and does not send a USB write

#### Scenario: Undocumented path is explicitly allowed
- **WHEN** HTTP or CLI writes are run with unknown writes explicitly allowed and a client writes to a datastore path that is not in the embedded schema
- **THEN** the system forwards the write to the device while continuing to validate documented paths

#### Scenario: Validation disabled
- **WHEN** HTTP or CLI writes are run with validation disabled
- **THEN** the system forwards writes without checking type, range, enum, or permission

### Requirement: Datastore long-polling
The system SHALL support datastore long-polling by accepting a client ETag via HTTP `If-None-Match`, comparing it against coordinated datastore state maintained by USB long-polling, and returning either the changed datastore payload with the new ETag or `304 Not Modified` when no change occurs within the wait window.

#### Scenario: No change within the wait window
- **WHEN** a client issues a long-poll GET with an `If-None-Match` ETag equal to the current datastore ETag and no datastore change occurs within the wait window
- **THEN** the proxy returns `304 Not Modified` with the unchanged ETag

#### Scenario: Change during the wait window
- **WHEN** a client issues a long-poll GET with an `If-None-Match` ETag and the datastore changes during the wait window
- **THEN** the proxy returns the changed datastore payload and the new ETag

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

### Requirement: Path compatibility
The system SHALL normalize datastore paths using the same compatibility behavior as the handover MVP.

#### Scenario: Datastore path pass-through
- **WHEN** the user requests `/datastore/uid`
- **THEN** the system uses `/datastore/uid` without adding another datastore prefix

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

### Requirement: Capability and version discovery
The system SHALL provide a command that reports the device datastore `apiversion`, the per-section capability versions, and device identity read from the documented datastore paths.

#### Scenario: Report API and section versions
- **WHEN** the user runs the discovery command against a connected MOTU device
- **THEN** the system reports the global `apiversion` and the available `ext/caps/avb`, `ext/caps/router`, and `ext/caps/mixer` section versions

#### Scenario: Absent section reported as not present
- **WHEN** a section capability path such as `ext/caps/mixer` does not exist on the device
- **THEN** the system reports that section as not present rather than failing

#### Scenario: Machine-readable output
- **WHEN** the user runs the discovery command with JSON output
- **THEN** the system emits the API version, section versions, and device identity as a JSON object

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

### Requirement: HTTP datastore hotplug recovery
The system SHALL keep the HTTP proxy process alive across MOTU USB datastore device disconnects, power-cycles, and USB resets, SHALL retry discovery/open/init until the configured device is available again, and SHALL resume datastore service without requiring a process restart.

#### Scenario: Foreground request while device is unavailable
- **WHEN** the HTTP proxy is running and the configured MOTU datastore control interface is disconnected, unavailable, or still reconnecting
- **THEN** a foreground HTTP datastore request returns `503 Service Unavailable` and the proxy process remains running

#### Scenario: Service resumes after device returns
- **WHEN** the configured MOTU datastore control interface becomes available again after a disconnect, power-cycle, or USB reset
- **THEN** the proxy rediscovers the device, reopens the vendor-specific bulk control interface, initializes the datastore session, and subsequent HTTP datastore requests can succeed without restarting the proxy process

#### Scenario: Reconnect resets coordinated datastore history
- **WHEN** the proxy recovers after a device disconnect, power-cycle, or USB reset
- **THEN** it discards stale datastore ETag and delta history from the previous USB session
- **AND** it resumes coordination from a fresh datastore read on the new session

#### Scenario: Foreground reconnect attempts are bounded
- **WHEN** a foreground HTTP datastore request arrives while the configured device is unavailable or still inside reconnect backoff
- **THEN** the request performs no more than one prompt opportunistic open attempt before returning `503 Service Unavailable`
- **AND** it does not keep the HTTP worker blocked in an unbounded reconnect loop

#### Scenario: No implicit write replay after reconnect
- **WHEN** an HTTP write fails because the datastore device is lost before a valid write response is received
- **THEN** the proxy returns an error for that request and does not replay the write automatically after reconnect

#### Scenario: Recovery preserves single control-interface ownership
- **WHEN** the proxy is reconnecting or serving requests after recovery
- **THEN** it uses at most one active vendor-specific datastore control session at a time and does not claim class-compliant ALSA audio interfaces

#### Scenario: Reconnect honors configured device selection
- **WHEN** the proxy was started with VID, PID, serial, interface, or endpoint selection options
- **THEN** reconnect attempts use the same selection constraints and remain unavailable if the matching device cannot be selected unambiguously

#### Scenario: Status reports reconnect state
- **WHEN** a client requests `GET /__motu_proxy/status` while the device is unavailable, reconnecting, or recovered
- **THEN** the status response reports whether a usable datastore session is available
- **AND** it includes the last reconnect error and retry/backoff state when applicable
- **AND** it is served through the status fast path without performing a datastore read
