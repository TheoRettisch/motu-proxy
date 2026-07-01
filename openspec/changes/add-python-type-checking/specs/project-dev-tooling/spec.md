## ADDED Requirements

### Requirement: Static type-checking validation
The system SHALL provide a Python static type-checking command as part of hardware-free development validation.

#### Scenario: Type checker installed with development tools
- **WHEN** a contributor installs the project with its development extra
- **THEN** the environment includes the configured static type checker for local validation

#### Scenario: Type checks run locally
- **WHEN** a contributor runs the documented static type-check command in a development environment
- **THEN** the repository's Python package, tests, and tools are checked without requiring an attached MOTU device

#### Scenario: Runtime dependency set remains unchanged
- **WHEN** the package is installed without development extras
- **THEN** the runtime dependency list remains empty

### Requirement: CI static type-checking
The system SHALL run the configured static type checker in GitHub Actions for pushes and pull requests.

#### Scenario: Pull request type validation
- **WHEN** a pull request updates repository code
- **THEN** GitHub Actions installs development dependencies and runs the static type checker

#### Scenario: Push type validation
- **WHEN** code is pushed to a CI-enabled branch
- **THEN** GitHub Actions runs the static type checker as part of the hardware-free checks

#### Scenario: CI type checks avoid live USB hardware
- **WHEN** the GitHub Actions workflow runs the static type checker
- **THEN** it does not require a connected MOTU device or USB permissions
