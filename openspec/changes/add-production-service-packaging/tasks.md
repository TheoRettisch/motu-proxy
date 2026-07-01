## 1. Service Artifacts

- [ ] 1.1 Add a repository service packaging directory with `motu-proxy.service` and an example environment file.
- [ ] 1.2 Make the unit start `motu-proxy serve` with read-only `127.0.0.1:1280` defaults and operator-configurable CLI arguments.
- [ ] 1.3 Configure `RuntimeDirectory=motu-proxy` and `RuntimeDirectoryMode=0700` so `/run/motu-proxy/write-token` has a service-managed parent directory.
- [ ] 1.4 Add conservative restart and hardening directives while preserving sysfs discovery and `/dev/bus/usb` access.
- [ ] 1.5 Document any directives intentionally not used, especially `PrivateDevices=true`, because they would block usbfs access.

## 2. Serve Lifecycle

- [ ] 2.1 Add service-style signal handling so `SIGTERM` and `SIGINT` cause `serve` to leave `serve_forever()` cleanly.
- [ ] 2.2 Ensure `before_close`, server close, coordinator close, and write-token cleanup run during service stop.
- [ ] 2.3 Preserve current interactive Ctrl-C behavior and one-shot CLI command behavior.
- [ ] 2.4 Verify write tokens are not printed in normal service logs when a token file is configured.

## 3. Tests

- [ ] 3.1 Add hardware-free tests that the service unit and environment example exist and contain the required default read-only options.
- [ ] 3.2 Add tests that the unit includes runtime directory, restart, and hardening directives without hiding USB devices.
- [ ] 3.3 Add serve lifecycle tests using a fake server to prove service-stop signal handling runs cleanup callbacks.
- [ ] 3.4 Add or extend tests that normal write-token logging remains redacted with token-file service configuration.
- [ ] 3.5 If `systemd-analyze` is available in the test environment, add an optional verification check or documented manual check for the unit syntax.

## 4. Documentation

- [ ] 4.1 Update README or operations documentation with install, environment-file configuration, start/stop/enable, and rollback steps.
- [ ] 4.2 Document how to check service logs, `/__motu_proxy/status`, and a harmless datastore read.
- [ ] 4.3 Document write-mode enablement, token-file location, and the reason tokens are not printed to journald by default.
- [ ] 4.4 Document live service validation against `root@10.0.8.104` using only vendor-specific datastore read paths and avoiding parallel USB validations.

## 5. Verification

- [ ] 5.1 Run `.venv/bin/python -m pytest -q`.
- [ ] 5.2 Run `.venv/bin/python -m ruff check .`.
- [ ] 5.3 Run `git diff --check`.
- [ ] 5.4 When hardware is available and no other process owns the vendor interface, run the documented live service validation against the MOTU 624.
