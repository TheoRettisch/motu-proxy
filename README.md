# motu-proxy

`motu-proxy` is a dependency-light Python CLI and localhost HTTP proxy for
MOTU AVB USB datastore operations. It talks to the Linux USB vendor-specific
bulk control interface while leaving the class-compliant ALSA audio interfaces
alone.

The current validation target is a MOTU 624 exposed as USB VID:PID
`07fd:0005`, with ALSA owning audio interfaces 0-2 and the datastore control
path on the unbound vendor-specific interface.

## Scope

- Runtime support is Linux-only. The USB backend uses sysfs and usbfs.
- Development and hardware-independent tests should work on non-Linux hosts.
- The package has no runtime Python dependencies beyond the standard library.
- HTTP writes are disabled by default and require explicit opt-in plus a write
  token.

## Install

Use an editable install while developing:

```sh
python -m pip install -e .
```

Install development tools for tests and lint checks:

```sh
python -m pip install -e ".[dev]"
```

You can also run the package without installing:

```sh
python -m motu_proxy selftest
```

USB access usually requires root, a suitable udev rule, or device permissions
for `/dev/bus/usb/...`.

## CLI Usage

Verify the protocol fixture builders without hardware:

```sh
motu-proxy selftest
```

Read a datastore path:

```sh
motu-proxy get /datastore/uid
```

Show datastore API, capability, and identity details:

```sh
motu-proxy info
motu-proxy info --json
```

Probe a few harmless baseline paths, continuing after errors:

```sh
motu-proxy probe --compact
```

Run a strict read-only smoke test that exits nonzero on any failed read and
prints timing/frame counters:

```sh
motu-proxy smoke --compact
motu-proxy smoke --no-body
motu-proxy smoke --path /uid --path /host/mode --continue-on-error
```

Send an explicit datastore POST over USB:

```sh
motu-proxy post /datastore/host/os '{"value":"linux"}'
```

CLI writes validate known datastore paths before USB I/O. They reject known
read-only paths, out-of-range values, invalid enums, and unknown paths by
default. Use `--allow-unknown-writes` for forward-compatible unknown paths, or
`--no-validate` for raw debugging.

Useful USB selection overrides:

```sh
motu-proxy get /uid --serial 0001f2fffe00c719
motu-proxy get /uid --interface 3 --ep-out 0x03 --ep-in 0x83
```

Paths are normalized for compatibility. For example, `/uid` becomes
`/datastore/uid`, and a leading 16-hex-character UID segment is stripped.

## HTTP Proxy

Start the read-only localhost proxy:

```sh
motu-proxy serve
```

Read through HTTP:

```sh
curl http://127.0.0.1:1280/datastore/uid
```

Forward a datastore client identifier or wait from a known ETag:

```sh
curl 'http://127.0.0.1:1280/datastore/uid?client=1479701624'
curl -i -H 'If-None-Match: 5678' http://127.0.0.1:1280/datastore
```

By default the server binds to `127.0.0.1`, opens one USB datastore session for
the server lifetime, and rejects POST/PATCH. A `DatastoreCoordinator` owns USB
access for the HTTP server: foreground reads and writes are serialized against a
background `/datastore` long-poller that tracks ETags and fans out changes to
HTTP long-poll waiters.

GET responses include `Cache-Control: no-cache` and an `ETag` header when the
device supplies one. A GET with `If-None-Match` waits against coordinated
datastore state and may return either a changed payload with a new `ETag` or
`304 Not Modified`.

During shutdown the coordinator asks the background poller to stop and waits for
it before the USB datastore context is released.

## Write Mode

HTTP writes require `--allow-writes`:

```sh
motu-proxy serve --allow-writes
```

When write mode is enabled, `motu-proxy` generates a random token, prints it to
stderr, and writes it to:

```text
/run/motu-proxy/write-token
```

The token file is created with owner-only permissions on Linux. Every HTTP
POST/PATCH must include the token using either:

```text
Authorization: Bearer <token>
```

or:

```text
X-Motu-Proxy-Token: <token>
```

Example write:

```sh
TOKEN="$(cat /run/motu-proxy/write-token)"
curl \
  -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"value":"linux"}' \
  http://127.0.0.1:1280/datastore/host/os
```

HTTP PATCH is accepted only as a compatibility alias for the same MOTU
datastore POST operation. It does not implement partial-update semantics.

HTTP writes accept either a raw JSON object body or an
`application/x-www-form-urlencoded` body with a `json` field.

Additional write-mode protections:

- Missing or incorrect write token is rejected.
- Non-loopback `Host` headers are rejected by default.
- Cross-origin browser writes are rejected when an `Origin` header is present.
- `Origin: null` is rejected.
- Request bodies larger than `--max-write-body-bytes` are rejected, and writes
  that cannot fit in one current MOTU USB datastore frame are rejected before
  USB I/O.
- Writes to paths absent from the embedded schema are rejected unless
  `--allow-unknown-writes` is set. `--no-validate` disables all datastore
  type, range, enum, permission, and unknown-path checks.
- `--allow-writes` with a non-loopback `--listen` address requires
  `--unsafe-allow-remote-writes`.

Avoid exposing write mode on a LAN unless the host is otherwise isolated and
the token is treated as a secret.

## Local Automation

Buildroot or other local scripts can consume the generated token without user
interaction by reading the token file and sending it as a bearer token:

```python
from pathlib import Path
from urllib.request import Request, urlopen

token = Path("/run/motu-proxy/write-token").read_text(encoding="ascii").strip()
request = Request(
    "http://127.0.0.1:1280/datastore/host/os",
    data=b'{"value":"linux"}',
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    method="POST",
)

response = urlopen(request, timeout=2).read()
```

The token path can be changed:

```sh
motu-proxy serve --allow-writes --write-token-file /run/my-service/motu-token
```

or disabled if an operator wants the token only on stderr:

```sh
motu-proxy serve --allow-writes --no-write-token-file
```

## Development

Use the repo-local virtual environment for checks:

```sh
.venv/bin/python -m pip install -e ".[dev]"
```

Run the hardware-free test suite:

```sh
.venv/bin/python -m pytest -q
```

Run lint checks:

```sh
.venv/bin/python -m ruff check .
```

Run the fixture self-test:

```sh
.venv/bin/python -m motu_proxy selftest
```

The tests cover protocol byte fixtures, path normalization, strict response
frame validation, parser helpers, fake sysfs discovery, HTTP write gating,
token handling, and USB short read/write failure paths. Fake Linux sysfs tests
are skipped on Windows where Linux interface names such as `3-3:1.3` are not
valid paths.

## Project Layout

- `motu_proxy/protocol.py`: CRC32, frame builders, ACK/init helpers, host
  sequence helpers.
- `motu_proxy/parser.py`: strict response-frame validation, response body
  helpers, and JSON display extraction.
- `motu_proxy/device.py`: Linux sysfs device/interface/endpoint discovery.
- `motu_proxy/transports/usbfs.py`: Linux usbfs bulk transport.
- `motu_proxy/datastore.py`: datastore GET/POST orchestration over a transport
  plus the persistent HTTP coordinator/long-poller.
- `motu_proxy/http_server.py`: localhost HTTP compatibility layer and write
  safety checks.
- `motu_proxy/schema.py`: embedded datastore type, permission, range, and enum
  validation for writes.
- `motu_proxy/cli.py`: command-line entry point.
- `tools/live_validate_response_frames.py`: opt-in live USB response-frame
  validator for CRC, message sequence, segmentation, and logical wrapper
  behavior.
- `tools/live_validate_foreground_preemption.py`: opt-in live validation for
  foreground reads/writes while the HTTP long-poller is active.
- `tests/`: hardware-free regression tests.
- `openspec/`: design/specification history for the rebuild.
- `research/spike-metering/`: exploratory notes and probes for meter reads.

## License

This project is licensed under the MIT License. See `LICENSE`.

## Known Follow-Ups

- Add service packaging and deployment-specific udev/systemd integration when
  the target image layout is settled.
