## Why

The current Ruff configuration intentionally catches only syntax, import, and undefined-name classes of problems. After the first hardening pass, the codebase is ready to consider a broader lint baseline that can catch maintainability and likely-bug patterns before review.

## What Changes

- Expand the configured Ruff rule families beyond `E4`, `E7`, `E9`, and `F`.
- Start with rule groups that provide practical value for this repository: bug-prone patterns, import hygiene, Python modernization, simplifications, and Ruff-specific checks.
- Fix or explicitly suppress any newly reported findings in a focused implementation patch.
- Keep the runtime dependency set unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-dev-tooling`: Broaden the repository's Ruff lint baseline while preserving hardware-free local and CI validation.

## Impact

- Affects `pyproject.toml` Ruff configuration.
- May require targeted cleanup in Python package, tests, or tools if new rules report existing issues.
- Affects contributor lint expectations and GitHub Actions results.
- Does not affect runtime dependencies, USB protocol behavior, HTTP API behavior, or live hardware requirements.
