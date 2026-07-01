## Why

The repository now has protocol framing, typed callback protocols, and threaded coordination paths where static type checking can catch regressions before review or hardware validation. Ruff and pytest cover syntax, lint, and behavior, but they do not verify cross-module type contracts.

## What Changes

- Add a Python static type-checking tool to the development validation set.
- Configure the checker for the repository's Python 3.11+ code while keeping the runtime dependency set empty.
- Document a local type-check command alongside pytest and Ruff.
- Run the same hardware-free type check in GitHub Actions for pushes and pull requests.
- Keep initial strictness practical for the current codebase and avoid broad runtime or API refactors.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-dev-tooling`: Add static type checking to the declared development dependencies, local validation commands, and GitHub Actions hardware-free checks.

## Impact

- Affects `pyproject.toml` development dependencies and type-checker configuration.
- Affects `.github/workflows/ci.yml` hardware-free checks.
- Affects README development check documentation.
- May require targeted typing cleanup in `motu_proxy`, tests, or tools.
- Does not affect runtime dependencies, USB protocol behavior, HTTP API behavior, or live hardware requirements.
