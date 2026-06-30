## ADDED Requirements

### Requirement: Mixer model over documented datastore paths
The system SHALL provide a typed mixer model that maps mixer channel strips and buses (`chan`, `main`, `aux`, `group`, `reverb`, `monitor`) to the documented `mix/*` datastore paths using 0-based indexing.

#### Scenario: Compose a channel parameter path
- **WHEN** a caller requests the fader of input channel 0
- **THEN** the system reads the documented `mix/chan/0/matrix/fader` datastore path

#### Scenario: Enumerate available strips and buses
- **WHEN** a caller lists the mixer state of a connected device
- **THEN** the system reports the channels and buses available on that device rather than a hard-coded set

### Requirement: Mixer parameter read and write
The system SHALL read mixer parameters and write read-write mixer parameters within their documented ranges.

#### Scenario: Read a mixer parameter
- **WHEN** a caller reads a mixer parameter such as a channel fader
- **THEN** the system returns the current value from the datastore

#### Scenario: Write a mixer parameter
- **WHEN** a caller sets a read-write mixer parameter to a value within its documented range and writes are enabled
- **THEN** the system writes that value to the corresponding datastore path

#### Scenario: Reject an out-of-range mixer value
- **WHEN** a caller sets a mixer parameter to a value outside its documented range
- **THEN** the system rejects the write and does not change the datastore

### Requirement: Batched mixer writes
The system SHALL support setting multiple mixer parameters in a single datastore write operation.

#### Scenario: Set multiple parameters at once
- **WHEN** a caller sets several mixer parameters in one request
- **THEN** the system composes a single datastore subtree write containing all of the changes
