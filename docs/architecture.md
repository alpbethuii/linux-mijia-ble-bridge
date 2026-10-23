# Architecture and implementation notes

## 1. Sensor data path: advertisements, not a session

BLE advertisements are small, periodic packets. A receiver can observe them without creating a GATT connection, and Bluetooth Mesh is not a prerequisite for ordinary BLE advertisements. This is useful for sleepy, battery-powered sensors that broadcast measurements but do not accept connections while idle.

For the tested MJWSD06MMC, the receiver filters the target device address and the FE95 service-data UUID, checks the clear product-ID field (`0x55B5`), and passes only the matching payload to `xiaomi-ble`. The parser uses the locally supplied bindkey when the advertisement is encrypted. The receiver records decoded values and timestamps; it does not save raw payloads or key material.

## 2. Why the bindkey matters

Some MiBeacon v4/v5 advertisements use authenticated AES encryption. Seeing a packet in a scanner proves only radio reception; it does not prove that the measurement can be decoded. A valid key, the correct model/product identifier, and a compatible parser are separate requirements. A parse/decryption error should not be “fixed” by changing firmware or repeatedly pairing the sensor until the evidence supports that choice.

## 3. Phone presence is a separate Bluetooth profile

The optional observer checks `org.bluez.Device1.Connected` and requests the specific AVRCP profile via `org.bluez.Device1.ConnectProfile`. It does not issue a generic `Connect()` call, which can attempt multiple profiles. It uses 5/10/20/40/60-second bounded backoff, classifies common errors, and limits repetitive failure logs.

A short-lived `bluetoothctl` process can create extra BlueZ client lifecycle noise in some deployments. In the reference deployment, repeated invocations correlated closely with repeated `Adv Monitor app ... disconnected from D-Bus` records. Prefer the narrow D-Bus method for an automated retry loop and verify the result from the `Connected` property.

## 4. Keep the actuator out of the observer

The observer writes state only. Any future smart-plug automation should be split into:

1. **Observer:** raw Bluetooth state and timestamps; unknown is not “away”.
2. **Shadow/debounce layer:** stable intent after separate connect/disconnect windows; no device writes.
3. **Actuator:** verify fresh inputs, exact target home/model/device identity, LAN reachability and fault status, physical-load assumptions, then write and read back the actual state.

A stale input, identity mismatch, CLI error, malformed JSON, or uncertain readback must fail closed. Never toggle a live plug just to diagnose a read error.
