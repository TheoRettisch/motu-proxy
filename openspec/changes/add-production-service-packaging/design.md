## Context

`motu-proxy serve` currently runs well as an interactive command, but production use needs service-manager behavior around startup, shutdown, configuration, logging, and hardening. The target host is Linux with systemd, sysfs discovery, and usbfs access to the vendor-specific datastore control interface. USB access usually requires root or explicit device permissions, and the live validation workflow already uses root on the MOTU host.

The service must preserve the existing safety model: read-only HTTP by default, explicit write-mode opt-in, no ALSA audio interface claiming, one USB datastore owner, and dependency-light Python runtime.

## Goals / Non-Goals

**Goals:**
- Provide systemd service artifacts that operators can install without hand-writing unit files.
- Make the default service read-only, loopback-bound, restartable, and compatible with optional `/run/motu-proxy/write-token` protection.
- Support environment-file configuration for listen address, port, serial/device selection, validation flags, debug flags, and explicit write-mode opt-in.
- Ensure supervised shutdown closes the coordinator/server path and removes the runtime token file.
- Include conservative hardening directives that do not block sysfs discovery or usbfs access.
- Document installation, configuration, health/status checks, logs, rollback, and live MOTU validation.

**Non-Goals:**
- Do not add a non-systemd service manager abstraction.
- Do not add a Python runtime dependency for service integration or sd_notify support.
- Do not implement a udev rule or dedicated service-user permission model in this change.
- Do not change CLI one-shot commands, USB frame semantics, datastore write validation, or HTTP datastore API behavior.

## Decisions

1. Ship explicit systemd deployment artifacts under a repository packaging/deploy directory.

   The change should add a service unit, an example environment file, and service installation documentation. Keeping these as explicit deployment artifacts avoids surprising behavior from `pip install`, while still giving packaging systems a stable source to install from.

   Alternative considered: install the unit automatically through Python package metadata. That is brittle across distro packaging, virtual environments, and root/non-root installs, and it hides an operationally sensitive step.

2. Use a simple environment-file argument pattern.

   The unit should run `motu-proxy serve` and read operator-controlled options from an environment file such as `/etc/motu-proxy/motu-proxy.env`. Defaults should bind to `127.0.0.1:1280`, keep writes disabled, and write the token to `/run/motu-proxy/write-token` only when writes and token protection are explicitly enabled. Device selection should be configurable with normal CLI flags, especially `--serial`.

   Alternative considered: invent a separate config-file parser. That adds runtime surface area and duplicates the CLI contract.

3. Let systemd own the runtime directory.

   The service unit should use `RuntimeDirectory=motu-proxy` and `RuntimeDirectoryMode=0700`. When token protection is enabled, the application still generates and removes the write-token file, but the service manager creates the parent directory with predictable permissions and cleans it on service exit.

   Alternative considered: continue relying only on application-side `mkdir`. That works interactively, but systemd runtime directory management is clearer and avoids stale directory ownership surprises.

4. Add signal-safe `serve` shutdown behavior.

   `serve` should respond to service-stop signals by exiting the `serve_forever()` loop, running the existing `before_close` callback, closing the server, and removing any generated token file. This should cover `SIGTERM` as used by systemd and preserve current Ctrl-C behavior.

   Alternative considered: rely on process termination. That can skip cleanup paths and leave the coordinator or token file lifecycle dependent on abrupt interpreter exit.

5. Harden the unit without hiding required USB/sysfs resources.

   The unit should include conservative hardening such as `NoNewPrivileges`, `PrivateTmp`, `ProtectHome`, `ProtectSystem`, restricted address families, and a narrow device policy allowing USB character-device access. It should not use `PrivateDevices=true`, because that would hide `/dev/bus/usb` from usbfs. The initial unit may run as root because that matches current usbfs operational requirements; dedicated-user operation can be documented as a future/local override once udev permissions exist.

   Alternative considered: run as a dedicated user by default. That is preferable long-term, but without a udev rule or distro-specific ACL setup it risks shipping a unit that fails to open the device on the known target.

## Risks / Trade-offs

- Hardening can accidentally block USB discovery or access -> Validate the unit with a smoke read on the live MOTU host and keep explicit notes for directives that must not be enabled.
- Running as root increases blast radius -> Keep the HTTP service loopback/read-only by default, avoid printing tokens to journald, and use systemd hardening to reduce writable filesystem and device access.
- Environment-file argument parsing can be subtle in systemd -> Keep examples simple, test the rendered command path where possible, and document quoting expectations.
- Clean signal handling can interact poorly with threaded HTTP shutdown -> Add handler-level tests or fake-server tests that prove `before_close` and token cleanup run during service-style shutdown.
- Systemd availability varies across development hosts -> Make `systemd-analyze verify` an optional check when present, while keeping unit-content tests hardware-free.

## Migration Plan

No datastore migration is required. Operators can install the unit and environment file, run `systemctl daemon-reload`, start the service, and confirm `GET /datastore/uid` or `/__motu_proxy/status` through localhost. Rollback is stopping/disabling the unit and returning to manual `motu-proxy serve` invocation.

Existing manual CLI workflows remain valid. Existing deployments that already use a custom service file can either keep their local unit or adopt the packaged unit after comparing environment options and hardening directives.

## Open Questions

- Should a later change add a dedicated service user plus udev rule for non-root usbfs access?
- Should distribution packages auto-install the unit, or should upstream continue shipping service artifacts for downstream packagers to install explicitly?
