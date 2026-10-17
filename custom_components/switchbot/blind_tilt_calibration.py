"""Calibration of the SwitchBot Blind Tilt (added to the Home Assistant `switchbot` integration).

Protocol observed on real devices by sniffing the Bluetooth traffic of the official app during calibrations, then replayed
from a computer (see docs/PROTOCOL.md). Full bytes, 0x57 prefix included:

  57 0F 45 05 01                  start the calibration             -> reply 01 (ERASES the current calibration)
  57 0E 01 00 0A FF FF 46 09 00   the blind streams status frames   -> reply 01, then frames 0F b7 00 vv
  57 0F 45 05 09 <p>              save the rod position, p = 1, 2, 3, 5 (one per step)
  57 0F 45 05 02                  end the calibration
  57 0E 00                        stop the stream
  57 0F 46 05                     read the state (byte [1] & 0x04 = calibrated)

Status frame 0F b7 00 vv: step = b7 & 0x0F; b7 & 0x10 = ready; b7 & 0x20 = exit; b7 & 0x40 = direction error;
vv = position counter that moves while the rod is turned.

An abandoned calibration leaves the blind NOT calibrated (state byte 0x00) and its previous values are lost: the whole
calibration must be redone. No command to write the values back is known.
"""
# New file (Apache-2.0): Blind Tilt calibration protocol and state machine.

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from typing import Any

import switchbot

_LOGGER = logging.getLogger(__name__)

CMD_START = "570f450501"
CMD_ENABLE_STREAM = "570e01000affff460900"
CMD_SAVE = "570f450509"
CMD_STOP = "570f450502"
CMD_DISABLE_STREAM = "570e00"
CMD_READ_STATE = "570f4605"

# Presets saved at each step: closed down, open, closed up, open (preset 4 is not used).
PRESETS = (1, 2, 3, 5)
STEP_COUNT = len(PRESETS)
FRAME_HEADER = 0x0F
# A command must never block the interface longer than this (weak Bluetooth link).
COMMAND_TIMEOUT = 25.0
STEP_TIMEOUT = 600.0


class CalibrationError(Exception):
    """Error during the calibration of a blind; `reason` lets the UI show a translated message."""

    def __init__(self, message: str, reason: str = "generic") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class CalibrationFrame:
    """Status frame streamed by the blind during the calibration."""

    step: int
    ready: bool
    exit_flag: bool
    direction_error: bool
    counter: int


class CalibratableBlindTilt(switchbot.SwitchbotBlindTilt):
    """SwitchBot Blind Tilt with a calibration feature."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the device and the calibration state."""
        super().__init__(*args, **kwargs)
        # User-visible state: blocks movement commands.
        self._calibration_active = False
        # The blind streams frames (until 57 0E 00 has completed).
        self._stream_on = False
        self._calibration_frame: CalibrationFrame | None = None
        self._calibration_callbacks: list[Callable[[], None]] = []

    @property
    def calibration_active(self) -> bool:
        """Return True while a calibration is in progress."""
        return self._calibration_active

    @property
    def calibration_frame(self) -> CalibrationFrame | None:
        """Return the last calibration frame received."""
        return self._calibration_frame

    def subscribe_calibration(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call `callback` on every new frame; return the unsubscribe function."""
        self._calibration_callbacks.append(callback)

        def _unsubscribe() -> None:
            if callback in self._calibration_callbacks:
                self._calibration_callbacks.remove(callback)

        return _unsubscribe

    def _fire_calibration_callbacks(self) -> None:
        for callback in list(self._calibration_callbacks):
            callback()

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Route calibration frames; everything else follows the library's normal handling.

        While the stream is on (even during the stop), 0F frames must NEVER be taken for
        the reply to a command: they are absorbed here.
        """
        raw = bytes(data)
        if self._stream_on and len(raw) >= 4 and raw[0] == FRAME_HEADER:
            if self._calibration_active:
                b7 = raw[1]
                self._calibration_frame = CalibrationFrame(
                    step=b7 & 0x0F,
                    ready=bool(b7 & 0x10),
                    exit_flag=bool(b7 & 0x20),
                    direction_error=bool(b7 & 0x40),
                    counter=raw[3],
                )
                # Keep the connection open for the whole calibration.
                self._reset_disconnect_timer()
                self._fire_calibration_callbacks()
            return
        super()._notification_handler(_sender, data)

    def poll_needed(self, seconds_since_last_poll: float | None) -> bool:
        """Do not poll the blind during the calibration."""
        if self._calibration_active or self._stream_on:
            return False
        return super().poll_needed(seconds_since_last_poll)

    async def _command(self, key: str) -> bytes:
        """Send a command with a time limit; any communication error becomes a CalibrationError."""
        try:
            reply = await asyncio.wait_for(self._send_command(key), COMMAND_TIMEOUT)
        except TimeoutError as err:
            raise CalibrationError(
                "no answer from the blind within the time limit (weak Bluetooth signal?)",
                "timeout",
            ) from err
        except CalibrationError:
            raise
        except Exception as err:  # noqa: BLE001
            raise CalibrationError(f"communication error: {err}", "comm") from err
        return bytes(reply) if reply else b""

    async def _calibration_command(self, key: str) -> bytes:
        """Send a calibration command and require the success reply (first byte 0x01)."""
        reply = await self._command(key)
        if not reply or reply[0] != 0x01:
            raise CalibrationError(
                f"command {key} refused by the blind (reply {reply.hex() if reply else 'none'})",
                "refused",
            )
        return reply

    async def start_calibration(self) -> None:
        """Start the calibration and the status stream (the current calibration is erased)."""
        if self._calibration_active:
            raise CalibrationError("a calibration is already in progress", "already_active")
        self._calibration_frame = None
        await self._calibration_command(CMD_START)
        self._calibration_active = True
        self._stream_on = True
        self._fire_calibration_callbacks()
        try:
            await self._calibration_command(CMD_ENABLE_STREAM)
        except BaseException:
            try:
                await self._end_calibration()
            except CalibrationError:
                pass
            raise

    async def wait_step_ready(
        self, step: int, timeout: float = STEP_TIMEOUT
    ) -> CalibrationFrame:
        """Wait until the blind reports ready for step `step` (0 to 3)."""
        deadline = time.monotonic() + timeout
        while True:
            if not self._calibration_active:
                raise CalibrationError("calibration interrupted", "not_active")
            frame = self._calibration_frame
            if frame and frame.step == step and frame.ready:
                return frame
            if time.monotonic() > deadline:
                raise CalibrationError(
                    f"the blind did not report ready for step {step + 1}", "not_ready"
                )
            await asyncio.sleep(0.2)

    async def save_calibration_position(self, step: int) -> None:
        """Save the position of step `step` (0 to 3); refused if the blind is not ready or reports a direction error."""
        if not self._calibration_active:
            raise CalibrationError("no calibration in progress", "not_active")
        if not 0 <= step < STEP_COUNT:
            raise CalibrationError(f"invalid step: {step}", "bad_step")
        frame = self._calibration_frame
        if frame is None or frame.step != step:
            raise CalibrationError(
                f"the blind is not at step {step + 1} yet", "not_at_step"
            )
        if frame.direction_error:
            raise CalibrationError("direction error reported by the blind", "direction_error")
        if not frame.ready:
            raise CalibrationError("the blind is not ready for this step yet", "not_ready")
        await self._calibration_command(CMD_SAVE + f"{PRESETS[step]:02x}")

    async def _end_calibration(self) -> None:
        """Hand control back at once (movements accepted again), then stop the calibration on the blind.

        The user-visible state is reset BEFORE the commands are sent: with a bad Bluetooth link the interface is
        never blocked. A stop error is raised so the user knows the blind did not confirm.
        """
        self._calibration_active = False
        self._fire_calibration_callbacks()
        errors: list[str] = []
        try:
            for key in (CMD_STOP, CMD_DISABLE_STREAM):
                try:
                    await self._command(key)
                except CalibrationError as err:
                    errors.append(str(err))
        finally:
            # Last frames that may still arrive.
            await asyncio.sleep(0.3)
            self._stream_on = False
        if errors:
            raise CalibrationError(
                "stop not confirmed by the blind: " + " ; ".join(errors),
                "stop_unconfirmed",
            )

    async def stop_calibration(self) -> bool:
        """End the calibration; return True if the blind reports itself calibrated."""
        if not self._calibration_active:
            raise CalibrationError("no calibration in progress", "not_active")
        await self._end_calibration()
        return await self.read_calibrated()

    async def abort_calibration(self) -> None:
        """Abandon the calibration (the blind stays NOT calibrated until it is redone entirely)."""
        if self._calibration_active:
            await self._end_calibration()

    async def read_calibrated(self) -> bool:
        """Read the calibration state of the blind (byte [1] & 0x04)."""
        reply = await self._command(CMD_READ_STATE)
        if len(reply) < 2 or reply[0] != 0x01:
            raise CalibrationError("cannot read the calibration state", "comm")
        return bool(reply[1] & 0x04)
