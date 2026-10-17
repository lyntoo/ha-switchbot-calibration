"""Test, without Home Assistant or Bluetooth, of the CalibratableBlindTilt state machine (simulated commands, injected frames).
Usage : pip install PySwitchbot==2.9.0 && python tests/test_calibration.py"""
import asyncio
import importlib.util
import os
import sys
import types

ICI = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ICI, "..", "custom_components", "switchbot", "blind_tilt_calibration.py")
spec = importlib.util.spec_from_file_location("blind_tilt_calibration", SRC)
bt = importlib.util.module_from_spec(spec)
sys.modules["blind_tilt_calibration"] = bt
spec.loader.exec_module(bt)


class Faux(bt.CalibratableBlindTilt):
    def __init__(self):  # no BLE: bypass the library's constructor
        self._calibration_active = False
        self._stream_on = False
        self._calibration_frame = None
        self._calibration_callbacks = []
        self.delai = 0
        self.erreurs = {}
        self.envoyes = []
        self.reponses = {}
        self.timers = 0

    async def _send_command(self, key, retry=None):
        self.envoyes.append(key)
        if self.delai:
            await asyncio.sleep(self.delai)
        if key in self.erreurs:
            raise self.erreurs[key]
        return self.reponses.get(key, b"\x01")

    def _reset_disconnect_timer(self):
        self.timers += 1


def trame(step, pret=False, erreur=False, sortie=False, val=0):
    b7 = step | (0x10 if pret else 0) | (0x20 if sortie else 0) | (0x40 if erreur else 0)
    return bytearray([0x0F, b7, 0x00, val])


async def main():
    d = Faux()
    d.reponses["570f4605"] = bytes.fromhex("010400fd020536011c")
    # frames ignored before the start (the library's normal handling is not tested here)
    assert not d.calibration_active
    await d.start_calibration()
    assert d.envoyes == ["570f450501", "570e01000affff460900"], d.envoyes
    assert d.calibration_active
    # save refused without a frame, then out of step, then on direction error, then not ready
    for tentative in (lambda: d.save_calibration_position(0),):
        try:
            await tentative(); raise SystemExit("should have failed (no frame)")
        except bt.CalibrationError:
            pass
    d._notification_handler(0, trame(0, pret=False))
    try:
        await d.save_calibration_position(0); raise SystemExit("should have failed (not ready)")
    except bt.CalibrationError:
        pass
    d._notification_handler(0, trame(0, pret=True, erreur=True))
    try:
        await d.save_calibration_position(0); raise SystemExit("should have failed (direction error)")
    except bt.CalibrationError:
        pass
    attendus = ["570f45050901", "570f45050902", "570f45050903", "570f45050905"]
    for k in range(bt.STEP_COUNT):
        d._notification_handler(0, trame(k, pret=True, val=10 + k))
        f = await d.wait_step_ready(k, timeout=2)
        assert f.counter == 10 + k and f.ready and not f.direction_error
        try:  # saving for another step must fail
            await d.save_calibration_position((k + 1) % bt.STEP_COUNT); raise SystemExit("should have failed (wrong step)")
        except bt.CalibrationError:
            pass
        await d.save_calibration_position(k)
        assert d.envoyes[-1] == attendus[k], (k, d.envoyes[-1])
    assert d.timers == 6, d.timers  # one timer reset per frame (6 injected frames)
    calibre = await d.stop_calibration()
    assert calibre is True and not d.calibration_active
    assert d.envoyes[-3:] == ["570f450502", "570e00", "570f4605"], d.envoyes[-3:]
    # abort: stop + end of stream, internal state reset
    d2 = Faux(); await d2.start_calibration(); await d2.abort_calibration()
    assert d2.envoyes[-2:] == ["570f450502", "570e00"] and not d2.calibration_active
    # blind refuses at start -> error and clean state
    d3 = Faux(); d3.reponses["570f450501"] = b"\x00"
    try:
        await d3.start_calibration(); raise SystemExit("should have failed (refusal)")
    except bt.CalibrationError:
        pass
    assert not d3.calibration_active
    # stream refused -> automatic abandon (stop sent)
    d4 = Faux(); d4.reponses["570e01000affff460900"] = b"\x00"
    try:
        await d4.start_calibration(); raise SystemExit("should have failed (stream refused)")
    except bt.CalibrationError:
        pass
    assert not d4.calibration_active and "570f450502" in d4.envoyes
    # poll_needed blocked during the calibration
    d5 = Faux(); d5._calibration_active = True
    assert d5.poll_needed(1000.0) is False
    # abort: the interface is released AT ONCE even if the blind does not answer; the error is raised
    d6 = Faux(); await d6.start_calibration()
    d6.erreurs["570f450502"] = OSError("liaison perdue")
    try:
        await d6.abort_calibration(); raise SystemExit("should have reported that the blind did not confirm")
    except bt.CalibrationError as err:
        assert "not confirmed" in str(err) and err.reason == "stop_unconfirmed", err
    assert not d6.calibration_active and not d6._stream_on
    # time limit exceeded: the command is interrupted, no blocking
    bt.COMMAND_TIMEOUT = 0.2
    d7 = Faux(); await d7.start_calibration(); d7.delai = 1.0
    try:
        await d7.abort_calibration(); raise SystemExit("devait expirer")
    except bt.CalibrationError as err:
        assert "time limit" in str(err) and err.reason == "stop_unconfirmed", err
    assert not d7.calibration_active
    # 0F frames are absorbed while the stream is on, even during the stop (never taken for a reply)
    d8 = Faux(); await d8.start_calibration()
    d8._calibration_active = False  # stop in progress
    d8._notification_handler(0, trame(2, pret=True))
    assert d8.calibration_frame is None and d8._stream_on
    print("ALL TESTS PASS")

asyncio.run(main())
