## Context

The repository currently uses Ruff as its only linting tool, configured with a deliberately narrow rule set: `E4`, `E7`, `E9`, and `F`. That catches syntax and undefined-name failures without forcing style cleanup during initial tooling adoption.

The codebase now has hardware-free tests, a local Ruff command, and GitHub Actions coverage on supported Python versions. Broadening Ruff is therefore a tooling policy change rather than a runtime behavior change. The implementation should keep the package dependency-light and avoid touching live hardware paths.

## Goals / Non-Goals

**Goals:**

- Add higher-signal Ruff rule families that catch likely bugs and maintainability issues.
- Keep the rule expansion understandable for contributors.
- Fix newly reported issues in focused patches, or add narrow per-file ignores when a rule is valuable globally but noisy in a specific context.
- Preserve the existing hardware-free validation workflow.

**Non-Goals:**

- Do not introduce mandatory formatting, Black, mypy, or additional lint tools.
- Do not perform broad unrelated refactors just to satisfy style preferences.
- Do not change runtime dependencies or MOTU USB behavior.
- Do not require live hardware in CI.

## Decisions

Enable rule families in one implementation step: `B`, `I`, `UP`, `SIM`, and `RUF`.

Rationale: these match the review finding and cover bug-prone constructs, import organization, modern Python idioms, simplifications, and Ruff-specific correctness checks. The project targets Python 3.11+, so `UP` can be useful without forcing compatibility with older interpreters.

Alternative considered: enable only one family at a time. Rejected because this is still a small repository and a single focused cleanup should keep review overhead lower than several tiny policy changes.

Use targeted fixes before ignores.

Rationale: if Ruff reports simple, local issues, updating code is clearer than carrying suppressions. Suppressions should be reserved for cases where a rule conflicts with an intentional test shape, command-line script pattern, or hardware-protocol clarity.

Alternative considered: add broad per-file ignores for tests or tools first. Rejected because it weakens the purpose of broadening the lint baseline before seeing actual findings.

Leave formatter adoption out of scope.

Rationale: `ruff format` or Black would create a separate formatting contract and can produce larger diffs. This change should focus on lint findings that improve correctness and maintainability.

Alternative considered: add formatting together with lint broadening. Rejected because formatting policy deserves a separate discussion.

## Risks / Trade-offs

- New rules may produce noisy findings -> Prefer targeted fixes and narrow ignores, and remove any proposed rule family if it creates broad churn without practical value.
- Import sorting can touch many files -> Keep changes mechanical and verify with tests and Ruff.
- Modernization rules may reduce explicitness in protocol code -> Keep readability where binary protocol intent is clearer than compactness, using narrow ignores if needed.
- Contributors may see new CI failures after the change -> Document the expanded rule set in `pyproject.toml` and rely on the existing local Ruff command.

## Migration Plan

1. Update `pyproject.toml` Ruff `select` to include the expanded rule families.
2. Run `.venv/bin/python -m ruff check .`.
3. Apply targeted fixes or narrow suppressions for findings.
4. Run `.venv/bin/python -m pytest -q` and `.venv/bin/python -m ruff check .`.
5. CI will enforce the same command on future pushes and pull requests.

Rollback is straightforward: revert the Ruff rule selection and associated lint-only cleanup changes.

## Open Questions

- Should Python 3.13 be added to the CI matrix in this same implementation, or handled as a separate CI-support change?
