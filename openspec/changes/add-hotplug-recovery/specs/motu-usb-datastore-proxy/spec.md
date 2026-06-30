## ADDED Requirements

### Requirement: HTTP datastore hotplug recovery
The system SHALL keep the HTTP proxy process alive across MOTU USB datastore device disconnects, power-cycles, and USB resets, SHALL retry discovery/open/init until the configured device is available again, and SHALL resume datastore service without requiring a process restart.

#### Scenario: Foreground request while device is unavailable
- **WHEN** the HTTP proxy is running and the configured MOTU datastore control interface is disconnected, unavailable, or still reconnecting
- **THEN** a foreground HTTP datastore request returns `503 Service Unavailable` and the proxy process remains running

#### Scenario: Service resumes after device returns
- **WHEN** the configured MOTU datastore control interface becomes available again after a disconnect, power-cycle, or USB reset
- **THEN** the proxy rediscovers the device, reopens the vendor-specific bulk control interface, initializes the datastore session, and subsequent HTTP datastore requests can succeed without restarting the proxy process

#### Scenario: No implicit write replay after reconnect
- **WHEN** an HTTP write fails because the datastore device is lost before a valid write response is received
- **THEN** the proxy returns an error for that request and does not replay the write automatically after reconnect

#### Scenario: Recovery preserves single control-interface ownership
- **WHEN** the proxy is reconnecting or serving requests after recovery
- **THEN** it uses at most one active vendor-specific datastore control session at a time and does not claim class-compliant ALSA audio interfaces

#### Scenario: Reconnect honors configured device selection
- **WHEN** the proxy was started with VID, PID, serial, interface, or endpoint selection options
- **THEN** reconnect attempts use the same selection constraints and remain unavailable if the matching device cannot be selected unambiguously
