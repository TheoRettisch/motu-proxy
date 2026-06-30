## 1. Capability Assembly

- [ ] 1.1 Add a helper that reads `apiversion` and the `ext/caps/*` section versions.
- [ ] 1.2 Read device identity keys (`uid`, `model_name`, `firmware_version`, `serial_number`).
- [ ] 1.3 Report absent optional capability paths as not present.

## 2. CLI Command

- [ ] 2.1 Add a `motu-proxy info` command with human-readable output.
- [ ] 2.2 Add `--json` output for tooling.

## 3. Tests And Validation

- [ ] 3.1 Test capability assembly with a fake transport.
- [ ] 3.2 Validate `info` against a live MOTU 624.
