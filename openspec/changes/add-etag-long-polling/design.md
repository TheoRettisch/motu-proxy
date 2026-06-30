## Context

The MOTU datastore supports long-polling via `If-None-Match` with a 15-second device-side hold, plus a `client` identifier that filters a client's own changes out of its long-poll stream. `motu-proxy` already carries an ETag in the USB GET frame but does not realize the behavior: the response ETag is discarded, the HTTP layer ignores `If-None-Match`, and `DEFAULT_TIMEOUT_MS` is roughly 600 ms so a held reply never arrives. The proxy also serializes all datastore access behind a single lock because there is one USB control pipe.

## Goals / Non-Goals

**Goals:**

- Realize datastore long-polling end to end: HTTP `If-None-Match` + `client` mapped to a USB long-poll, returning `304` on no-change and the delta plus new ETag on change.
- Keep the single-USB-pipe model correct: a held long-poll must not corrupt or permanently block other datastore traffic.
- Preserve existing read/write semantics and the safety posture.

**Non-Goals:**

- Do not implement metering here.
- Do not build a full typed mixer model here (`add-mixer-control`).
- Do not guarantee multi-client fairness beyond the chosen serialization strategy.

## Decisions

### Extend the USB read timeout for long-poll reads only

Long-poll reads use a dedicated timeout greater than the 15-second device hold, while ordinary reads keep the short timeout. The read loop must distinguish "device is holding" from "device is quiet" so it does not return early.

Alternative considered: raise the global timeout. Rejected because it would slow every ordinary read and every quiet-drain.

### Serialize long-polls without starving other requests

The single USB pipe and single dispatch lock mean a naive 15-second held GET blocks all other requests for up to 15 seconds. Three options:

1. **Bounded wait (simplest):** cap the held read at a short, configurable maximum (for example 1–2 seconds) and let the client re-poll. Lowest risk, slightly higher poll frequency, no concurrency model change.
2. **Single background poller with fan-out:** a dedicated thread long-polls the datastore, maintains the latest ETag and a cache, and HTTP long-poll handlers wait on a local condition variable rather than the USB pipe. Best client experience; introduces a worker and cache-coherency design.
3. **Separate USB session for polling:** open a second handle/endpoint for long-polls. Highest risk against the device and the ALSA-safe posture; not pursued initially.

This change starts with option 1 to land the contract safely, and records option 2 as the follow-up once behavior is validated on hardware.

Alternative considered: jump straight to option 2. Rejected for the initial change because cache coherency and delta-merge semantics need live-device validation first.

### Map timeout to 304 and change to 200

When the held read returns no change within the wait window, the HTTP layer returns `304 Not Modified` with the same ETag. When the device returns changes, the HTTP layer returns `200` with the changed payload and the new ETag.

## Risks / Trade-offs

- A held read blocks the single USB pipe. Mitigation: bounded wait (option 1) until the background-poller design (option 2) is validated.
- Delta semantics: the device returns "changes since the given ETag," which may be a partial object. Mitigation: forward the device payload verbatim; do not attempt local merge in this change.
- Client identifier filtering depends on device behavior. Mitigation: forward `client` and verify filtering against a live device.

## Migration Plan

1. Parse the reply ETag (from `add-datastore-http-api-compat`).
2. Add a long-poll datastore read with a dedicated timeout and a bounded wait.
3. Wire HTTP `If-None-Match` / `client` to it with `304` mapping.
4. Validate against a live MOTU 624 by changing a parameter and observing prompt long-poll return.
5. Evaluate the background-poller fan-out as a follow-up.

## Open Questions

- What maximum bounded-wait value best balances latency and pipe contention before the background poller exists?
- Does the device's delta payload need any normalization for clients, or is verbatim forwarding sufficient?
- How should a long-poll interact with a concurrent write from another HTTP client under the single lock?
