# Blind Tilt calibration: observed protocol

Observed on SwitchBot Blind Tilt units (Bluetooth LE, no password) by sniffing the Bluetooth traffic of the official app
during real calibrations of the author's own devices, then replayed from a computer. Not official documentation: it may
change with firmware. Characteristics: write `cba20002-224d-11e6-9fb8-0002a5d5c51b`, notify `cba20003-224d-11e6-9fb8-0002a5d5c51b`.

| Purpose | Bytes (hex) | Reply |
|---|---|---|
| Start (erases the current calibration) | `57 0F 45 05 01` | `01` |
| Stream status frames | `57 0E 01 00 0A FF FF 46 09 00` | `01`, then frames |
| Save the current rod position | `57 0F 45 05 09 <p>` with p = 1, 2, 3, 5 | `01 ..` |
| End | `57 0F 45 05 02` | `01 ..` |
| Stop the stream | `57 0E 00` | `01` |
| Read state | `57 0F 46 05` | `01 <flags> ...` (`flags & 0x04` = calibrated) |

Status frame: `0F b7 00 vv`: `b7 & 0x0F` = step, `b7 & 0x10` = ready, `b7 & 0x20` = exit, `b7 & 0x40` = direction error, `vv` = position counter.

Sequence used by the wizard: start, stream, then four times (turn the rod, wait for ready, save p = 1, 2, 3, 5 for closed down / open / closed up / open), end, stop stream, read state.

Known limitation: no command to write calibration values back was found, so an abandoned calibration leaves the blind uncalibrated until a full calibration is redone.
