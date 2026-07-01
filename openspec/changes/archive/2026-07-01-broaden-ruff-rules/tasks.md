## 1. Ruff Configuration

- [x] 1.1 Update `pyproject.toml` Ruff `select` to include `E4`, `E7`, `E9`, `F`, `B`, `I`, `UP`, `SIM`, and `RUF`.
- [x] 1.2 Keep the development extra and runtime dependency list unchanged.
- [x] 1.3 Keep Python 3.13 CI support out of scope for this lint-policy change.

## 2. Lint Cleanup

- [x] 2.1 Run `.venv/bin/python -m ruff check .` and review all new findings.
- [x] 2.2 Apply targeted code fixes for clear correctness, import hygiene, modernization, and simplification findings.
- [x] 2.3 Add narrow per-file or per-line ignores only for intentional patterns where the rule remains valuable globally, and include a short reason for each suppression.

## 3. Verification

- [x] 3.1 Run `.venv/bin/python -m ruff check .` and confirm it passes with the expanded baseline.
- [x] 3.2 Run `.venv/bin/python -m pytest -q` and confirm hardware-free tests still pass.
- [x] 3.3 Review the diff to ensure changes are limited to lint configuration and lint-driven cleanup.
