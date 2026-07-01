## Context

`motu-proxy serve` currently has two separate write-safety layers that are coupled in the CLI: `--allow-writes` enables HTTP POST/PATCH, and a generated write token is always required for those HTTP writes. The server also keeps other protections: writes are off by default, non-loopback listen addresses are rejected unless `--unsafe-allow-remote-writes` is set, write requests require a loopback Host header unless remote writes are explicitly allowed, cross-origin writes are blocked, and datastore validation remains enabled by default.

This change separates write enablement from token-based write authentication. The target use case is trusted local automation where `--allow-writes` on `127.0.0.1` is already the deliberate opt-in, and a mandatory bearer/header token adds friction without changing the local trust boundary.

## Goals / Non-Goals

**Goals:**
- Make HTTP write-token enforcement optional and disabled unless explicitly requested.
- Preserve the existing default read-only behavior and the explicit `--allow-writes` gate for all HTTP writes.
- Preserve Host, Origin, validation, body-size, and write logging behavior independently of token configuration.
- Keep generated-token support for operators who want token protection, including token-file and debug/no-file workflows.
- Update tests, help text, and operational docs so token protection is described as a separate opt-in.

**Non-Goals:**
- Do not change one-shot CLI `post` behavior or require tokens for direct USB writes.
- Do not add persistent static tokens, user accounts, TLS, or a broader authentication system.
- Do not change datastore USB frame semantics, write validation policy, or PATCH alias behavior.
- Do not make remote writes safe by default; `--unsafe-allow-remote-writes` remains the explicit operator risk boundary.

## Decisions

1. Add a dedicated write-token opt-in flag.

   Introduce a `serve` option such as `--require-write-token`. `--allow-writes` continues to mean "permit HTTP POST/PATCH after the normal safety checks"; `--require-write-token` means "also generate a token and reject writes that do not present it." This keeps the CLI readable and avoids overloading `--write-token-file` as both a destination and an enablement flag.

   Alternative considered: make `--write-token-file` itself enable token protection. Rejected because it gives no clean way to request token protection without a file, and it makes the existing `--no-write-token-file` wording awkward as an enablement path.

2. Treat `write_token=None` as token protection disabled.

   `validate_write_token()` and `DatastoreDispatcher.validate_write_headers()` should only enforce token comparison when an expected token is configured. If writes are enabled and no token is configured, the dispatcher should continue through Host, Origin, JSON, schema, size, and logging checks before issuing USB writes.

   Alternative considered: add a second boolean such as `require_write_token` to every dispatcher path. Rejected because the presence of an expected generated token already models the runtime behavior, and keeping one source of truth reduces drift between handler pre-validation and dispatch.

3. Generate and clean up token files only when token protection is enabled.

   `command_serve()` should call `prepare_write_token()` only when both `--allow-writes` and `--require-write-token` are set. Existing `--write-token-file` and `--no-write-token-file` behavior remains meaningful inside token-protected write mode: by default the token goes to `/run/motu-proxy/write-token`, `--no-write-token-file` prints the generated token instead, and cleanup removes only the generated matching token file.

   Alternative considered: always generate a token but skip validation unless the opt-in flag is set. Rejected because it keeps unnecessary token-file lifecycle side effects in the default write-enabled local workflow.

4. Make serve startup logging match the selected protection mode.

   When writes are enabled without token protection, startup logs should say that HTTP writes are enabled and write-token protection is disabled. They should not print `write token: None` or token-header instructions. When token protection is enabled, keep the existing redacted token-file behavior and debug/no-file token display. If remote writes are enabled without token protection, log an explicit warning.

   Alternative considered: stay silent about token-disabled mode. Rejected because write-mode startup logs are the operator's last confirmation before the proxy accepts mutations.

5. Keep remote write token protection opt-in.

   `--unsafe-allow-remote-writes` should continue to be required for non-loopback listen addresses, and request Host checks should still be relaxed only in that mode. It should not silently force token protection, because this change's contract is that token protection is opt-in. Operators who expose remote writes can combine `--unsafe-allow-remote-writes` with `--require-write-token`.

   Alternative considered: require tokens for remote writes even after making local write tokens optional. Rejected because it preserves a hidden mandatory-token path and makes the option semantics harder to explain; the existing `unsafe` flag should remain the explicit high-risk opt-in.

## Risks / Trade-offs

- Operators may assume `--allow-writes` still creates a token barrier -> Update help text, docs, startup logs, and service examples to make token protection a separate opt-in.
- Remote writes without token protection are high risk -> Keep the non-loopback listen guard, require `--unsafe-allow-remote-writes`, and emit a strong startup warning when remote writes run without token protection.
- Active service-packaging docs/specs may still describe token files as part of normal write enablement -> Update those docs/examples during implementation if that change is applied together with this one.
- Token validation pre-check and dispatch validation could diverge -> Use `write_token is not None` as the shared enforcement signal and cover both direct dispatcher and handler request paths with tests.

## Migration Plan

No datastore migration is required. Existing local clients that send token headers can continue sending them, but those headers are only required when the server starts with token protection enabled.

Operators who want the previous token-required behavior should add the new token opt-in flag wherever they currently run `motu-proxy serve --allow-writes`. Operators who want the simpler trusted-local workflow can run `--allow-writes` without token options and remove token-reading code from local automation.
