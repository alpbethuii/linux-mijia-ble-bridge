# Diagnostics and error classification

## Sensor receiver

- `status=ok`: both temperature and humidity have recent decoded observations.
- `status=partial`: only one measurement is fresh.
- `status=stale`: no target advertisement has been observed within the configured freshness window.
- `status=waiting_for_broadcast`: receiver started but has not seen a matching advertisement yet.
- `status=configuration_error` or `local_config_*`: fix the local MAC/product ID/bindkey path or bindkey permissions; do not paste the bindkey into logs or issues.
- `parse_error_count > 0`: inspect the sanitized `last_parse_error` class, confirm the target MAC and product ID, then verify the local bindkey is exactly 32 hexadecimal characters with restrictive permissions. Do not expose payloads or key material when asking for help.

Useful read-only checks:

```bash
systemctl is-active bluetooth.service mijia-ble-receiver.service
busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered
busctl --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discovering
jq '{status, last_seen_at, temperature_observed_at, humidity_observed_at, advertisement_count, parsed_frame_count, parse_error_count, last_parse_error}' /var/lib/mijia-ble-bridge/advertisement-state.json
journalctl -u mijia-ble-receiver.service --since '30 minutes ago' --no-pager
```

`bluetoothctl` is useful interactively, but avoid launching it in a tight automated polling loop. Prefer `busctl` for property reads.

## Phone presence observer

The observer stores only sanitized categories such as `remote_unreachable`, `bluez_not_ready`, `profile_not_available`, `in_progress`, `permission_denied`, `timeout`, or `dbus_error`; it never stores raw D-Bus error text. Retry delays are bounded at 60 seconds. `Host is down (112)` usually indicates that the peer was temporarily unreachable; it is not by itself proof of a defective adapter.

Read-only checks:

```bash
systemctl is-active bluetooth.service phone-presence-observer.service
busctl --system get-property org.bluez /org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX org.bluez.Device1 Connected
cat /run/mijia-ble-bridge-presence/state.json
journalctl -u phone-presence-observer.service --since '30 minutes ago' --no-pager
```

Replace the example BlueZ object path with the local paired device path. Do not post real MAC addresses publicly.

## Smart-plug read failures

If a separate actuator's allowlisted read-only property query fails, record return code, duration, timeout status, output byte counts, and a sanitized error class. Compare the exact read-only command outside and inside an equivalent systemd sandbox. Do not run a write command to test connectivity, and do not treat `actual_state=null` or `verified=false` as a physical switch state.
