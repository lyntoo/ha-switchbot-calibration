# Changelog

## 1.0.0
- First public release, based on Home Assistant Core 2026.10.1 (PySwitchbot 2.9.0).
- SwitchBot Blind Tilt: guided calibration wizard (Settings > Devices & services > SwitchBot > the blind > Configure > Calibrate the blind), English and French.
- Movement commands (open, close, position) refused while a calibration runs and while a blind reports itself uncalibrated; a repair issue explains why.
- Diagnostic entities "Calibration in progress" and "Calibration step"; services `switchbot.calibration_start|save|finish|abort|status`.
