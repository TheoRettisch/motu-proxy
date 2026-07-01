## 1. CLI Token Mode

- [x] 1.1 Add a `serve` opt-in flag such as `--require-write-token` and update help text for write-token, token-file, and unsafe remote-write options.
- [x] 1.2 Change `command_serve()` so generated tokens and token files are created only when writes and token protection are both enabled.
- [x] 1.3 Preserve existing token-file permissions, symlink refusal, and matching-token cleanup for token-protected mode.
- [x] 1.4 Update serve startup logging for token-disabled write mode, token-file mode, no-file/debug token display, and remote writes without token protection.

## 2. HTTP Enforcement

- [x] 2.1 Update write-token validation so a missing expected token means token protection is disabled, not an automatic write rejection.
- [x] 2.2 Preserve `403` rejection for missing, wrong, or non-ASCII request tokens when an expected token is configured.
- [x] 2.3 Verify writes still require `--allow-writes` and still apply Host, Origin, body, schema validation, and frame-size checks before USB writes.
- [x] 2.4 Ensure `--unsafe-allow-remote-writes` relaxes the Host restriction without implicitly requiring or generating a token.

## 3. Tests And Documentation

- [x] 3.1 Update HTTP dispatcher tests for local writes without token protection, token-protected rejection/acceptance, and unsafe remote writes without token protection.
- [x] 3.2 Update HTTP handler tests so pre-body token rejection still happens when token protection is enabled and no-token write mode succeeds through the normal request path.
- [x] 3.3 Update CLI serve tests for parser defaults, token-generation opt-in, token-file cleanup, and startup log text.
- [x] 3.4 Update repository documentation and any service-packaging examples/specs that describe write-token behavior so token protection is presented as optional.

## 4. Verification

- [x] 4.1 Run `.venv/bin/python -m pytest tests/test_http_server.py tests/test_cli.py`.
- [x] 4.2 Run `.venv/bin/python -m ruff check .`.
