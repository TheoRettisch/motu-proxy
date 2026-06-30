## 1. Development Dependencies

- [x] 1.1 Add a `dev` optional dependency group to `pyproject.toml` with `pytest` and `ruff`.
- [x] 1.2 Add conservative Ruff configuration targeting Python 3.11+ and the project source, tests, and tools.
- [x] 1.3 Confirm installing without extras still leaves the runtime dependency list empty.

## 2. Continuous Integration

- [x] 2.1 Add a GitHub Actions workflow for push and pull request validation.
- [x] 2.2 Configure the workflow to install `.[dev]` and run `python -m pytest -q`.
- [x] 2.3 Configure the workflow to run `python -m ruff check .`.
- [x] 2.4 Ensure the workflow does not invoke live MOTU USB validation tools.

## 3. Documentation and Verification

- [x] 3.1 Document the development install and local check commands in `README.md`.
- [x] 3.2 Run the hardware-free test suite locally.
- [x] 3.3 Run Ruff locally and address only focused findings needed for the new check.
- [x] 3.4 Validate the OpenSpec change.
