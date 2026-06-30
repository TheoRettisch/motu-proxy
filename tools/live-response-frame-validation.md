# Live Response Frame Validation

Date: 2026-06-30

Target:

- Host: `root@10.0.8.104`
- OS: Ubuntu 24.04
- Device: MOTU 624, USB VID:PID `07fd:0005`
- Serial: `0001f2fffe00c719`

Command:

```sh
PYTHONPATH=. python3 tools/live_validate_response_frames.py \
  --serial 0001f2fffe00c719 \
  --include-full-datastore
```

Result:

```text
live response-frame validation PASS: paths=3 frames=58
```

Validated response set:

- `/datastore/uid`: 1 response frame, message sequence `2`, 332 payload bytes.
- `/datastore/host/mode`: 1 response frame, message sequence `3`, 319 payload bytes, 1 trailing USB padding byte after the logical wrapper.
- `/datastore`: 56 response frames, message sequence `4`, 223833 joined payload bytes, final frame had 3 trailing USB padding bytes after the logical wrapper.

Observed live response frame shape:

- USB logical wrapper is 4 bytes: response sequence, `0x00`, and little-endian logical length.
- A bulk read may contain extra padding after the logical wrapper length; strict parsing should trim to the wrapper length before validating the MOTU body.
- MOTU body starts with `NREK` for the validated GET responses.
- Stored CRC32 at body offset 4 matches `crc32(body[20 : 20 + payload_len])`.
- Message sequence at body offset 8 matches the outbound request message sequence for every frame in that response.
- Body offset 12 is `0` on continuation frames and `1` on single-frame or final frames.
- Body offset 16 is the zero-based segment index.
- Body offset 18 is the protected payload length.
- The 4 bytes immediately after the protected payload duplicate the USB logical wrapper header.

Implemented parser behavior:

- Validate and strip response frames using `payload_len`, not the old fixed `frame[20:]` join behavior.
- Reject CRC or message-sequence mismatches after trimming to the logical wrapper length.
- Treat USB padding after the logical wrapper as transport padding, not as a protocol failure.
