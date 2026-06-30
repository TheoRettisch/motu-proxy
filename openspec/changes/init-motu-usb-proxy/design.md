## Context

The current implementation lives in `handover/motu_usb_mvp.py` as a single-file Python prototype. It has been validated on `root@10.0.8.104`, an Ubuntu 24.04.4 host with a MOTU 624 attached as USB VID:PID `07fd:0005`, serial `0001f2fffe00c719`.

The MOTU exposes class-compliant ALSA audio through interfaces 0-2 and an unbound vendor-specific bulk control interface at interface 3. The proxy must claim only interface 3, using bulk OUT endpoint `0x03` and bulk IN endpoint `0x83`, so audio playback and capture remain owned by `snd-usb-audio`.

The rebuild is a maintainability reset, not a feature expansion. The known-good behavior from the handover MVP remains the compatibility target.

## Goals / Non-Goals

**Goals:**

- Preserve the MVP command set and localhost HTTP behavior.
- Split protocol construction, USB transport, datastore operations, CLI handling, and HTTP serving into focused modules.
- Keep the default runtime dependency-light and suitable for the current Ubuntu host and future small deployment images.
- Add tests that lock down the protocol bytes and safety behavior already proven by the handover MVP.
- Make endpoint/interface discovery explicit enough to avoid hard-coding the MOTU 624 forever while still defaulting to the validated shape.

**Non-Goals:**

- Do not add semantic understanding for every MOTU datastore key.
- Do not enable writes by default.
- Do not make PyUSB a required dependency without demonstrated benefit.
- Do not replace or manage ALSA audio streaming.
- Do not implement service packaging, watchdog integration, or Buildroot packaging in the initial rebuild unless needed for validation.

## Decisions

### Package the MVP as focused Python modules

Use a small Python package with modules similar to:

- `protocol.py`: CRC32, frame builders, ACK/init helpers, sequence helpers.
- `parser.py`: response frame joining and JSON extraction, with room for stricter validation later.
- `device.py`: MOTU device and endpoint discovery from sysfs.
- `transports/usbfs.py`: Linux usbfs bulk transfer backend.
- `datastore.py`: GET/POST orchestration over a transport.
- `http_server.py`: localhost HTTP compatibility layer.
- `cli.py`: command-line entry point.

Alternative considered: keep the MVP as one cleaned-up script. That would be faster, but it preserves the current coupling and makes protocol tests and transport substitution harder.

### Keep usbfs as the baseline transport

The current usbfs path works on Ubuntu 24.04 and avoids extra dependencies. It also matches the future tiny-target constraint captured in the handover notes, where PyUSB/libusb may not be available.

Alternative considered: make PyUSB the primary backend. The host currently has `libusb-1.0-0` but not `python3-usb`, and the existing implementation already validates against the device. PyUSB can remain a future optional backend if it materially improves descriptor discovery, debugging, or portability.

### Preserve the current safety posture

The HTTP server binds to `127.0.0.1` by default. POST/PATCH over HTTP remain disabled unless `--allow-writes` is provided. CLI `post` remains explicit and visible.

Alternative considered: expose a fuller HTTP API immediately. That increases risk around device state mutation before response parsing and write behavior are hardened.

### Discover the vendor control interface, but keep validated defaults

The rebuilt code should discover the MOTU device by VID:PID and optional serial, then locate an unbound vendor-specific interface with one bulk IN and one bulk OUT endpoint. The validated MOTU 624 defaults are interface `3`, OUT `0x03`, IN `0x83`, max packet `512`.

Alternative considered: hard-code interface 3 forever. That is enough for the current 624, but makes the code less robust across MOTU AVB models and future re-enumeration checks.

### Test against captured and live-proven behavior

Automated tests should include CRC32 vectors, GET and POST frame fixtures from the handover MVP, sequence rollover, path normalization, response JSON extraction, and HTTP write gating. Live USB tests remain manual or opt-in because they require the attached MOTU.

Alternative considered: start with only live host testing. That would make refactoring brittle and would not protect protocol byte compatibility.

## Risks / Trade-offs

- Incomplete response parsing -> keep initial behavior equivalent to the MVP, then isolate parser code so stricter validation can be added without touching transport or HTTP layers.
- USB access contention -> serialize datastore requests in the HTTP server and open/close the transport per request for MVP equivalence; later work can introduce a long-lived worker if needed.
- Accidental writes -> keep HTTP writes gated by `--allow-writes`, log write attempts, and keep first live POST validation out of scope for the initial clean rebuild.
- PyUSB uncertainty -> avoid introducing it as a dependency until a concrete benefit is proven against the Ubuntu host.
- Multiple MOTU devices -> require `--serial` when discovery matches more than one device.

## Migration Plan

1. Build the new package alongside the handover files.
2. Validate unit tests locally.
3. Copy or run the rebuilt CLI on `root@10.0.8.104`.
4. Confirm `selftest`, `get /datastore/uid`, `probe --compact`, and read-only HTTP `GET /datastore/uid`.
5. Keep the handover MVP available as a rollback reference until the rebuilt behavior matches.

## Open Questions

- Which harmless datastore key should be used for first live POST validation after the clean rebuild is working?
- Should service packaging be a follow-up change, or should it be folded in after the same-functionality rebuild is stable?
