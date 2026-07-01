## ADDED Requirements

### Requirement: Production systemd service packaging
The system SHALL provide production service packaging artifacts for running `motu-proxy serve` under systemd with read-only localhost behavior by default and operator-configurable CLI options.

#### Scenario: Default service starts read-only localhost proxy
- **WHEN** an operator installs the packaged service unit and starts it with the default environment
- **THEN** the service runs `motu-proxy serve` bound to `127.0.0.1` with HTTP writes disabled

#### Scenario: Device selection is configurable
- **WHEN** an operator configures service environment options for VID, PID, serial, interface, endpoint, timeout, or debug behavior
- **THEN** the service passes those options to `motu-proxy serve` without requiring edits to the installed unit file

#### Scenario: Write mode requires explicit opt-in
- **WHEN** the packaged service is installed with default configuration
- **THEN** HTTP POST and PATCH remain disabled
- **AND** enabling HTTP writes requires an explicit service configuration change
- **AND** requiring HTTP write tokens requires a separate explicit service configuration option

### Requirement: Service-managed token runtime directory
The system SHALL use service-manager runtime directory handling for the optional HTTP write-token path and SHALL avoid exposing generated write tokens in service logs by default when token protection is enabled.

#### Scenario: Runtime directory is created for token file
- **WHEN** the service starts under systemd
- **THEN** `/run/motu-proxy` is available with owner-only permissions suitable for `/run/motu-proxy/write-token`

#### Scenario: Token is not printed to journald by default
- **WHEN** the service starts with HTTP writes and token protection enabled with a token file configured
- **THEN** the generated token is written to the token file and is not printed in normal service logs

#### Scenario: Token file is removed on clean service stop
- **WHEN** systemd stops the service cleanly after the application generated a write-token file
- **THEN** the application cleanup path removes the token file if it still contains the generated token

### Requirement: Supervised service lifecycle
The system SHALL support service-manager start, restart, and stop lifecycle behavior without bypassing HTTP server, coordinator, or token cleanup.

#### Scenario: SIGTERM performs clean shutdown
- **WHEN** systemd sends the service a normal stop signal
- **THEN** `serve` exits its HTTP loop, closes the coordinator/server path, and runs token cleanup before process exit

#### Scenario: Service restarts after process failure
- **WHEN** the service process exits because of an unhandled failure
- **THEN** the packaged unit requests a bounded systemd restart rather than leaving the proxy permanently stopped

#### Scenario: Manual CLI behavior remains unchanged
- **WHEN** a user runs one-shot CLI commands such as `get`, `post`, `probe`, `smoke`, or `selftest`
- **THEN** those commands keep their existing command-line behavior and are not coupled to systemd

### Requirement: Service hardening preserves USB datastore access
The system SHALL provide systemd hardening directives that reduce service privileges while preserving sysfs discovery, usbfs access to the vendor-specific datastore interface, and non-ownership of ALSA audio interfaces.

#### Scenario: Hardening does not hide USB device nodes
- **WHEN** the packaged unit hardening is active
- **THEN** the service can still read sysfs USB descriptors and open the selected `/dev/bus/usb` device for vendor-specific datastore control

#### Scenario: ALSA audio interfaces remain undisturbed
- **WHEN** the service starts under the packaged unit
- **THEN** device discovery still selects only the unbound vendor-specific bulk control interface and does not claim class-compliant ALSA audio interfaces

#### Scenario: Filesystem write access is constrained
- **WHEN** the packaged unit hardening is active
- **THEN** the service writable filesystem surface is limited to required runtime paths such as `/run/motu-proxy`

### Requirement: Production service operations documentation
The system SHALL document production service installation, configuration, status checks, logs, rollback, and validation against live MOTU hardware.

#### Scenario: Operator installs service from repository artifacts
- **WHEN** an operator follows the production service documentation
- **THEN** they can install the service unit and environment file, reload systemd, start the service, and confirm its status

#### Scenario: Operator checks proxy health
- **WHEN** the service is running
- **THEN** documentation shows how to verify process health, service logs, `/__motu_proxy/status`, and a harmless datastore read such as `/datastore/uid`

#### Scenario: Live validation uses vendor datastore interface only
- **WHEN** production service validation is performed against the known MOTU hardware host
- **THEN** validation uses harmless read paths through the vendor-specific datastore interface and does not disturb ALSA audio ownership

#### Scenario: Operator rolls back service packaging
- **WHEN** service packaging causes a deployment problem
- **THEN** documentation shows how to stop and disable the service and return to manual `motu-proxy serve` execution
