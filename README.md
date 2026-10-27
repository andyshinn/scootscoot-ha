# ScootScoot for Home Assistant

Monitor a Segway-Ninebot scooter's battery and ride telemetry over Bluetooth,
locally, with no cloud account. Built on the
[scootscoot](https://github.com/andyshinn/scootscoot) library and validated
against a Segway SuperScooter GT3 Pro.

Read-only: it never writes to the scooter and never pairs with it.

## Requirements

- Home Assistant 2026.10 or newer.
- A Bluetooth path to where the scooter parks or charges: an
  [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html)
  (active connections enabled) or an adapter on the Home Assistant host.
- The scooter's 16-byte Bluetooth password, extracted from the Segway app. See
  [Getting the password](#getting-the-password).

## Install

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/andyshinn/scootscoot-ha` with type **Integration**.
3. Install **ScootScoot** and restart Home Assistant.
4. The scooter should appear under **Settings → Devices & services** as
   discovered. If not, choose **Add integration → ScootScoot** and pick it from
   the list. It advertises its serial number (for example `03GGG2527C0165`) as
   its Bluetooth name.
5. Choose the model and paste the password.

Setup connects once to check the password before creating the entry.

## Getting the password

A scooter paired with the Segway app holds a password the app negotiated, and it
**silently ignores** any attempt to set another. This integration therefore asks
for the existing one instead of pairing.

On iOS the password is the `{SERIAL}_decrypt` key in
`Library/Preferences/com.ninebot.segway.plist` inside the Segway app's container
(a preferences key, not a file); a device backup is one way to reach it. Enter it
as 32 hexadecimal characters or as the 24-character base64 the plist uses in its
XML form. Spaces, colons and a `0x` prefix are ignored.

If the scooter is later reset and paired with the app again, the password
changes. Home Assistant will prompt you to re-enter it.

## What you get

On a parked scooter only the battery management system and the Bluetooth radio
are awake, so battery data stays current while ride data (odometer, range,
temperatures, error codes) is the last value read while the scooter was on.

| Entity | Notes |
|---|---|
| Battery | State of charge from the BMS |
| Battery voltage, current, power | Current and power are negative while charging |
| Remaining capacity | Ah |
| Charging | Current below −0.5 A. The pack reads about −0.06 A at rest |
| Time to full | Raw BMS value in minutes; its meaning while not charging is unverified |
| Battery temperature max / min | Across the pack's 8 probes |
| Cell voltage max / min / delta | Across the 20 cells (diagnostic) |
| Odometer, estimated range | Last value seen with the scooter on |
| Powered on | Whether the scooter was switched on at the last poll |
| Fault | On when the error code is non-zero |
| Connection, last seen | Whether the last poll got through, and when one last did |

Disabled by default: one sensor per cell and per temperature probe, and the
dashboard battery percentage. Diagnostic: full-charge capacity, charge cycles,
body and motor-controller temperatures, raw error/warning/charge-status codes.

## How polling works

Each poll connects, authenticates, reads, and disconnects. Nothing is held
between polls, because the scooter accepts **one Bluetooth connection at a
time**. While a poll runs (a few seconds), the Segway app will see the scooter
as busy; if the app holds the connection, that poll is skipped.

- Every **15 minutes** while parked; every **2 minutes** while charging or
  switched on. Both are adjustable under **Configure**.
- If no adapter or proxy can see the scooter, no connection is attempted. The
  next advertisement triggers a poll straight away, so a scooter coming home
  updates promptly.
- Failed polls keep the last readings. A reading becomes unavailable after
  **24 hours** without a fresh one (adjustable; 0 keeps it forever).
- The last readings are saved, so a restart does not blank the sensors.

## Troubleshooting

Download diagnostics from the device page; the password is redacted. For debug
logs:

```yaml
logger:
  logs:
    custom_components.scootscoot: debug
    scootscoot: debug
```

The library withholds frame bodies at debug level on purpose. Do not enable
`scootscoot.transport.wire`, and never post its output: it is enough to recover
the password.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

## Disclaimer

Not affiliated with, authorized by, or endorsed by Segway Inc. or Ninebot.
"Segway" and "Ninebot" are trademarks of their respective owners, used here
only to identify the hardware this integration talks to.

## License

Apache-2.0. See [LICENSE](LICENSE).
