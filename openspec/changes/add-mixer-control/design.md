## Context

The documented mixer (`mix/*`, mixer version 1.0) is a complete channel-strip and bus model: `mix/chan` input strips (hpf, four-band EQ, gate, comp, matrix fader/mute/solo/pan, sends to aux/group/reverb), and `mix/main`, `mix/aux`, `mix/group`, `mix/reverb`, `mix/monitor` buses. All indices are 0-based and most parameters carry documented ranges. `motu-proxy` exposes only raw datastore operations. The two reference projects show the demand: a Mackie surface bridge and a typed JS mixer model.

## Goals / Non-Goals

**Goals:**

- A typed mapping from basic mixer concepts (strip, bus, fader, mute, solo, pan, name) to documented datastore paths.
- Read and write of the first-pass mixer controls with 0-based indexing and documented ranges.
- Batched multi-parameter writes in one datastore operation.
- A CLI surface that is convenient for operators and a reference for bridge authors.

**Non-Goals:**

- Do not implement metering (separate, and undocumented in this API doc).
- Do not implement a MIDI control-surface bridge here; this change provides the model such a bridge would consume.
- Do not add higher-level HTTP mixer routes in this change.
- Do not include EQ, gate, compressor, or send controls in the first pass.

## Decisions

### Model strips and buses over documented paths

Represent `chan`, `main`, `aux`, `group`, `reverb`, and `monitor` as bus kinds, each with a first-pass parameter map for documented fader, mute, solo, pan, and name controls where applicable. Reads compose the concrete path from kind, index, and parameter; writes do the reverse.

Alternative considered: a flat passthrough with helper constants. Rejected because it pushes range and structure knowledge back onto every caller.

### Batched writes via multi-key subtree POST

The datastore write already forwards a JSON object of key/value pairs; the mixer layer composes a single subtree write that sets several parameters at once, matching the API's multi-value write and the reference client's batching.

Alternative considered: one write per parameter. Rejected as slower over the single USB pipe and not atomic.

### Build on the datastore layer, not around it

The mixer module calls the existing datastore read/write, so write gating, validation (when present), and transport remain in one place.

## Risks / Trade-offs

- Available channels and buses vary by model and sample rate. Mitigation: enumerate from the device (bank and channel counts) rather than hard-coding, and treat absent paths as unavailable.
- Mixer version may change in future firmware. Mitigation: gate the model on the mixer capability version from `add-capability-discovery` when available.

## Migration Plan

1. Add the typed model and first-pass basic control path mapping for `mix/chan` first, then the buses.
2. Add CLI read verbs (`mixer show`), then write verbs (`mixer set ...`).
3. Add batched-write support.
4. Validate against a live MOTU 624 on harmless parameters.

## Open Questions

- Should the CLI accept dB and Hz with unit conversion, or only the raw documented units and ranges?
