## ADDED Requirements

### Requirement: Development dependency declaration
The system SHALL declare development-only dependencies needed to run tests and lint checks without adding runtime dependencies to the installed proxy package.

#### Scenario: Install development tools
- **WHEN** a contributor installs the project with its development extra
- **THEN** the environment includes pytest and Ruff for local validation

#### Scenario: Runtime dependency set unchanged
- **WHEN** the package is installed without development extras
- **THEN** the runtime dependency list remains empty

### Requirement: Hardware-free automated checks
The system SHALL provide standard local commands for hardware-free validation of the Python package, tests, and tooling.

#### Scenario: Unit tests run locally
- **WHEN** a contributor runs the documented pytest command in a development environment
- **THEN** hardware-free tests execute without requiring an attached MOTU device

#### Scenario: Lint checks run locally
- **WHEN** a contributor runs the documented Ruff check command in a development environment
- **THEN** the repository is checked using the project's Ruff configuration

### Requirement: GitHub CI workflow
The system SHALL run hardware-free validation in GitHub Actions for pushes and pull requests.

#### Scenario: Pull request validation
- **WHEN** a pull request updates repository code
- **THEN** GitHub Actions installs development dependencies and runs the hardware-free test suite

#### Scenario: Pull request lint validation
- **WHEN** a pull request updates repository code
- **THEN** GitHub Actions runs `python -m ruff check .` using the project's Ruff configuration

#### Scenario: Supported Python versions are checked
- **WHEN** GitHub Actions runs hardware-free validation
- **THEN** it runs on Python 3.11 and 3.12

#### Scenario: Push validation
- **WHEN** code is pushed to a CI-enabled branch
- **THEN** GitHub Actions installs development dependencies and runs the hardware-free checks

#### Scenario: CI avoids live USB hardware
- **WHEN** the GitHub Actions workflow runs
- **THEN** it does not require a connected MOTU device or USB permissions
