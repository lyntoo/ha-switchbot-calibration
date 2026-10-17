"""Support for SwitchBot curtains."""
# Modified from Home Assistant Core (Apache-2.0): Blind Tilt calibration services; movement commands refused while calibrating or uncalibrated; repair issue.

from collections.abc import Mapping
import logging
from typing import Any, override

import switchbot

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_SPEED,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
    CoverEntityStateAttribute,
)
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform, issue_registry as ir
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .blind_tilt_calibration import STEP_COUNT, CalibrationError
from .const import (
    DOMAIN,
    CONF_CURTAIN_SPEED,
    CURTAIN_3_MODEL_FRIENDLY_NAME,
    CURTAIN_SPEED_TO_VALUE,
    DEFAULT_CURTAIN_SPEED,
    ROLLER_SHADE_SPEED_PERFORMANCE,
    ROLLER_SHADE_SPEED_TO_MODE,
)
from .coordinator import SwitchbotConfigEntry, SwitchbotDataUpdateCoordinator
from .entity import SwitchbotEntity, exception_handler

# Initialize the logger
_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SwitchbotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Switchbot curtain based on a config entry."""
    coordinator = entry.runtime_data
    if isinstance(coordinator.device, switchbot.SwitchbotGarageDoorOpener):
        async_add_entities([SwitchbotGarageDoorOpenerEntity(coordinator)])
    elif isinstance(coordinator.device, switchbot.SwitchbotBlindTilt):
        async_add_entities([SwitchBotBlindTiltEntity(coordinator)])
        # Blind Tilt calibration services (see blind_tilt_calibration.py)
        platform = entity_platform.async_get_current_platform()
        platform.async_register_entity_service("calibration_start", None, "async_calibration_start")
        platform.async_register_entity_service("calibration_save", None, "async_calibration_save")
        platform.async_register_entity_service("calibration_finish", None, "async_calibration_finish")
        platform.async_register_entity_service("calibration_abort", None, "async_calibration_abort")
        platform.async_register_entity_service(
            "calibration_status",
            None,
            "async_calibration_status",
            supports_response=SupportsResponse.ONLY,
        )
    elif isinstance(coordinator.device, switchbot.SwitchbotRollerShade):
        async_add_entities([SwitchBotRollerShadeEntity(coordinator)])
    else:
        async_add_entities([SwitchBotCurtainEntity(coordinator)])


class SwitchBotCurtainEntity(SwitchbotEntity, CoverEntity, RestoreEntity):
    """Representation of a Switchbot."""

    _device: switchbot.SwitchbotCurtain
    _attr_device_class = CoverDeviceClass.CURTAIN
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    _attr_translation_key = "cover"
    _attr_name = None

    def __init__(self, coordinator: SwitchbotDataUpdateCoordinator) -> None:
        """Initialize the Switchbot."""
        super().__init__(coordinator)
        self._attr_is_closed = None
        # Only the Curtain 3 honours the speed byte; expose cover speeds
        # (e.g. silent mode) for that model only.
        if self._device.data.get("modelFriendlyName") == CURTAIN_3_MODEL_FRIENDLY_NAME:
            self._attr_supported_features |= CoverEntityFeature.SPEED
            self._attr_supported_speeds = list(CURTAIN_SPEED_TO_VALUE)

    @callback
    def _get_curtain_speed(self, kwargs: dict[str, Any]) -> int:
        """Return the movement speed for the requested cover speed.

        A speed passed to the action takes precedence; otherwise the speed
        configured in the options flow is used.
        """
        if (speed := kwargs.get(ATTR_SPEED)) is not None:
            # The cover entity validated the speed against _attr_supported_speeds.
            return CURTAIN_SPEED_TO_VALUE[speed]
        return int(
            self.coordinator.config_entry.options.get(
                CONF_CURTAIN_SPEED, DEFAULT_CURTAIN_SPEED
            )
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if (
            not last_state
            or CoverEntityStateAttribute.CURRENT_POSITION not in last_state.attributes
        ):
            return

        self._attr_current_cover_position = last_state.attributes.get(
            CoverEntityStateAttribute.CURRENT_POSITION
        )
        self._last_run_success = last_state.attributes.get("last_run_success")
        if self._attr_current_cover_position is not None:
            self._attr_is_closed = self._attr_current_cover_position <= 20

    @exception_handler
    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the curtain."""

        _LOGGER.debug("Switchbot to open curtain %s", self._address)
        speed = self._get_curtain_speed(kwargs)
        self._last_run_success = bool(await self._device.open(speed))
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the curtain."""

        _LOGGER.debug("Switchbot to close the curtain %s", self._address)
        speed = self._get_curtain_speed(kwargs)
        self._last_run_success = bool(await self._device.close(speed))
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the moving of this device."""

        _LOGGER.debug("Switchbot to stop %s", self._address)
        self._last_run_success = bool(await self._device.stop())
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover shutter to a specific position."""
        position = kwargs[ATTR_POSITION]

        _LOGGER.debug("Switchbot to move at %d %s", position, self._address)
        speed = self._get_curtain_speed(kwargs)
        self._last_run_success = bool(await self._device.set_position(position, speed))
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_closing = self._device.is_closing()
        self._attr_is_opening = self._device.is_opening()
        self._attr_current_cover_position = self.parsed_data["position"]
        self._attr_is_closed = self.parsed_data["position"] <= 20

        self.async_write_ha_state()


class SwitchBotBlindTiltEntity(SwitchbotEntity, CoverEntity, RestoreEntity):
    """Representation of a Switchbot."""

    _device: switchbot.SwitchbotBlindTilt
    _attr_device_class = CoverDeviceClass.BLIND
    _attr_supported_features = (
        CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
        | CoverEntityFeature.STOP_TILT
        | CoverEntityFeature.SET_TILT_POSITION
    )
    _attr_name = None
    _attr_translation_key = "cover"
    CLOSED_UP_THRESHOLD = 80
    CLOSED_DOWN_THRESHOLD = 20

    def __init__(self, coordinator: SwitchbotDataUpdateCoordinator) -> None:
        """Initialize the Switchbot."""
        super().__init__(coordinator)
        self._attr_is_closed = None
        self._calibration_key: tuple[int, bool, bool] | None = None

    @callback
    def _on_calibration_frame(self) -> None:
        """Rewrite the state only when the step, ready or direction error change (not on every frame)."""
        frame = self._device.calibration_frame
        key = (
            (frame.step, frame.ready, frame.direction_error)
            if frame and self._device.calibration_active
            else None
        )
        if key != self._calibration_key:
            self._calibration_key = key
            self._sync_uncalibrated_issue()
            self.async_write_ha_state()

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Add the state of the running calibration to the attributes."""
        attrs = dict(super().extra_state_attributes)
        if self._device.calibration_active:
            frame = self._device.calibration_frame
            attrs["calibration_active"] = True
            if frame:
                attrs["calibration_step"] = frame.step + 1
                attrs["calibration_steps_total"] = STEP_COUNT
                attrs["calibration_ready"] = frame.ready
                attrs["calibration_direction_error"] = frame.direction_error
                attrs["calibration_counter"] = frame.counter
        return attrs

    def _raise_if_calibrating(self) -> None:
        """Refuse movement commands during a calibration (they would disturb it)."""
        if self._device.calibration_active:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="blind_tilt_calibrating"
            )

    def _check_can_move(self) -> None:
        """Refuse movements during a calibration AND while the blind is not calibrated (unreliable percentages)."""
        self._raise_if_calibrating()
        if self.parsed_data.get("calibration") is False:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="blind_tilt_uncalibrated",
                translation_placeholders={"name": self.coordinator.device_name},
            )

    @callback
    def _sync_uncalibrated_issue(self) -> None:
        """Show a repair issue while the blind is not calibrated (outside a running calibration)."""
        issue_id = f"blind_tilt_uncalibrated_{self._address}"
        if self.parsed_data.get("calibration") is False and not self._device.calibration_active:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="blind_tilt_uncalibrated",
                translation_placeholders={"name": self.coordinator.device_name},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Remove the repair issue if the entity goes away."""
        ir.async_delete_issue(self.hass, DOMAIN, f"blind_tilt_uncalibrated_{self._address}")
        await super().async_will_remove_from_hass()

    async def async_calibration_start(self) -> None:
        """Start the calibration of the blind."""
        try:
            await self._device.start_calibration()
        except CalibrationError as err:
            raise HomeAssistantError(str(err)) from err
        self.async_write_ha_state()

    async def async_calibration_save(self) -> None:
        """Save the position of the current step (refused if the blind is not ready or reports a direction error)."""
        frame = self._device.calibration_frame
        if frame is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="blind_tilt_no_frame")
        try:
            await self._device.save_calibration_position(frame.step)
        except CalibrationError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_calibration_finish(self) -> None:
        """End the calibration; fail if the blind does not report itself calibrated."""
        try:
            calibrated = await self._device.stop_calibration()
        except CalibrationError as err:
            raise HomeAssistantError(str(err)) from err
        self.async_write_ha_state()
        if not calibrated:
            raise HomeAssistantError(
                "Calibration finished but the blind does not report itself calibrated: redo the full calibration"
            )

    async def async_calibration_abort(self) -> None:
        """Abandon the calibration (the blind stays uncalibrated until it is redone entirely)."""
        await self._device.abort_calibration()
        self.async_write_ha_state()

    async def async_calibration_status(self) -> dict[str, Any]:
        """Return the state of the running calibration."""
        frame = self._device.calibration_frame
        return {
            "active": self._device.calibration_active,
            "step": frame.step + 1 if frame else None,
            "steps_total": STEP_COUNT,
            "ready": frame.ready if frame else None,
            "direction_error": frame.direction_error if frame else None,
            "counter": frame.counter if frame else None,
        }

    @override
    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        self.async_on_remove(self._device.subscribe_calibration(self._on_calibration_frame))
        self._sync_uncalibrated_issue()
        last_state = await self.async_get_last_state()
        if (
            not last_state
            or CoverEntityStateAttribute.CURRENT_TILT_POSITION
            not in last_state.attributes
        ):
            return

        self._attr_current_cover_tilt_position = last_state.attributes.get(
            CoverEntityStateAttribute.CURRENT_TILT_POSITION
        )
        self._last_run_success = last_state.attributes.get("last_run_success")
        if (_tilt := self._attr_current_cover_tilt_position) is not None:
            self._attr_is_closed = (_tilt < self.CLOSED_DOWN_THRESHOLD) or (
                _tilt > self.CLOSED_UP_THRESHOLD
            )

    @exception_handler
    @override
    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the tilt."""
        self._check_can_move()

        _LOGGER.debug("Switchbot to open blind tilt %s", self._address)
        self._last_run_success = bool(await self._device.open())
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the tilt."""
        self._check_can_move()

        _LOGGER.debug("Switchbot to close the blind tilt %s", self._address)
        self._last_run_success = bool(await self._device.close())
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop the moving of this device."""
        self._raise_if_calibrating()

        _LOGGER.debug("Switchbot to stop %s", self._address)
        self._last_run_success = bool(await self._device.stop())
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Move the cover tilt to a specific position."""
        self._check_can_move()
        position = kwargs.get(ATTR_TILT_POSITION)

        _LOGGER.debug("Switchbot to move at %d %s", position, self._address)
        self._last_run_success = bool(await self._device.set_position(position))
        self.async_write_ha_state()

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _tilt = self.parsed_data["tilt"]
        self._attr_current_cover_tilt_position = _tilt
        self._attr_is_closed = (_tilt < self.CLOSED_DOWN_THRESHOLD) or (
            _tilt > self.CLOSED_UP_THRESHOLD
        )
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self._sync_uncalibrated_issue()
        self.async_write_ha_state()


class SwitchBotRollerShadeEntity(SwitchbotEntity, CoverEntity, RestoreEntity):
    """Representation of a Switchbot."""

    _device: switchbot.SwitchbotRollerShade
    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.SPEED
    )
    _attr_supported_speeds = list(ROLLER_SHADE_SPEED_TO_MODE)

    _attr_translation_key = "roller_shade"
    _attr_name = None

    def __init__(self, coordinator: SwitchbotDataUpdateCoordinator) -> None:
        """Initialize the switchbot."""
        super().__init__(coordinator)
        self._attr_is_closed = None

    @callback
    def _motor_mode(self, kwargs: dict[str, Any]) -> int:
        """Return the motor mode for the requested speed."""
        # The cover entity validated the speed against _attr_supported_speeds.
        return ROLLER_SHADE_SPEED_TO_MODE[
            kwargs.get(ATTR_SPEED, ROLLER_SHADE_SPEED_PERFORMANCE)
        ]

    @override
    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if (
            not last_state
            or CoverEntityStateAttribute.CURRENT_POSITION not in last_state.attributes
        ):
            return

        self._attr_current_cover_position = last_state.attributes.get(
            CoverEntityStateAttribute.CURRENT_POSITION
        )
        self._last_run_success = last_state.attributes.get("last_run_success")
        if self._attr_current_cover_position is not None:
            self._attr_is_closed = self._attr_current_cover_position <= 20

    @exception_handler
    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the roller shade."""

        _LOGGER.debug("Switchbot to open roller shade %s", self._address)
        self._last_run_success = bool(await self._device.open(self._motor_mode(kwargs)))
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the roller shade."""

        _LOGGER.debug("Switchbot to close roller shade %s", self._address)
        self._last_run_success = bool(
            await self._device.close(self._motor_mode(kwargs))
        )
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the moving of roller shade."""

        _LOGGER.debug("Switchbot to stop roller shade %s", self._address)
        self._last_run_success = bool(await self._device.stop())
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""

        position = kwargs[ATTR_POSITION]
        _LOGGER.debug("Switchbot to move at %d %s", position, self._address)
        self._last_run_success = bool(
            await self._device.set_position(position, self._motor_mode(kwargs))
        )
        self._attr_is_opening = self._device.is_opening()
        self._attr_is_closing = self._device.is_closing()
        self.async_write_ha_state()

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_closing = self._device.is_closing()
        self._attr_is_opening = self._device.is_opening()
        self._attr_current_cover_position = self.parsed_data["position"]
        self._attr_is_closed = self.parsed_data["position"] <= 20

        self.async_write_ha_state()


class SwitchbotGarageDoorOpenerEntity(SwitchbotEntity, CoverEntity):
    """Representation of a Switchbot garage door."""

    _device: switchbot.SwitchbotGarageDoorOpener
    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_translation_key = "garage_door"
    _attr_name = None

    @property
    @override
    def is_closed(self) -> bool | None:
        """Return true if cover is closed, else False."""
        return not self._device.door_open()

    @exception_handler
    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the garage door."""
        await self._device.open()
        self.async_write_ha_state()

    @exception_handler
    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the garage door."""
        await self._device.close()
        self.async_write_ha_state()
