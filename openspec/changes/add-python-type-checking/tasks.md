## 1. Tooling Configuration

- [ ] 1.1 Add `mypy` to the `dev` optional dependency set without changing runtime dependencies.
- [ ] 1.2 Add `[tool.mypy]` configuration in `pyproject.toml` for Python 3.11+ and the checked paths.
- [ ] 1.3 Keep the initial mypy baseline practical rather than fully strict, with any exclusions or overrides narrowly scoped.

## 2. Local and CI Integration

- [ ] 2.1 Document `.venv/bin/python -m mypy motu_proxy tests tools` in the README development checks.
- [ ] 2.2 Add the same mypy command to the GitHub Actions hardware-free workflow.
- [ ] 2.3 Ensure the CI step runs without live MOTU hardware, USB permissions, or runtime dependency changes.

## 3. Type Cleanup

- [ ] 3.1 Run `.venv/bin/python -m mypy motu_proxy tests tools` and review all findings.
- [ ] 3.2 Apply targeted typing fixes for clear type contract, optional-value, context-manager, callable, or Protocol issues.
- [ ] 3.3 Add narrow ignores or config overrides only for intentional dynamic patterns that should remain unchanged.

## 4. Verification

- [ ] 4.1 Run `.venv/bin/python -m mypy motu_proxy tests tools` and confirm it passes.
- [ ] 4.2 Run `.venv/bin/python -m ruff check .` and confirm it still passes.
- [ ] 4.3 Run `.venv/bin/python -m pytest -q` and confirm hardware-free tests still pass.
- [ ] 4.4 Review the diff to confirm the runtime dependency list remains empty and implementation changes are limited to type-checking adoption.
