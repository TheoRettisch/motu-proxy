## Context

The project currently has a small Python-only runtime dependency surface, a development extra for pytest and Ruff, and a GitHub Actions workflow that runs hardware-free checks on Python 3.11 and 3.12. The codebase uses type annotations throughout important protocol and coordination paths, but those annotations are not checked in local validation or CI.

Recent review found static type checking valuable because the code contains protocol framing, callback protocols, optional metadata, context managers, and threaded state coordination. This change is a development-tooling policy change; it should not change USB behavior, HTTP behavior, runtime dependencies, or live hardware requirements.

## Goals / Non-Goals

**Goals:**

- Add one mandatory static type-checking command to local and CI validation.
- Keep type-checking dependencies in the development extra only.
- Start from a practical strictness level that can be made green without broad refactors.
- Configure the checker from `pyproject.toml` where possible.
- Document the command in README development checks.

**Non-Goals:**

- Do not add runtime dependencies.
- Do not require live MOTU hardware or USB permissions.
- Do not introduce formatting changes or broad style refactors.
- Do not add both mypy and pyright in the first implementation.
- Do not expand the supported Python CI matrix as part of this change.

## Decisions

Use mypy as the initial checker.

Rationale: mypy runs cleanly through the existing repo-local Python virtual environment pattern (`.venv/bin/python -m mypy ...`) and can be installed through the existing development extra without introducing a Node.js dependency or separate package manager expectation. It is mature, CI-friendly, and works well for gradually typed libraries.

Alternative considered: use pyright. Pyright is fast and often strong at inference, but the common installation path introduces Node tooling or a Python wrapper around it. That is workable, but it is a larger tooling surface than this repository currently has. Pyright remains a good future replacement or complementary advisory check if mypy proves too awkward for the protocol-heavy code.

Run mypy over `motu_proxy`, `tests`, and `tools`.

Rationale: checking only package code would miss helper Protocols, fake transports, and live-validation tools that exercise important API contracts. Including tests and tools makes type-checking regressions visible where the codebase already documents behavior.

Alternative considered: check only `motu_proxy` first. That would be easier to make green, but it would leave the same callable dispatch and fake hardware contracts unchecked in tests.

Start with a practical baseline rather than maximum strictness.

Rationale: the first implementation should make type checking part of every contributor's normal loop. Enabling maximum strictness immediately can turn adoption into a typing cleanup project instead of a validation improvement. The configuration should still catch meaningful errors, and stricter flags can be proposed later once the baseline is stable.

Alternative considered: enable `strict = true` immediately. Rejected for the initial change because it may force broad annotation churn and make the review less focused.

Keep CI hardware-free.

Rationale: type checking should run before any live USB validation and must not require attached MOTU hardware. The GitHub Actions workflow already has the right install-and-run shape; this change only adds the type-check command after dependency installation.

## Risks / Trade-offs

- Initial mypy findings may expose broad annotation gaps -> Fix targeted issues first, and use narrow configuration exclusions or ignores only where a dynamic pattern is intentional.
- Tests may require extra annotations around fake callables and mocks -> Prefer local type aliases or helper Protocols over weakening package code types.
- mypy and pyright can disagree -> Standardize CI on mypy for this change and revisit pyright only if its diagnostics would materially improve the project.
- Contributors may see another required check -> Document the exact local command and keep it runnable through the repo-local virtual environment.

## Migration Plan

1. Add `mypy` to the development extra.
2. Add a `[tool.mypy]` configuration scoped to Python 3.11+ and the repo's checked paths.
3. Add a README command for `.venv/bin/python -m mypy motu_proxy tests tools`.
4. Add the same command to GitHub Actions after tests and Ruff.
5. Run mypy locally, fix targeted issues, then rerun pytest, Ruff, and mypy.

Rollback is straightforward: remove the mypy development dependency, configuration, README command, CI step, and type-check-driven cleanup.

## Open Questions

- None. If pyright is preferred before implementation, switch the checker decision in this design and update the tasks accordingly.
