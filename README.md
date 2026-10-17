# SwitchBot Bluetooth with Blind Tilt calibration

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lyntoo&repository=ha-switchbot-calibration&category=integration)

> **This is a customized copy of Home Assistant's official *SwitchBot Bluetooth* integration, with a few additions.**
> It is **unofficial** and **not affiliated with SwitchBot or the Home Assistant project**. It is based on Home Assistant Core
> 2026.10.1 and replaces the built-in integration (same domain `switchbot`), so your existing SwitchBot devices, entity IDs and
> settings keep working. Everything else behaves exactly like the official integration.

## What is added

SwitchBot **Blind Tilt** only (other SwitchBot devices are untouched):

- **Calibration wizard** in Home Assistant: *Settings > Devices & services > SwitchBot > your blind > Configure > Calibrate the blind*.
  Four guided steps with a single **Next** button; it refuses to continue if the blind is not ready or reports a direction error. In English and French.
- **Safety lock:** while a calibration runs, and while a blind reports itself **uncalibrated**, open / close / position commands are refused
  (percentages would be unreliable and movements risky). A **repair issue** explains why and how to fix it.
- Two diagnostic entities per blind: **Calibration in progress** and **Calibration step**.
- Services for advanced use: `switchbot.calibration_start`, `calibration_save`, `calibration_finish`, `calibration_abort`, `calibration_status`.

Until now the calibration could only be done with the SwitchBot phone app.

## Install

1. Click the **Add to HACS** button above (or in HACS: *Custom repositories* > this repository > category *Integration*).
2. Download, then **restart Home Assistant**.
3. Nothing to configure: your existing SwitchBot devices are picked up as they are. To go back to the official integration, remove
   `custom_components/switchbot` and restart (see [docs/UPSTREAM.md](docs/UPSTREAM.md)).

Home Assistant shows a normal "custom integration" notice; this is expected. Requires Home Assistant 2026.10 or newer (tested on 2026.10.1).

## Important

- **Starting a calibration ERASES the blind's current calibration**, and it cannot be restored: no command to write the values back
  exists in the blind's protocol. There is no cancel. Once started, finish the four steps; if something goes wrong, start over from the beginning.
- An abandoned calibration leaves the blind **uncalibrated**; Home Assistant then refuses its movement commands until it is calibrated again.
- Turn the rod **in one direction only** at each step and stop before pressing Next.
- Use at your own risk. See the license: this software is provided "as is", without warranty.
- After updating, **hard-refresh your browser** (Ctrl+Shift+R) so the wizard's texts reload.
- A good Bluetooth signal between the blind and your adapter or proxy matters during the calibration (about 2 minutes).

## Tested with

SwitchBot Blind Tilt units on Home Assistant 2026.10.1 with ESPHome Bluetooth proxies. The protocol was observed on real devices; see
[docs/PROTOCOL.md](docs/PROTOCOL.md). Firmware changes may alter it.

## Relationship with Home Assistant Core

This copy does not follow Home Assistant's own updates automatically. A weekly workflow compares Core's integration with the baseline kept in
`upstream-baseline/` and opens an issue when they differ. Differences from Core are listed in [FORK.md](FORK.md).

## License and credits

Apache License 2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)). Based on the Home Assistant Core `switchbot` integration and its many
contributors; uses the [PySwitchbot](https://github.com/sblibs/pySwitchbot) library (MIT). "SwitchBot" is a trademark of its owner.
