"""Tests, inside Home Assistant's interpreter, the lock on commands of an uncalibrated Blind Tilt and the repair issue.
Run: docker exec -i homeassistant python3 - < this file (component copied to /tmp/forktest/custom_components/switchbot)."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, "/tmp/forktest")
from homeassistant.exceptions import ServiceValidationError
from custom_components.switchbot import cover as cv

issues = []
with patch.object(cv.ir, "async_create_issue", lambda hass, dom, iid, **kw: issues.append(("create", iid, kw["translation_key"]))), \
     patch.object(cv.ir, "async_delete_issue", lambda hass, dom, iid: issues.append(("delete", iid))):

    def entite(parsed, active=False):
        e = object.__new__(cv.SwitchBotBlindTiltEntity)
        e.coordinator = SimpleNamespace(device=SimpleNamespace(parsed_data=parsed), device_name="Test Blind")
        e._device = SimpleNamespace(calibration_active=active)
        e._address = "AA:BB:CC:DD:EE:FF"
        e.hass = object()
        return e

    # calibrated: commands allowed, no repair issue
    e = entite({"calibration": True}); e._check_can_move(); e._sync_uncalibrated_issue()
    assert issues[-1][0] == "delete", issues
    # no information: do not block (do not break a blind that does not advertise its state)
    e = entite({}); e._check_can_move()
    # NOT calibrated: movements refused with the translation key, repair issue created
    e = entite({"calibration": False})
    try:
        e._check_can_move(); raise SystemExit("devait refuser")
    except ServiceValidationError as err:
        assert err.translation_key == "blind_tilt_uncalibrated" and err.translation_placeholders == {"name": "Test Blind"}, err
    e._sync_uncalibrated_issue()
    assert issues[-1] == ("create", "blind_tilt_uncalibrated_AA:BB:CC:DD:EE:FF", "blind_tilt_uncalibrated"), issues
    # stop stays allowed outside a calibration, even when uncalibrated
    e._raise_if_calibrating()
    # calibration running: everything refused ("calibration in progress" message), no "not calibrated" repair issue
    e = entite({"calibration": False}, active=True)
    for fonction in (e._check_can_move, e._raise_if_calibrating):
        try:
            fonction(); raise SystemExit("devait refuser")
        except ServiceValidationError as err:
            assert err.translation_key == "blind_tilt_calibrating", err
    e._sync_uncalibrated_issue()
    assert issues[-1][0] == "delete", issues

base = "/tmp/forktest/custom_components/switchbot/translations"
for lang in ("en", "fr"):
    d = json.load(open(f"{base}/{lang}.json", encoding="utf-8"))
    for k in ("blind_tilt_calibrating", "blind_tilt_uncalibrated", "blind_tilt_no_frame"):
        assert k in d["exceptions"], (lang, k)
    assert "{name}" in d["exceptions"]["blind_tilt_uncalibrated"]["message"]
    assert "{name}" in d["issues"]["blind_tilt_uncalibrated"]["title"]
print("ALL SAFETY LOCK TESTS PASS")
