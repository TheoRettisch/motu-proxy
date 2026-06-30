## 1. Recovery Core

- [ ] 1.1 Add a domain error for temporary datastore device unavailability and map discovery/open/init failures into it.
- [ ] 1.2 Implement a datastore manager that owns `DatastoreConfig`, discovery, usbfs open/close, datastore init, current session state, and bounded reconnect retry timing.
- [ ] 1.3 Ensure the manager closes and discards the current session after USB read/write/open/init failures that indicate the device was lost.
- [ ] 1.4 Guard session open/reopen so concurrent foreground requests or poll cycles cannot create parallel vendor-control sessions.

## 2. HTTP Coordinator Integration

- [ ] 2.1 Update `command_serve` to pass a managed datastore into `DatastoreCoordinator` instead of a lifetime-open raw datastore.
- [ ] 2.2 Keep the background poll loop alive during device-unavailable periods and retry reconnects without noisy repeated logs.
- [ ] 2.3 Make foreground HTTP reads/writes return promptly with temporary-unavailable errors when reconnect cannot complete.
- [ ] 2.4 Ensure failed writes are not replayed automatically after reconnect.

## 3. Tests And Documentation

- [ ] 3.1 Add hardware-free tests for discovery/open/init failure mapping, session discard after USB loss, and reconnect success after a fake device returns.
- [ ] 3.2 Add coordinator/server tests for `503 Service Unavailable` during outage and successful request flow after reconnect.
- [ ] 3.3 Add tests proving no implicit write replay after a reconnectable write failure.
- [ ] 3.4 Update README operational notes for hotplug recovery behavior and temporary-unavailable responses.

## 4. Live Validation

- [ ] 4.1 Add or document a live validation workflow that starts `serve`, confirms a harmless read, power-cycles or reconnects the MOTU, observes `503` during outage, and confirms reads resume after rediscovery.
- [ ] 4.2 Run the hardware-free test suite and, when hardware is available, the live recovery validation against `root@10.0.8.104` using only the vendor-specific datastore interface.
