"""Config flow for Switchbot."""
# Modified from Home Assistant Core (Apache-2.0): adds the Blind Tilt calibration wizard to the options flow.

import asyncio
import logging
from typing import Any, override

import probatio
from switchbot import (
    SwitchbotAccountConnectionError,
    SwitchBotAdvertisement,
    SwitchbotApiError,
    SwitchbotAuthenticationError,
    SwitchbotModel,
    fetch_cloud_devices,
    parse_advertisement_data,
)

from homeassistant.components import bluetooth, persistent_notification
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_PASSWORD,
    CONF_SENSOR_TYPE,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .blind_tilt_calibration import STEP_COUNT, CalibratableBlindTilt, CalibrationError
from .const import (
    CONF_CURTAIN_SPEED,
    CONF_ENCRYPTION_KEY,
    CONF_KEY_ID,
    CONF_LOCK_NIGHTLATCH,
    CONF_RETRY_COUNT,
    CONNECTABLE_SUPPORTED_MODEL_TYPES,
    CURTAIN_SPEED_MAX,
    CURTAIN_SPEED_MIN,
    DEFAULT_CURTAIN_SPEED,
    DEFAULT_LOCK_NIGHTLATCH,
    DEFAULT_RETRY_COUNT,
    DOMAIN,
    ENCRYPTED_MODELS,
    ENCRYPTED_SWITCHBOT_MODEL_TO_CLASS,
    NON_CONNECTABLE_SUPPORTED_MODEL_TYPES,
    SUPPORTED_MODEL_TYPES,
    SupportedModels,
)

_LOGGER = logging.getLogger(__name__)


def format_unique_id(address: str) -> str:
    """Format the unique ID for a switchbot."""
    return address.replace(":", "").lower()


def short_address(address: str) -> str:
    """Convert a Bluetooth address to a short address."""
    results = address.replace("-", ":").split(":")
    return f"{results[-2].upper()}{results[-1].upper()}"[-4:]


def name_from_discovery(discovery: SwitchBotAdvertisement) -> str:
    """Get the name from a discovery."""
    return f"{discovery.data['modelFriendlyName']} {short_address(discovery.address)}"


class SwitchbotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Switchbot."""

    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SwitchbotOptionsFlowHandler:
        """Get the options flow for this handler."""
        return SwitchbotOptionsFlowHandler()

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_adv: SwitchBotAdvertisement | None = None
        self._discovered_advs: dict[str, SwitchBotAdvertisement] = {}
        self._cloud_username: str | None = None
        self._cloud_password: str | None = None
        self._encryption_method_selected = False

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered bluetooth device: %s", discovery_info.as_dict())
        await self.async_set_unique_id(format_unique_id(discovery_info.address))
        self._abort_if_unique_id_configured()
        parsed = parse_advertisement_data(
            discovery_info.device, discovery_info.advertisement
        )
        if not parsed or parsed.data.get("modelName") not in SUPPORTED_MODEL_TYPES:
            return self.async_abort(reason="not_supported")
        model_name = parsed.data.get("modelName")
        if (
            not discovery_info.connectable
            and model_name in CONNECTABLE_SUPPORTED_MODEL_TYPES
            and model_name not in NON_CONNECTABLE_SUPPORTED_MODEL_TYPES
        ):
            # Source is not connectable but the model is connectable only
            return self.async_abort(reason="not_supported")
        self._discovered_adv = parsed
        data = parsed.data
        self.context["title_placeholders"] = {
            "name": data["modelFriendlyName"],
            "address": short_address(discovery_info.address),
        }
        if model_name in ENCRYPTED_MODELS:
            return await self.async_step_encrypted_choose_method()
        if self._discovered_adv.data["isEncrypted"]:
            return await self.async_step_password()
        return await self.async_step_confirm()

    async def _async_create_entry_from_discovery(
        self, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create an entry from a discovery."""
        assert self._discovered_adv is not None
        discovery = self._discovered_adv
        name = name_from_discovery(discovery)
        model_name = discovery.data["modelName"]
        sensor_type = SUPPORTED_MODEL_TYPES[model_name]

        options: dict[str, Any] = {CONF_RETRY_COUNT: DEFAULT_RETRY_COUNT}
        if sensor_type == SupportedModels.CURTAIN:
            options[CONF_CURTAIN_SPEED] = DEFAULT_CURTAIN_SPEED

        return self.async_create_entry(
            title=name,
            data={
                **user_input,
                CONF_ADDRESS: discovery.address,
                CONF_SENSOR_TYPE: str(sensor_type),
            },
            options=options,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a single device."""
        assert self._discovered_adv is not None
        if user_input is not None:
            return await self._async_create_entry_from_discovery(user_input)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            data_schema=probatio.Schema({}),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv)
            },
        )

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the password step."""
        assert self._discovered_adv is not None
        if user_input is not None:
            # There is currently no api to validate the password
            # that does not operate the device so we have
            # to accept it as-is
            return await self._async_create_entry_from_discovery(user_input)

        return self.async_show_form(
            step_id="password",
            data_schema=probatio.Schema({probatio.Required(CONF_PASSWORD): str}),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv)
            },
        )

    async def async_step_encrypted_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the SwitchBot API auth step."""
        errors: dict[str, str] = {}
        assert self._discovered_adv is not None
        description_placeholders: dict[str, str] = {}

        if user_input is None:
            if not self._encryption_method_selected and not (
                self._cloud_username and self._cloud_password
            ):
                return await self.async_step_encrypted_choose_method()
            self._encryption_method_selected = False

        # If we have saved credentials from cloud login, try them first
        if user_input is None and self._cloud_username and self._cloud_password:
            user_input = {
                CONF_USERNAME: self._cloud_username,
                CONF_PASSWORD: self._cloud_password,
            }

        if user_input is not None:
            model: SwitchbotModel = self._discovered_adv.data["modelName"]
            cls = ENCRYPTED_SWITCHBOT_MODEL_TO_CLASS[model]
            try:
                key_details = await cls.async_retrieve_encryption_key(
                    async_get_clientsession(self.hass),
                    self._discovered_adv.address,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except (SwitchbotApiError, SwitchbotAccountConnectionError) as ex:
                _LOGGER.debug(
                    "Failed to connect to SwitchBot API: %s", ex, exc_info=True
                )
                raise AbortFlow(
                    "api_error", description_placeholders={"error_detail": str(ex)}
                ) from ex
            except SwitchbotAuthenticationError as ex:
                _LOGGER.debug("Authentication failed: %s", ex, exc_info=True)
                errors = {"base": "auth_failed"}
                description_placeholders = {"error_detail": str(ex)}
                # Clear saved credentials if auth failed
                self._cloud_username = None
                self._cloud_password = None
            except Exception:
                _LOGGER.exception("Unexpected error retrieving encryption key")
                errors = {"base": "unknown"}
            else:
                return await self.async_step_encrypted_key(key_details)

        user_input = user_input or {}
        return self.async_show_form(
            step_id="encrypted_auth",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_USERNAME, default=user_input.get(CONF_USERNAME)
                    ): str,
                    probatio.Required(CONF_PASSWORD): str,
                }
            ),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv),
                **description_placeholders,
            },
        )

    async def async_step_encrypted_choose_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the SwitchBot API chose method step."""
        assert self._discovered_adv is not None

        self._encryption_method_selected = True
        return self.async_show_menu(
            step_id="encrypted_choose_method",
            menu_options=["encrypted_auth", "encrypted_key"],
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv),
            },
        )

    async def async_step_encrypted_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the encryption key step."""
        errors: dict[str, str] = {}
        assert self._discovered_adv is not None

        if user_input is None:
            if not self._encryption_method_selected:
                return await self.async_step_encrypted_choose_method()
            self._encryption_method_selected = False

        if user_input is not None:
            model: SwitchbotModel = self._discovered_adv.data["modelName"]
            cls = ENCRYPTED_SWITCHBOT_MODEL_TO_CLASS[model]
            if not await cls.verify_encryption_key(
                self._discovered_adv.device,
                user_input[CONF_KEY_ID],
                user_input[CONF_ENCRYPTION_KEY],
                model=model,
            ):
                errors = {
                    "base": "encryption_key_invalid",
                }
            else:
                return await self._async_create_entry_from_discovery(user_input)

        return self.async_show_form(
            step_id="encrypted_key",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_KEY_ID): str,
                    probatio.Required(CONF_ENCRYPTION_KEY): str,
                }
            ),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv),
            },
        )

    @callback
    def _async_discover_devices(self) -> None:
        current_addresses = self._async_current_ids(include_ignore=False)
        for connectable in (True, False):
            for discovery_info in async_discovered_service_info(self.hass, connectable):
                address = discovery_info.address
                if (
                    format_unique_id(address) in current_addresses
                    or address in self._discovered_advs
                ):
                    continue
                parsed = parse_advertisement_data(
                    discovery_info.device, discovery_info.advertisement
                )
                if not parsed:
                    continue
                model_name = parsed.data.get("modelName")
                if (
                    discovery_info.connectable
                    and model_name in CONNECTABLE_SUPPORTED_MODEL_TYPES
                ) or model_name in NON_CONNECTABLE_SUPPORTED_MODEL_TYPES:
                    self._discovered_advs[address] = parsed

        if not self._discovered_advs:
            raise AbortFlow("no_devices_found")

    async def _async_set_device(self, discovery: SwitchBotAdvertisement) -> None:
        """Set the device to work with."""
        self._discovered_adv = discovery
        address = discovery.address
        await self.async_set_unique_id(
            format_unique_id(address), raise_on_progress=False
        )
        self._abort_if_unique_id_configured()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to choose cloud login or direct discovery."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["cloud_login", "select_device"],
        )

    async def async_step_cloud_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the cloud login step."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            try:
                await fetch_cloud_devices(
                    async_get_clientsession(self.hass),
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except (SwitchbotApiError, SwitchbotAccountConnectionError) as ex:
                _LOGGER.debug(
                    "Failed to connect to SwitchBot API: %s", ex, exc_info=True
                )
                raise AbortFlow(
                    "api_error", description_placeholders={"error_detail": str(ex)}
                ) from ex
            except SwitchbotAuthenticationError as ex:
                _LOGGER.debug("Authentication failed: %s", ex, exc_info=True)
                errors = {"base": "auth_failed"}
                description_placeholders = {"error_detail": str(ex)}
            except Exception:
                _LOGGER.exception("Unexpected error during cloud login")
                errors = {"base": "unknown"}
            else:
                # Save credentials temporarily for the duration of this flow
                # to avoid re-prompting if encrypted device auth is needed
                # These will be discarded when the flow completes
                self._cloud_username = user_input[CONF_USERNAME]
                self._cloud_password = user_input[CONF_PASSWORD]
                return await self.async_step_select_device()

        user_input = user_input or {}
        return self.async_show_form(
            step_id="cloud_login",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_USERNAME, default=user_input.get(CONF_USERNAME)
                    ): str,
                    probatio.Required(CONF_PASSWORD): str,
                }
            ),
            description_placeholders=description_placeholders,
        )

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the step to pick discovered device."""
        errors: dict[str, str] = {}
        device_adv: SwitchBotAdvertisement | None = None
        if user_input is not None:
            device_adv = self._discovered_advs[user_input[CONF_ADDRESS]]
            await self._async_set_device(device_adv)
            if device_adv.data.get("modelName") in ENCRYPTED_MODELS:
                return await self.async_step_encrypted_choose_method()
            if device_adv.data["isEncrypted"]:
                return await self.async_step_password()
            return await self._async_create_entry_from_discovery(user_input)

        await bluetooth.async_request_active_scan(self.hass)
        self._async_discover_devices()
        if len(self._discovered_advs) == 1:
            # If there is only one device we can ask for a password
            # or simply confirm it
            device_adv = list(self._discovered_advs.values())[0]
            await self._async_set_device(device_adv)
            if device_adv.data.get("modelName") in ENCRYPTED_MODELS:
                return await self.async_step_encrypted_choose_method()
            if device_adv.data["isEncrypted"]:
                return await self.async_step_password()
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="select_device",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ADDRESS): probatio.In(
                        {
                            address: name_from_discovery(parsed)
                            for address, parsed in self._discovered_advs.items()
                        }
                    ),
                }
            ),
            errors=errors,
        )


# Blind Tilt calibration wizard. ALL displayed texts live in translations/*.json (steps etape_1 to etape_4, reprise,
# calibrate, errors, endings): nothing is hard-coded here, so the language follows the user's.
# Fail-safe design: no cancel (the previous calibration is erased as soon as it starts and cannot be restored); a single
# "Next" button per step that refuses cleanly when the blind is not ready; if anything goes wrong, start over.
CALIBRATION_ERROR_KEYS = {
    "direction_error": "direction_error",
    "not_ready": "not_ready",
    "not_at_step": "not_at_step",
}
# Only text outside the translation files (persistent notifications do not go through them): follows Home Assistant's language.
CALIBRATION_NOTIFICATION = {
    "fr": (
        "SwitchBot – calibration",
        "La calibration du store « {name} » est incomplète ({detail}). Le store reste NON calibré : rouvrez Configurer > "
        "Calibrer le store pour la continuer ou la recommencer du début.",
    ),
    "en": (
        "SwitchBot – calibration",
        "The calibration of blind “{name}” is incomplete ({detail}). It stays UNCALIBRATED: reopen Configure > "
        "Calibrate the blind to continue it or restart it from the beginning.",
    ),
}
CALIBRATION_FAILURE_REASONS = {"timeout", "comm", "refused", "stop_unconfirmed", "not_calibrated"}


class SwitchbotOptionsFlowHandler(OptionsFlow):
    """Handle Switchbot options."""

    def __init__(self) -> None:
        """Initialize the state of the calibration wizard (Blind Tilt)."""
        super().__init__()
        self._skip_menu = False
        self._cal_step = 0

    def _is_blind_tilt(self) -> bool:
        return (
            self.config_entry.data.get(CONF_SENSOR_TYPE) == SupportedModels.BLIND_TILT
        )

    def _calibration_device(self) -> CalibratableBlindTilt | None:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        device = getattr(coordinator, "device", None)
        return device if isinstance(device, CalibratableBlindTilt) else None

    @property
    def _notification_id(self) -> str:
        return f"switchbot_calibration_{self.config_entry.entry_id}"

    @callback
    def _notify_incomplete(self, detail: str) -> None:
        """Persistent notification: an incomplete calibration must never go unnoticed."""
        lang = "fr" if self.hass.config.language.startswith("fr") else "en"
        title, message = CALIBRATION_NOTIFICATION[lang]
        persistent_notification.async_create(
            self.hass,
            message.format(name=self.config_entry.title, detail=detail),
            title=title,
            notification_id=self._notification_id,
        )

    def _abort_failed(self, reason: str, detail: str) -> ConfigFlowResult:
        """Failed ending: translated message depending on the cause (timeout, link, refusal, unconfirmed stop, uncalibrated)."""
        self._notify_incomplete(detail)
        key = f"calibration_failed_{reason}" if reason in CALIBRATION_FAILURE_REASONS else "calibration_failed"
        return self.async_abort(reason=key, description_placeholders={"detail": detail})

    @callback
    def _dismiss_notification(self) -> None:
        persistent_notification.async_dismiss(self.hass, self._notification_id)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Usual settings (retry count): same fields as the original options screen."""
        self._skip_menu = True
        return await self.async_step_init(user_input)

    async def async_step_calibrate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start screen: blind state and warning. A calibration already in progress offers to continue or start over."""
        device = self._calibration_device()
        if device is None:
            return self.async_abort(reason="calibration_unavailable")
        if device.calibration_active:
            return await self.async_step_reprise()
        if user_input is None:
            try:
                calibrated = await asyncio.wait_for(device.read_calibrated(), 12)
                state = "✅" if calibrated else "❌"
            except Exception:  # noqa: BLE001
                state = "❓"
            return self.async_show_form(
                step_id="calibrate",
                data_schema=probatio.Schema({}),
                description_placeholders={"state": state},
            )
        return await self._start()

    async def _start(self) -> ConfigFlowResult:
        """Start a new calibration (step 1)."""
        device = self._calibration_device()
        if device is None:
            return self.async_abort(reason="calibration_unavailable")
        try:
            await device.start_calibration()
        except Exception as err:  # noqa: BLE001
            return self._abort_failed(getattr(err, "reason", "comm"), str(err))
        self._notify_incomplete("…")
        self._cal_step = 0
        return self._show_step()

    async def async_step_reprise(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue / Start over menu: HA requires a method for EVERY step_id shown (otherwise UnknownStep)."""
        return self.async_show_menu(
            step_id="reprise",
            menu_options=["continue_calibration", "restart_calibration"],
        )

    async def async_step_continue_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Resume the running calibration at the step the blind is at."""
        device = self._calibration_device()
        frame = device.calibration_frame if device else None
        self._cal_step = min(frame.step, STEP_COUNT - 1) if frame else 0
        return self._show_step()

    async def async_step_restart_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start over: end the current session, then start a new one."""
        device = self._calibration_device()
        if device is not None:
            try:
                await device.abort_calibration()
            except CalibrationError:
                pass  # the new session sends the start command again anyway
        return await self._start()

    def _show_step(
        self, errors: dict[str, str] | None = None, error_text: str = ""
    ) -> ConfigFlowResult:
        """Form of the current step: translated instruction and a single Next button."""
        return self.async_show_form(
            step_id=f"etape_{self._cal_step + 1}",
            data_schema=probatio.Schema({}),
            errors=errors or {},
            description_placeholders={"detail": error_text},
        )

    async def _step(self, k: int, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        """Step k (0 to 3): Next saves the position if the blind is ready, otherwise shows why, breaking nothing."""
        self._cal_step = k
        if user_input is None:
            return self._show_step()
        device = self._calibration_device()
        if device is None or not device.calibration_active:
            return self.async_abort(reason="calibration_unavailable")
        try:
            await device.save_calibration_position(k)
        except CalibrationError as err:
            key = CALIBRATION_ERROR_KEYS.get(err.reason, "save_failed")
            return self._show_step({"base": key}, str(err))
        self._cal_step = k + 1
        if self._cal_step >= STEP_COUNT:
            return await self._finish()
        return self._show_step()

    async def async_step_etape_1(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: closed down."""
        return await self._step(0, user_input)

    async def async_step_etape_2(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2: open."""
        return await self._step(1, user_input)

    async def async_step_etape_3(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 3: closed up."""
        return await self._step(2, user_input)

    async def async_step_etape_4(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 4: open."""
        return await self._step(3, user_input)

    async def _finish(self) -> ConfigFlowResult:
        """End the calibration and check that the blind reports itself calibrated; otherwise start over."""
        device = self._calibration_device()
        if device is None:
            return self.async_abort(reason="calibration_unavailable")
        try:
            calibrated = await device.stop_calibration()
        except CalibrationError as err:
            return self._abort_failed(err.reason, str(err))
        if not calibrated:
            return self._abort_failed("not_calibrated", "state byte 0x00")
        self._dismiss_notification()
        return self.async_abort(reason="calibration_done")

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Switchbot options."""
        if self._is_blind_tilt() and not self._skip_menu and user_input is None:
            return self.async_show_menu(
                step_id="init", menu_options=["settings", "calibrate"]
            )
        if user_input is not None:
            # Update common entity options for all other entities.
            return self.async_create_entry(title="", data=user_input)

        options: dict[probatio.Optional, Any] = {
            probatio.Optional(
                CONF_RETRY_COUNT,
                default=self.config_entry.options.get(
                    CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT
                ),
            ): int
        }
        if CONF_SENSOR_TYPE in self.config_entry.data and self.config_entry.data[
            CONF_SENSOR_TYPE
        ] in (
            SupportedModels.LOCK,
            SupportedModels.LOCK_PRO,
            SupportedModels.LOCK_ULTRA,
            SupportedModels.LOCK_ULTRA_MAX,
            SupportedModels.LOCK_PRO_WIFI,
            SupportedModels.LOCK_VISION,
            SupportedModels.LOCK_VISION_PRO,
        ):
            options.update(
                {
                    probatio.Optional(
                        CONF_LOCK_NIGHTLATCH,
                        default=self.config_entry.options.get(
                            CONF_LOCK_NIGHTLATCH, DEFAULT_LOCK_NIGHTLATCH
                        ),
                    ): bool
                }
            )
        if (
            CONF_SENSOR_TYPE in self.config_entry.data
            and self.config_entry.data[CONF_SENSOR_TYPE] == SupportedModels.CURTAIN
        ):
            options.update(
                {
                    probatio.Optional(
                        CONF_CURTAIN_SPEED,
                        default=self.config_entry.options.get(
                            CONF_CURTAIN_SPEED, DEFAULT_CURTAIN_SPEED
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=CURTAIN_SPEED_MIN,
                            max=CURTAIN_SPEED_MAX,
                            step=1,
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    )
                }
            )

        return self.async_show_form(
            step_id="init", data_schema=probatio.Schema(options)
        )
