## ADDED Requirements

### Requirement: Expanded Ruff lint baseline
The system SHALL configure Ruff to check a broader lint baseline that includes `E4`, `E7`, `E9`, `F`, `B`, `I`, `UP`, `SIM`, and `RUF`.

#### Scenario: Local lint uses expanded rules
- **WHEN** a contributor runs the documented Ruff check command in a development environment
- **THEN** Ruff evaluates the repository using the expanded rule baseline

#### Scenario: CI lint uses expanded rules
- **WHEN** GitHub Actions runs the Ruff check
- **THEN** it evaluates the repository using the same expanded rule baseline

#### Scenario: Runtime dependency set remains unchanged
- **WHEN** the package is installed without development extras
- **THEN** the runtime dependency list remains empty
