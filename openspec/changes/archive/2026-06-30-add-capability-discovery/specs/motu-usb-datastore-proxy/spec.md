## ADDED Requirements

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
