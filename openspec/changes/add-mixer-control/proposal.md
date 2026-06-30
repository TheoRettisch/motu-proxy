## Why

The MOTU datastore exposes a full DSP mixer under `mix/*`: input channel strips (`mix/chan/<i>`) with high-pass filter, four-band parametric EQ, gate, compressor, and a matrix section (fader, mute, solo, pan) plus sends to aux, group, and reverb buses; and `mix/main`, `mix/aux`, `mix/group`, `mix/reverb`, and `mix/monitor` buses with their own EQ, dynamics, and matrix controls. Each parameter has a documented range and 0-based indexing.

Both reference projects are, at heart, mixer controllers built on these paths: `ixnas/Mackie-of-the-Unicorn` maps a Mackie Control surface onto channel fader / mute / solo / pan / name with banking, and `alexanderson1993/Motu-Control` builds a typed channel model and batches several edits into one request. `motu-proxy` exposes only raw datastore reads and writes; there is no mixer-aware layer, so every consumer must re-encode path strings and ranges by hand. A typed mixer model over the documented paths gives CLI users and downstream bridges (a MIDI control surface, a web mixer) a direct, validated way to read and set mixer state.

## What Changes

- Add a `motu-mixer-control` capability: a typed model mapping the documented `mix/*` paths to channel strips and buses with their parameters and ranges.
- Provide read access (enumerate strips and buses; get fader, mute, solo, pan, name, EQ bands, gate, compressor, sends) and write access for `rw` parameters, using 0-based indexing and documented ranges.
- Support batched multi-parameter writes in a single datastore operation, matching the API's multi-key subtree write and the reference client's batching behavior.
- Expose the model through CLI verbs (for example `mixer show`, `mixer set chan <i> fader <v>`), built on the existing datastore layer.
- Add tests for the path mapping, range handling, and batched-write encoding using a fake transport.

## Capabilities

### New Capabilities
- `motu-mixer-control`: A typed mixer model and control commands over the MOTU datastore, suitable as the basis for control-surface and web-mixer clients.

### Modified Capabilities
- None.

## Impact

- Affected code: new `motu_proxy/mixer.py` (typed model and path mapping), `motu_proxy/cli.py` (the `mixer` command group); builds on `motu_proxy/datastore.py`.
- Affected APIs: a new CLI command group; optional higher-level HTTP routes are out of scope for this change.
- Affected systems: control-surface bridges and mixer UIs that prefer a typed model over raw datastore paths.
- Dependencies: standard library only. Benefits from `add-datastore-type-permission-model` for range validation and `add-etag-long-polling` for live state, but does not require them.
