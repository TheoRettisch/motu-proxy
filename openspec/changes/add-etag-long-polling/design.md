## Context

The MOTU datastore supports long-polling via `If-None-Match` with a 15-second device-side hold, plus a `client` identifier that filters a client's own changes out of its long-poll stream. `motu-proxy` already carries an ETag in the USB GET frame but does not realize the behavior: the response ETag is discarded, the HTTP layer ignores `If-None-Match`, and `DEFAULT_TIMEOUT_MS` is roughly 600 ms so a held reply never arrives. The proxy also serializes all datastore access behind a single lock because there is one USB control pipe.

## Goals / Non-Goals

**Goals:**

- Realize datastore long-polling end to end: HTTP `If-None-Match` + `client` mapped to a USB long-poll, returning `304` on no-change and the delta plus new ETag on change.
- Keep the single-USB-pipe model correct: a held long-poll must not corrupt or permanently block other datastore traffic.
- Support multiple HTTP long-poll clients without giving each request its own held USB read.
- Preserve existing read/write semantics and the safety posture.

**Non-Goals:**

- Do not implement metering here.
- Do not build a full typed mixer model here (`add-mixer-control`).
- Do not open a second USB session or claim additional USB interfaces.

## Decisions

### Extend the USB read timeout for long-poll reads only

Long-poll reads use a dedicated timeout greater than the 15-second device hold, while ordinary reads keep the short timeout. The read loop must distinguish "device is holding" from "device is quiet" so it does not return early.

Alternative considered: raise the global timeout. Rejected because it would slow every ordinary read and every quiet-drain.

### Use a single background poller with fan-out

The single USB pipe and single dispatch lock mean a naive 15-second held GET per HTTP request blocks other traffic and multiplies pressure on the device. This change uses a single datastore coordinator that owns USB operations. A background worker performs one device long-poll at a time with the native hold window, tracks the latest ETag and recent change events, and wakes HTTP long-poll handlers through an in-process condition variable.

HTTP long-poll requests do not issue their own held USB reads when the coordinator is current. They compare the request `If-None-Match` value with coordinator state, return immediately when a matching change is already known, or wait locally until the poller publishes a change or the HTTP wait deadline expires.

Alternative considered: bounded short waits per request. Rejected because it is MVP-level behavior and increases client polling frequency. Alternative considered: separate USB session for polling. Rejected because it is higher risk against the device and the ALSA-safe posture.

### Serialize writes and direct reads through the same coordinator

Ordinary reads and writes enter the same coordinator queue as the poller. The poller only holds the pipe while an actual device long-poll is in flight; between poll cycles the coordinator can service writes and direct reads, then resume polling from the newest known ETag. Successful writes publish their returned ETag/payload when available, or trigger an immediate refresh when the write response does not carry enough state.

This keeps one owner for sequence numbers, ETag state, and USB I/O, which is more reliable than mixing a background poller with independent request-thread transport calls.

### Preserve client filtering locally where possible

The native API lets a `client` identifier filter that client's own changes. A single background poller cannot ask the device for different client filters simultaneously, so the proxy records the `client` value attached to writes that pass through it and suppresses those locally for matching HTTP waiters. Device-originated changes and changes whose origin is unknown are fanned out to all waiters.

Alternative considered: one device long-poll per client identifier. Rejected because it recreates the USB-pipe starvation problem and scales poorly.

### Map timeout to 304 and change to 200

When the coordinator has no matching change before the HTTP wait deadline, the HTTP layer returns `304 Not Modified` with the same ETag. When the poller or a serialized write publishes a matching change, the HTTP layer returns `200` with the changed payload and the new ETag.

### Forward adjacent deltas verbatim and retain bounded history

The coordinator forwards device delta payloads verbatim for adjacent ETag transitions. It does not normalize, merge, reshape, or synthesize deltas in the first implementation, because the native API already defines the meaning of "changes since ETag X" and consumers expect that shape.

The coordinator retains a bounded ring of the 64 most recent ETag transitions. If a client asks from an ETag that can be satisfied from a complete adjacent transition chain, the coordinator may return the corresponding device payload. If the requested ETag is missing or too stale for the ring, the coordinator performs a direct refresh or full datastore read instead of inventing a merged delta.

## Risks / Trade-offs

- Coordinator complexity: a worker, condition variable, and shared state are more moving parts than request-local reads. Mitigation: keep a single USB owner and cover fan-out, shutdown, timeout, and write interleaving with fake-transport tests.
- Delta semantics: the device returns "changes since the given ETag," which may be a partial object. Mitigation: forward device payloads verbatim for adjacent transitions and fall back to a direct coordinator read or full refresh when the 64-entry local history cannot satisfy a stale client safely.
- Client identifier filtering is only fully knowable for writes that pass through the proxy. Mitigation: locally suppress proxy-originated changes for the same `client`, and fan out unknown-origin changes to avoid dropping real device updates.

## Migration Plan

1. Parse the reply ETag (from `add-datastore-http-api-compat`).
2. Add the datastore coordinator and background poller with a dedicated native-hold timeout.
3. Wire HTTP `If-None-Match` / `client` to local waiters with `304` mapping and change fan-out.
4. Add the 64-entry ETag transition history and stale-client direct refresh fallback.
5. Route ordinary reads and writes through the coordinator and publish write-originated changes.
6. Validate against a live MOTU 624 by changing a parameter and observing prompt long-poll return without starving ordinary requests.
