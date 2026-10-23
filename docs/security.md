# Security and privacy

- Keep real BLE MAC addresses and bindkeys in local configuration files only. `.env.example` uses invalid placeholders.
- Store the 16-byte bindkey as 32 hexadecimal characters, root-owned with mode `0600`; its parent directory should be root-owned and inaccessible to other users.
- Never commit `.env`, the bindkey file, sensor state JSON, raw BLE captures, screenshots, or unredacted journal output.
- The sensor state file includes the configured sensor address. Keep the state directory private and redact the address before sharing a diagnostic snapshot.
- Do not include Xiaomi account tokens, cloud credentials, plug IDs, home IDs, private LAN addresses, or real household names in public issue reports.
- The phone observer does not control a plug. Any actuator should use explicit allowlists, freshness and identity gates, fail-closed behavior, and verified readback.
- The examples are intended for devices and accounts you own or are authorized to administer.
