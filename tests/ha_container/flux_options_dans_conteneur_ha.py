"""Simulates the calibration wizard (options flow) with a FAKE blind, inside Home Assistant's interpreter.
Run: docker exec -i homeassistant python3 - < this file (component copied to /tmp/forktest/custom_components/switchbot)."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, "/tmp/forktest")
from custom_components.switchbot import blind_tilt_calibration as bt
from custom_components.switchbot import config_flow as cf


class Faux(bt.CalibratableBlindTilt):
    def __init__(self):
        self._calibration_active = False
        self._stream_on = False
        self._calibration_frame = None
        self._calibration_callbacks = []
        self.envoyes = []
        self.reponses = {"570f4605": bytes.fromhex("010400f901fe37011c")}
        self.erreurs = {}

    async def _send_command(self, key, retry=None):
        self.envoyes.append(key)
        if key in self.erreurs:
            raise self.erreurs[key]
        return self.reponses.get(key, b"\x01")

    def _reset_disconnect_timer(self):
        pass


def trame(step, pret=False, erreur=False, val=0):
    b7 = step | (0x10 if pret else 0) | (0x40 if erreur else 0)
    return bytearray([0x0F, b7, 0, val])


def verifier_etapes(f, r):
    """HA raises UnknownStep if a displayed step_id has no method: check every result."""
    if isinstance(r, dict) and r.get("type") in ("form", "menu"):
        assert hasattr(f, f"async_step_{r['step_id']}"), f"missing method for step {r['step_id']}"
    return r


def fabriquer(device):
    entree = SimpleNamespace(data={"sensor_type": "blind_tilt"}, title="Test Blind", entry_id="abc", runtime_data=SimpleNamespace(device=device), options={})
    class Flux(cf.SwitchbotOptionsFlowHandler):
        config_entry = entree  # replaces HA's property
    for nom in [n for n in dir(Flux) if n.startswith("async_step_")]:
        original = getattr(Flux, nom)
        def enveloppe(self, *a, _o=original, **k):
            async def go():
                return verifier_etapes(self, await _o(self, *a, **k))
            return go()
        setattr(Flux, nom, enveloppe)
    f = Flux()
    f.hass = SimpleNamespace(config=SimpleNamespace(language="fr"), async_create_task=lambda c: asyncio.ensure_future(c))
    f.flow_id = "f1"
    f.handler = "abc"
    return f


async def main():
    notifs = []
    with patch.object(cf.persistent_notification, "async_create", lambda hass, msg, **kw: notifs.append((msg, kw.get("notification_id")))), \
         patch.object(cf.persistent_notification, "async_dismiss", lambda hass, nid: notifs.append(("DISMISS", nid))):
        # 1) menu d'accueil pour un Blind Tilt
        d = Faux(); f = fabriquer(d)
        r = await f.async_step_init()
        assert r["type"] == "menu" and r["menu_options"] == ["settings", "calibrate"], r
        # 2) start screen: state as a language-neutral symbol
        r = await f.async_step_calibrate()
        assert r["type"] == "form" and r["step_id"] == "calibrate" and r["description_placeholders"]["state"] == "✅", r
        # 3) submit -> starts -> step 1 form (single button), "in progress" notification
        r = await f.async_step_calibrate({})
        assert d.calibration_active and d.envoyes[-2:] == ["570f450501", "570e01000affff460900"], d.envoyes
        assert r["type"] == "form" and r["step_id"] == "etape_1", r
        assert notifs and notifs[-1][1] == "switchbot_calibration_abc"
        # 4) Next too early (no frame) -> not_at_step error, stay on step 1, nothing saved
        r = await f.async_step_etape_1({})
        assert r["step_id"] == "etape_1" and r["errors"] == {"base": "not_at_step"}, r
        # 5) not ready -> not_ready; ready but direction error -> direction_error
        d._notification_handler(0, trame(0, pret=False, val=3))
        r = await f.async_step_etape_1({})
        assert r["errors"] == {"base": "not_ready"}, r
        d._notification_handler(0, trame(0, pret=True, erreur=True, val=3))
        r = await f.async_step_etape_1({})
        assert r["errors"] == {"base": "direction_error"}, r
        assert not any(e.startswith("570f450509") for e in d.envoyes), d.envoyes
        # 6) ready -> saves preset 1 -> step 2
        d._notification_handler(0, trame(0, pret=True, val=16))
        r = await f.async_step_etape_1({})
        assert d.envoyes[-1] == "570f45050901" and r["step_id"] == "etape_2", (d.envoyes, r)
        # 7) closing the window does NOT abandon the calibration (no async_remove): it can be resumed
        assert d.calibration_active
        d._notification_handler(0, trame(1, pret=True, val=137))
        f2 = fabriquer(d)  # window reopened
        r = await f2.async_step_calibrate()
        assert r["type"] == "menu" and r["step_id"] == "reprise" and r["menu_options"] == ["continue_calibration", "restart_calibration"], r
        r = await f2.async_step_continue_calibration()
        assert r["step_id"] == "etape_2", r
        # 8) Start over: new session, step 1, no extra preset saved
        f3 = fabriquer(d)
        r = await f3.async_step_restart_calibration()
        assert r["step_id"] == "etape_1" and d.calibration_active and d.envoyes[-2:] == ["570f450501", "570e01000affff460900"], (r, d.envoyes[-4:])
        # 9) full run to "Calibration complete" (+ notification dismissed)
        d4 = Faux(); f4 = fabriquer(d4)
        await f4.async_step_calibrate({})
        etapes = [f4.async_step_etape_1, f4.async_step_etape_2, f4.async_step_etape_3, f4.async_step_etape_4]
        for k in range(bt.STEP_COUNT):
            d4._notification_handler(0, trame(k, pret=True, val=10 + k))
            r = await etapes[k]({})
        assert r["type"] == "abort" and r["reason"] == "calibration_done", r
        assert d4.envoyes[-3:] == ["570f450502", "570e00", "570f4605"], d4.envoyes[-3:]
        assert [e for e in d4.envoyes if e.startswith("570f450509")] == ["570f45050901", "570f45050902", "570f45050903", "570f45050905"]
        assert notifs[-1] == ("DISMISS", "switchbot_calibration_abc"), notifs[-1]
        # 10) failure at start -> reason translated by cause + notification
        d5 = Faux(); d5.erreurs["570f450501"] = OSError("pas de liaison"); f5 = fabriquer(d5)
        r = await f5.async_step_calibrate({})
        assert r["type"] == "abort" and r["reason"] == "calibration_failed_comm" and "pas de liaison" in r["description_placeholders"]["detail"], r
        # 11) blind answering "not calibrated" at the end -> dedicated reason
        d6 = Faux(); d6.reponses["570f4605"] = bytes.fromhex("01000000ffff000000"); f6 = fabriquer(d6)
        await f6.async_step_calibrate({})
        for k, step in enumerate([f6.async_step_etape_1, f6.async_step_etape_2, f6.async_step_etape_3, f6.async_step_etape_4]):
            d6._notification_handler(0, trame(k, pret=True, val=5))
            r = await step({})
        assert r["reason"] == "calibration_failed_not_calibrated", r
        # 12) store qui n'est PAS un Blind Tilt : pas de menu, formulaire d'origine
        f7 = fabriquer(d); f7.config_entry.data["sensor_type"] = "bot"
        r = await f7.async_step_init()
        assert r["type"] == "form" and r["step_id"] == "init", r
        # 13) translation keys present for every step and error used (en and fr)
        import json, os
        base = "/tmp/forktest/custom_components/switchbot/translations"
        for lang in ("en", "fr"):
            o = json.load(open(f"{base}/{lang}.json", encoding="utf-8"))["options"]
            for k in ("calibrate", "reprise", "etape_1", "etape_2", "etape_3", "etape_4", "init"):
                assert k in o["step"], (lang, k)
            for k in ("not_ready", "direction_error", "not_at_step", "save_failed"):
                assert k in o["error"], (lang, k)
            for k in ("calibration_done", "calibration_failed", "calibration_unavailable") + tuple(f"calibration_failed_{x}" for x in cf.CALIBRATION_FAILURE_REASONS):
                assert k in o["abort"], (lang, k)
    print("ALL WIZARD TESTS PASS")

asyncio.run(main())
