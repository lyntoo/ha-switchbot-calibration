"""Import test inside Home Assistant's interpreter (run with `docker exec -i homeassistant python3 -` after copying the component
to /tmp/forktest/custom_components/switchbot): checks that every module imports and that the subclass can be built."""
import importlib
import sys

sys.path.insert(0, "/tmp/forktest")
for m in ["blind_tilt_calibration", "blind_tilt_entities", "cover", "sensor", "binary_sensor", "button", "config_flow", "services", "__init__"]:
    name = "custom_components.switchbot" + ("" if m == "__init__" else "." + m)
    importlib.import_module(name)
    print("import OK", m)
from bleak.backends.device import BLEDevice

import custom_components.switchbot as sb
import custom_components.switchbot.blind_tilt_calibration as bt

d = BLEDevice("AA:BB:CC:DD:EE:FF", "WoBlindTilt", -60)
dev = bt.CalibratableBlindTilt(device=d, retry_count=2, reverse_mode=True)
print("constructor OK:", dev.calibration_active, dev.calibration_frame)
print("CLASS_BY_DEVICE blind_tilt ->", sb.CLASS_BY_DEVICE["blind_tilt"].__name__)
