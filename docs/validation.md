# Validation notes

## Real-hardware reference

The reference implementation was exercised on an Armbian/BlueZ Linux host with a stock-firmware Xiaomi/Mijia Temperature and Humidity Monitor 3 Mini (`MJWSD06MMC`, product ID `0x55B5`). The receiver decoded FE95 advertisements into local temperature and humidity observations without a GATT connection or Mesh gateway. During the documented live checks, the receiver remained active, advertisement and parsed-frame counters advanced, and `parse_error_count` stayed at zero. Exact MAC addresses, readings, timestamps, and production logs are deliberately omitted.

## Phone observer and retry behavior

The reference host showed thousands of repeated `Host is down (112)` and `Adv Monitor app ... disconnected from D-Bus` records while the old observer spawned short-lived `bluetoothctl connect` commands at a fixed retry interval. The replacement uses the targeted `org.bluez.Device1.ConnectProfile` D-Bus method and 5/10/20/40/60-second bounded backoff. After deploying it, the paired phone remained connected, the observer reported zero reconnect attempts, and no new `Host is down` errors appeared in the observation window. Three `Adv Monitor app` disconnect entries in that window aligned with three manually run `bluetoothctl show` diagnostic commands; no further `bluetoothctl` commands were used after that finding.

The reconnect failure path was validated with unit tests and simulated D-Bus errors. A deliberate phone disconnect was not forced during acceptance because that could affect a live presence-controlled device. Test results therefore establish the method selection, error classification, and backoff logic, not universal radio compatibility with every phone.

## Plug actuator boundary

The reference actuator's historical read failure was not reproduced during a fresh comparison: the exact allowlisted read-only switch-property query succeeded both outside and inside a transient systemd sandbox matching the relevant production restrictions. No plug write was issued for diagnosis. The historical root cause remains unconfirmed; the project recommends repeating this differential test only if the failure recurs.

## Automated checks

The repository test suite checks the D-Bus call path, bounded retry schedule, sanitized error classes, and public-tree privacy rules. These checks do not replace testing with the actual sensor, phone, Bluetooth adapter, and target smart plug.
