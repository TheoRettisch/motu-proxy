## Why

The test suite has grown beyond a one-off hardware-free check, but the repository does not yet declare the tools needed to run it consistently or verify it in CI. Adding lightweight dev dependencies and a GitHub Actions test workflow makes regressions visible before they reach live hardware work.

## What Changes

- Add a `dev` optional dependency group with `pytest` and `ruff`.
- Add minimal Ruff configuration so formatting/lint expectations are explicit and reproducible.
- Add a GitHub Actions workflow that installs the project with dev dependencies and runs the hardware-free test suite on supported Python versions.
- Keep live MOTU USB validation out of CI because it requires attached hardware and elevated USB access.

## Capabilities

### New Capabilities
- `project-dev-tooling`: Development and CI tooling required to run automated checks for the project.

### Modified Capabilities
- None.

## Impact

- Affected files: `README.md`, `pyproject.toml`, `.github/workflows/*.yml`.
- Affected systems: local contributors and GitHub pull request/push validation.
- Dependencies: optional development-only packages `pytest` and `ruff`; no new runtime dependency.
