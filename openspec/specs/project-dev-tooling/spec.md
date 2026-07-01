# project-dev-tooling Specification

## Purpose
Define the development dependencies, local hardware-free checks, and GitHub Actions validation baseline for contributor changes.
## Requirements
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
