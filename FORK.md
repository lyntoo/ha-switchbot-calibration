# Differences from Home Assistant Core's `switchbot` integration

Base: Home Assistant Core 2026.10.1 (`homeassistant/components/switchbot`), kept unmodified in `upstream-baseline/2026.10.1/`.
Compare at any time: `diff -ru upstream-baseline/2026.10.1 custom_components/switchbot`.

| File | Change |
|---|---|
| `blind_tilt_calibration.py` | NEW: `CalibratableBlindTilt` (subclass of PySwitchbot's `SwitchbotBlindTilt`): calibration commands, status frames, timeouts, safe abort |
| `blind_tilt_entities.py` | NEW: diagnostic binary sensor "Calibration in progress" and sensor "Calibration step" |
| `__init__.py` | the Blind Tilt model is instantiated as `CalibratableBlindTilt` |
| `cover.py` | calibration services; open/close/position refused while calibrating or uncalibrated; repair issue "Blind not calibrated" |
| `config_flow.py` | calibration wizard in the options flow (menu Settings / Calibrate) |
| `sensor.py`, `binary_sensor.py` | register the two diagnostic entities for the Blind Tilt |
| `services.yaml` | calibration services (end of file) |
| `translations/en.json`, `fr.json` | wizard, errors, repair issue, exceptions, services |
| `manifest.json` | domain kept (`switchbot`) so existing config entries and entity IDs keep working; version, codeowner, documentation, issue tracker; `quality_scale` removed |
| `quality_scale.yaml` | removed (not applicable to a custom component) |

Every other file is identical to Core. Other SwitchBot devices behave exactly as in Core.
