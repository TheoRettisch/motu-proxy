## Context

`motu-proxy` has a small dependency-free runtime and a growing hardware-free test suite. Local development currently relies on contributors knowing to install pytest out of band, and there is no CI workflow to run tests before changes are merged. The project also has no declared lint tool, so style checks are informal.

## Goals / Non-Goals

**Goals:**

- Declare the development-only packages needed for tests and linting without adding runtime dependencies.
- Add a minimal GitHub Actions workflow that runs hardware-free checks on pushes and pull requests.
- Keep CI independent of attached MOTU hardware, USB device permissions, and live validation scripts.
- Make lint configuration explicit enough that contributors and CI use the same rules.

**Non-Goals:**

- Do not add runtime dependencies to the installed `motu-proxy` package.
- Do not run live USB/MOTU validation in CI.
- Do not introduce packaging, release, coverage-upload, or deployment automation in this change.
- Do not reformat the entire codebase unless Ruff configuration requires a targeted cleanup.

## Decisions

### Use an optional `dev` dependency group

`pyproject.toml` will define `[project.optional-dependencies] dev = ["pytest", "ruff"]`. This keeps the production package dependency-free while giving contributors a standard install path: `python -m pip install -e ".[dev]"`.

Alternative considered: add a `requirements-dev.txt`. Rejected because the project already uses `pyproject.toml` for packaging metadata, and optional dependencies are discoverable by Python packaging tools.

### Use Ruff as the first lint tool

Ruff provides fast static checks with a single development dependency. The first configuration should be conservative: target Python 3.11+, check the package, tests, and tools, and avoid broad style churn.

Alternative considered: add Black, isort, mypy, and Ruff together. Rejected because that is a larger policy decision; this change should establish the smallest useful dev tooling baseline.

### Run CI only for hardware-free checks

The GitHub Actions workflow will install dev dependencies and run `python -m pytest -q`. It may also run `python -m ruff check .` once Ruff configuration is present. It will not run `tools/live_validate_response_frames.py` against a device because that requires a reachable MOTU and USB permissions.

Alternative considered: add opt-in live validation behind secrets or self-hosted runners. Rejected for this change because the current need is basic regression feedback for ordinary pull requests.

### Test supported Python versions

The workflow should run on Python versions supported by `requires-python >=3.11`, starting with 3.11 and 3.12 as the baseline. Additional versions can be added later once the project wants to track newest interpreter behavior explicitly.

## Risks / Trade-offs

- CI runtime increases slightly -> Keep checks minimal and hardware-free.
- Ruff may flag existing code -> Start with conservative rules and fix only actionable findings in scope.
- GitHub Actions version pinning can age -> Use stable major-version action references and keep future upgrades routine.
- Local and CI environments may differ -> Prefer `python -m pytest` and `python -m ruff` so both use the active interpreter environment.

## Migration Plan

1. Add optional development dependencies and Ruff configuration to `pyproject.toml`.
2. Add a GitHub Actions workflow under `.github/workflows/`.
3. Run the local test suite and Ruff check.
4. Adjust only focused issues needed to make the new checks pass.

Rollback is straightforward: remove the workflow and optional dependency/configuration additions.

## Open Questions

- Should CI include Python 3.13 immediately, or wait until local development has exercised it?
- Should Ruff formatting (`ruff format`) become mandatory later, or should this change only introduce lint checks?
