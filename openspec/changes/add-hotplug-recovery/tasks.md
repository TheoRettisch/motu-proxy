## 1. Recovery Core

- [ ] 1.1 Add a domain error for temporary datastore device unavailability and map discovery/open/init failures into it.
- [ ] 1.2 Define and test an explicit device-loss taxonomy that maps only reconnectable discovery/open/init/USB transport failures into recovery, while leaving request validation, permission, parser, and protocol errors on their existing error paths.
- [ ] 1.3 Implement a datastore manager that owns `DatastoreConfig`, discovery, usbfs open/close, datastore init, current session state, session generation, and bounded reconnect retry timing.
- [ ] 1.4 Ensure the manager closes and discards the current session after USB read/write/open/init failures that indicate the device was lost.
- [ ] 1.5 Expose manager status fields for availability, reconnect state, last reconnect error, and retry/backoff timing.
- [ ] 1.6 Guard session open/reopen so concurrent foreground requests or poll cycles cannot create parallel vendor-control sessions.

## 2. HTTP Coordinator Integration

- [ ] 2.1 Update `command_serve` to pass a managed datastore into `DatastoreCoordinator` instead of a lifetime-open raw datastore.
- [ ] 2.2 Keep the background poll loop alive during device-unavailable periods and retry reconnects without noisy repeated logs.
- [ ] 2.3 Make foreground HTTP reads/writes perform at most one prompt opportunistic reopen attempt when eligible, then return temporary-unavailable errors when reconnect cannot complete.
- [ ] 2.4 Reset coordinator ETag, cached datastore state, and delta transition history when the manager reports a new USB datastore session generation.
- [ ] 2.5 Ensure failed writes are not replayed automatically after reconnect.
- [ ] 2.6 Add hotplug availability, last error, and reconnect/backoff fields to `/__motu_proxy/status` without routing status checks through datastore dispatch.

## 3. Tests And Documentation

- [ ] 3.1 Add hardware-free tests for discovery/open/init failure mapping, session discard after USB loss, and reconnect success after a fake device returns.
- [ ] 3.2 Add coordinator/server tests for `503 Service Unavailable` during outage and successful request flow after reconnect.
- [ ] 3.3 Add tests proving no implicit write replay after a reconnectable write failure.
- [ ] 3.4 Add tests proving reconnect clears coordinated ETag/delta history and resumes from a fresh datastore read.
- [ ] 3.5 Add status endpoint tests for unavailable/reconnecting state and recovered availability.
- [ ] 3.6 Update README operational notes for hotplug recovery behavior, temporary-unavailable responses, status fields, and no implicit write replay.

## 4. Live Validation

- [ ] 4.1 Add or document a live validation workflow that starts `serve`, confirms a harmless read, power-cycles or reconnects the MOTU, observes `503` during outage, and confirms reads resume after rediscovery.
- [ ] 4.2 Run the hardware-free test suite and, when hardware is available, the live recovery validation against `root@10.0.8.104` using only the vendor-specific datastore interface.
