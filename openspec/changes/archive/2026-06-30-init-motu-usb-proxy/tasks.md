## 1. Project Structure

- [x] 1.1 Create a Python package layout for the maintained proxy implementation.
- [x] 1.2 Add a CLI entry point that preserves the handover command names and arguments.
- [x] 1.3 Keep the handover files available as reference fixtures during the rebuild.

## 2. Protocol And Parsing

- [x] 2.1 Move CRC32, sized words, init, ACK, GET, and POST frame builders into a protocol module.
- [x] 2.2 Add tests for CRC32, captured GET frame bytes, and captured POST frame bytes.
- [x] 2.3 Add sequence rollover tests for the host sequence range used by the MVP.
- [x] 2.4 Move response joining and JSON extraction into a parser module with tests for existing MVP behavior.

## 3. USB Device And Transport

- [x] 3.1 Implement sysfs MOTU device discovery by VID:PID and optional serial.
- [x] 3.2 Implement vendor-specific bulk interface and endpoint discovery while avoiding ALSA-bound interfaces.
- [x] 3.3 Implement the Linux usbfs transport as the required baseline backend.
- [x] 3.4 Verify sysfs plus usbfs covers the Ubuntu 24.04 host cleanly; defer PyUSB unless a concrete discovery, diagnostics, or reliability gap is found.
- [x] 3.5 Add fake sysfs tests for single-device discovery, serial selection, and multiple-device refusal when `--serial` is omitted.

## 4. Datastore Operations

- [x] 4.1 Implement datastore init, GET, POST, ACK, and response collection over the transport abstraction.
- [x] 4.2 Preserve per-request open/close behavior for initial MVP equivalence.
- [x] 4.3 Preserve path normalization for `/datastore/...`, bare paths, root, and UID-prefixed paths.
- [x] 4.4 Add tests for path normalization and datastore request behavior using fake transports.

## 5. HTTP Proxy

- [x] 5.1 Implement a localhost HTTP server that binds to `127.0.0.1` by default.
- [x] 5.2 Implement the `serve` command as the entry point for the localhost HTTP server, with writes disabled by default.
- [x] 5.3 Implement GET handling for normalized datastore paths.
- [x] 5.4 Implement POST/PATCH rejection unless `--allow-writes` is explicitly set.
- [x] 5.5 Implement POST/PATCH body handling for `json=` form fields and raw JSON bodies.
- [x] 5.6 Route HTTP POST and PATCH through one explicit datastore POST implementation, with PATCH documented in code as a compatibility alias rather than partial-update semantics.
- [x] 5.7 Add tests for HTTP GET behavior, write gating, `serve` defaults, and PATCH alias behavior.

## 6. Validation

- [x] 6.1 Run the full unit test suite locally without MOTU hardware.
- [x] 6.2 Run `selftest` on `root@10.0.8.104`.
- [x] 6.3 Run `get /datastore/uid` on `root@10.0.8.104` and confirm the known serial response.
- [x] 6.4 Run `probe --compact` on `root@10.0.8.104` and compare behavior to the handover MVP.
- [x] 6.5 Run the read-only HTTP proxy on `root@10.0.8.104`, confirm `GET /datastore/uid`, and stop the proxy after validation.
