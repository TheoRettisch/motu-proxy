## ADDED Requirements

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
