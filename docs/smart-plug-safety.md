# Safe smart-plug automation pattern

This repository intentionally does not ship a live plug-control implementation. For a separate integration, keep three layers independent:

1. **Presence observer** reports the raw profile state and observation time. Unknown or stale data is not equivalent to disconnected.
2. **Shadow decision** applies asymmetric debounce (a short stable-connected delay before “present”, a longer stable-disconnected delay before “away”) and records intended action without writing to the plug.
3. **Actuator** independently verifies fresh observer and shadow inputs, exact target identity, fault-free device state, LAN path, and any required physical-installation guard. Before a write, re-read the inputs; after a write, read back the real property and record whether it was verified.

A transient CLI error should be classified and retried with bounded backoff. It must not be interpreted as “plug is off”. A repeated read failure should first be reproduced with the exact allowlisted read-only command in the service-equivalent sandbox. Do not toggle power as a diagnostic action, particularly when the plug powers a display or other equipment.
