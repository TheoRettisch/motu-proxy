## Context

The datastore API exposes a global `apiversion` and per-section semvers at `ext/caps/<section>`, plus device identity keys. `motu-proxy` already normalizes `/apiversion` but has no discovery command. This change adds a thin read-only aggregator.

## Goals / Non-Goals

**Goals:**

- One command to report API version, section capability versions, and device identity.
- Machine-readable output for tooling.

**Non-Goals:**

- Do not cache across invocations.
- Do not interpret or validate section contents beyond presence and version.

## Decisions

### Aggregate documented capability paths in one command

`info` reads `apiversion`, `ext/caps/avb`, `ext/caps/router`, `ext/caps/mixer`, and the identity keys, then reports them together. Absent optional capability paths are reported as "not present," matching the API's rule that a missing capability path means the section does not exist.

Alternative considered: read the entire datastore and infer sections. Rejected as heavier and less explicit than reading the documented capability keys.

## Risks / Trade-offs

- Some identity keys may be absent on some models. Mitigation: treat identity fields as optional and report what is available.

## Migration Plan

1. Add the capability assembly helper.
2. Add the `info` command with text and `--json` output.
3. Validate against a live MOTU 624.

## Open Questions

- Should `info` also surface a compact summary of input bank, output bank, and mixer channel counts, or is that the mixer change's responsibility?
