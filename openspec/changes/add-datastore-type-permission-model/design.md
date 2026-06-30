## Context

The MOTU datastore API documents, per path, a type (string, real, int, semver, with modifiers list / pair / opt / bool / enum), a permission (`r` or `rw`), and frequently a numeric range or enum value set. `motu-proxy` forwards writes blindly. The existing safety posture already gates writes behind `--allow-writes`; this change adds value-level safety on top.

## Goals / Non-Goals

**Goals:**

- Reject writes to read-only datastore paths before any USB I/O.
- Validate write values against the documented type, range, and enum for known paths.
- Keep forward compatibility for undocumented paths.
- Return clear, specific HTTP errors.

**Non-Goals:**

- Do not attempt to model every path exhaustively in the first pass; cover the documented global, routing, and mixer parameters and pass the rest through.
- Do not add semantic cross-field validation (for example interactions between two parameters).
- Do not change read behavior.

## Decisions

### Embed a generated schema as data

Generate a path to (type, permission, range/enum) table from the documented API and embed it as a Python data module. Keep it data, not code, so it can be regenerated as the API documentation evolves.

Alternative considered: fetch the schema from the device at runtime by reading the full datastore. Rejected because the datastore returns values, not type/permission metadata; the documented API is the source of permission and range truth.

### Validate by longest-prefix match with placeholder segments

Documented paths use placeholders such as `mix/chan/<index>/eq/highshelf/freq`. Match a concrete request path against the schema by treating numeric segments as placeholder matches, then apply the documented type/permission/range.

Alternative considered: exact-path lookup only. Rejected because indices make exact lookup impractical.

### Forward undocumented paths by default

If a path is not in the schema, forward the write (optionally logging a warning) so that newer firmware paths are not blocked by a stale schema. `--no-validate` forwards everything unchecked.

Alternative considered: deny unknown paths. Rejected as too brittle against firmware updates.

## Risks / Trade-offs

- Schema drift: the embedded schema can lag firmware. Mitigation: forward-compatible passthrough for unknown paths and a clearly regenerable data module.
- Over-strict ranges could block legitimate values on some models. Mitigation: the `--no-validate` escape hatch and conservative range sourcing from the documented API.

## Migration Plan

1. Generate the schema data module from the documented API.
2. Add the validation layer with longest-prefix placeholder matching.
3. Wire validation into the write path with `403`/`422` mapping and the `--no-validate` flag.
4. Add tests for denial, range/type/enum, and passthrough.

## Open Questions

- Which subset of paths should be authoritative in the first schema pass (global + routing + mixer), and how are model-specific ranges handled?
- Should CLI `post` enforce validation by default, matching HTTP, or remain a lower-level escape hatch?
