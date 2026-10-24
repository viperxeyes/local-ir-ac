"""Local IR air conditioner platform for Home Assistant.

Drives a Tuya IR blaster over the LAN (no cloud) using codes learned in the
Smart Life app and exported from the Tuya cloud once.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, CONF_NAME, CONF_HOST
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_CODES_FILE = "codes_file"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_DEVICE = "device"

FAN_INDEX = {FAN_AUTO: 0, FAN_LOW: 1, FAN_MEDIUM: 2, FAN_HIGH: 3}
INDEX_FAN = {v: k for k, v in FAN_INDEX.items()}

# The unit only accepts a wake-up frame learned at 23 °C; any other temperature
# frame is ignored while it is off, so power-on always goes through 23 first.
POWER_ON_TEMP = 23
# Custom HVAC mode: the unit's "speed"/turbo setting, no fan or temperature control.
FULL_POWER = "full_power"

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_LOCAL_KEY): cv.string,
        vol.Optional(CONF_CODES_FILE, default="/config/local_ir_codes.json"): cv.string,
        vol.Optional(CONF_MIN_TEMP, default=20): vol.Coerce(int),
        vol.Optional(CONF_MAX_TEMP, default=26): vol.Coerce(int),
        vol.Optional(CONF_DEVICE, default="AC"): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the local IR climate platform."""
    async_add_entities([LocalIrAc(config)], update_before_add=True)


class LocalIrAc(ClimateEntity):
    """Optimistic climate entity backed by stored IR frames."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    # The unit has four operating modes: cool, auto, dry (dehumidify) and a
    # full-power "speed" mode. Full power is exposed as a preset because it is
    # not one of Home Assistant's standard HVAC modes.
    # HA's climate service only accepts its own mode names, so the unit's speed
    # ("turbo") mode occupies the fan_only slot; it sends the captured speed_mode
    # frame and ignores fan and temperature.
    # heat_cool carries the unit's "auto" mode: Matter controllers only offer an
    # Auto button when the thermostat advertises heating + cooling + AutoMode.
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.HEAT_COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ]
    _attr_fan_modes = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]
    _attr_temperature_unit = "°C"
    _attr_target_temperature_step = 1

    def __init__(self, config: ConfigType) -> None:
        self._name = config[CONF_NAME]
        self._host = config[CONF_HOST]
        self._device_id = config[CONF_DEVICE_ID]
        self._local_key = config[CONF_LOCAL_KEY]
        self._codes_path = config[CONF_CODES_FILE]
        self._min_temp = config[CONF_MIN_TEMP]
        self._max_temp = config[CONF_MAX_TEMP]
        self._attr_min_temp = self._min_temp
        self._attr_max_temp = self._max_temp
        self._attr_unique_id = f"local_ir_ac_{config[CONF_DEVICE]}"

        self._codes: dict[str, str] = {}
        self._hvac_mode = HVACMode.OFF
        self._target_temp = POWER_ON_TEMP
        self._powered = False
        self._last_code: str | None = None
        self._fan_mode = FAN_AUTO

    async def async_added_to_hass(self) -> None:
        await self._async_load_codes()

    async def _async_load_codes(self) -> None:
        def _read() -> dict[str, str]:
            with open(self._codes_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, list):
                return {e["name"]: e["code"] for e in raw}
            return raw

        try:
            self._codes = await self.hass.async_add_executor_job(_read)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Cannot read IR codes from %s: %s", self._codes_path, err)

    def _code_name(self, mode: HVACMode, temp: int, fan: int) -> str | None:
        if mode == HVACMode.FAN_ONLY:
            return "speed_mode"
        if mode == HVACMode.COOL:
            return f"M0_T{temp}_S{fan}"
        if mode == HVACMode.HEAT_COOL:
            return f"M2_S{fan}"
        if mode == HVACMode.HEAT:
            return "heat_mode"
        if mode == HVACMode.DRY:
            return "M4_S0"
        return None

    async def _async_send(self, code_name: str) -> bool:
        code = self._codes.get(code_name)
        if not code:
            _LOGGER.error("IR code %s not found", code_name)
            return False

        def _send() -> None:
            from tinytuya.Contrib.IRRemoteControlDevice import IRRemoteControlDevice

            dev = IRRemoteControlDevice(
                self._device_id, self._host, self._local_key, version=3.5, control_type=1
            )
            dev.set_socketPersistent(False)
            dev.set_socketTimeout(6)
            dev.send_button(base64.b64encode(bytes.fromhex(code)).decode())

        try:
            await self.hass.async_add_executor_job(_send)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to send IR code %s: %s", code_name, err)
            return False
        _LOGGER.debug("Sent IR code %s", code_name)
        return True

    async def _async_apply(self) -> None:
        fan = FAN_INDEX.get(self._fan_mode, 0)
        if self._hvac_mode == HVACMode.OFF:
            await self._async_send("power_off")
            self._powered = False
            self._last_code = None
        else:
            if not self._powered:
                # Always wake the unit with a cool-23 frame; other frames are
                # ignored while it is off.
                wake = f"M0_T{POWER_ON_TEMP}_S{fan}"
                if self._codes.get(wake):
                    await self._async_send(wake)
                    self._powered = True
                    self._last_code = wake
                    await asyncio.sleep(0.8)
            # Mode, fan and setpoint all live in the same IR frame, so the frame
            # must be re-sent whenever any of them changes -- including when the
            # setpoint is still the wake-up temperature of 23.
            name = self._code_name(self._hvac_mode, self._target_temp, fan)
            if name and name != self._last_code:
                await self._async_send(name)
                self._last_code = name
        self.async_write_ha_state()

    @property
    def name(self) -> str:
        return self._name

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    @property
    def target_temperature(self) -> float:
        return self._target_temp

    @property
    def fan_mode(self) -> str:
        return self._fan_mode

    @property
    def current_temperature(self) -> float | None:
        return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._hvac_mode = hvac_mode
        await self._async_apply()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._target_temp = int(max(self._min_temp, min(self._max_temp, temp)))
        if self._hvac_mode in (HVACMode.OFF,):
            self._hvac_mode = HVACMode.COOL
        await self._async_apply()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._fan_mode = fan_mode
        if self._hvac_mode != HVACMode.OFF:
            await self._async_apply()

    async def async_turn_on(self) -> None:
        if self._hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.COOL
        await self._async_apply()

    async def async_turn_off(self) -> None:
        self._hvac_mode = HVACMode.OFF
        await self._async_apply()
