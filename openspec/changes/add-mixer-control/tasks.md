## 1. Typed Model

- [ ] 1.1 Define bus kinds (`chan`, `main`, `aux`, `group`, `reverb`, `monitor`) and their parameter-to-path maps.
- [ ] 1.2 Compose concrete datastore paths from kind, index, and parameter with 0-based indexing.
- [ ] 1.3 Enumerate available channels and buses from the device rather than hard-coding.

## 2. Read And Write

- [ ] 2.1 Read mixer parameters (fader, mute, solo, pan, name, EQ bands, gate, comp, sends).
- [ ] 2.2 Write `rw` mixer parameters with documented ranges.
- [ ] 2.3 Support batched multi-parameter writes in one datastore operation.

## 3. CLI

- [ ] 3.1 Add a `mixer show` command that reports strip and bus state.
- [ ] 3.2 Add `mixer set <kind> <index> <param> <value>` write verbs.

## 4. Tests And Validation

- [ ] 4.1 Test path composition and range handling with a fake transport.
- [ ] 4.2 Test batched-write encoding.
- [ ] 4.3 Validate harmless reads and writes against a live MOTU 624.
