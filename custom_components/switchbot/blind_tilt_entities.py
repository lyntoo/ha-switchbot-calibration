"""Diagnostic entities for the SwitchBot Blind Tilt calibration.

- binary sensor "Calibration in progress";
- sensor "Calibration step" (idle / step_1 to step_4; ready, direction error and counter as attributes).
The calibration itself is driven from the options flow wizard (config_flow.py); these entities only report the state."""
# New file (Apache-2.0): Blind Tilt calibration diagnostic entities.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .blind_tilt_calibration import STEP_COUNT, CalibratableBlindTilt
from .coordinator import SwitchbotDataUpdateCoordinator
from .entity import SwitchbotEntity

STEP_OPTIONS = ["idle"] + [f"step_{i}" for i in range(1, STEP_COUNT + 1)]


class _CalibrationEntity(SwitchbotEntity):
    """Base: subscribe to calibration frames and rewrite the state only when it changes."""

    _device: CalibratableBlindTilt
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _key: str = ""

    def __init__(self, coordinator: SwitchbotDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.base_unique_id}-{key}"
        self._last_view: Any = None

    def _view(self) -> Any:
        """Return the watched value: the state is rewritten only when it changes."""
        return None

    @callback
    def _on_calibration_change(self) -> None:
        view = self._view()
        if view != self._last_view:
            self._last_view = view
            self.async_write_ha_state()

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._device.subscribe_calibration(self._on_calibration_change))


class BlindTiltCalibrationActiveBinarySensor(_CalibrationEntity, BinarySensorEntity):
    """True while a calibration is in progress."""

    def __init__(self, coordinator: SwitchbotDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "calibration_active")

    def _view(self) -> Any:
        return self._device.calibration_active

    @property
    @override
    def is_on(self) -> bool:
        return self._device.calibration_active


class BlindTiltCalibrationStepSensor(_CalibrationEntity, SensorEntity):
    """Current calibration step (idle, step_1 to step_4)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STEP_OPTIONS

    def __init__(self, coordinator: SwitchbotDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "calibration_step")

    def _view(self) -> Any:
        frame = self._device.calibration_frame
        if not self._device.calibration_active or frame is None:
            return None
        return (frame.step, frame.ready, frame.direction_error)

    @property
    @override
    def native_value(self) -> str:
        frame = self._device.calibration_frame
        if not self._device.calibration_active or frame is None:
            return "idle"
        return f"step_{min(frame.step + 1, STEP_COUNT)}"

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, Any]:
        attrs = dict(super().extra_state_attributes)
        frame = self._device.calibration_frame
        if self._device.calibration_active and frame is not None:
            attrs["ready"] = frame.ready
            attrs["direction_error"] = frame.direction_error
            attrs["counter"] = frame.counter
        return attrs
