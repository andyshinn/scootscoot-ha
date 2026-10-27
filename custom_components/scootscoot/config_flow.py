"""Config flow for ScootScoot.

Never pairs. A scooter that has been set up with the Segway app already holds a
password and silently ignores attempts to set another, so the only safe path is
to supply the one the app negotiated.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from scootscoot import (
    AuthenticationFailed,
    Credentials,
    DeviceInfo,
    PairingRequired,
    Scooter,
    ScootScootError,
    VehicleBusy,
    profiles,
)

from .const import (
    CONF_FAST_INTERVAL,
    CONF_PROFILE,
    CONF_SERIAL,
    CONF_SLOW_INTERVAL,
    CONF_STALE_AFTER,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
)
from .coordinator import ScootScootConfigEntry

_LOGGER = logging.getLogger(__name__)

_PASSWORD_LEN = 16

#: Serial prefix of the scooters this integration knows. Matches the manifest's
#: bluetooth matcher; used here only to sort likely candidates to the top.
_SCOOTER_NAME_PREFIX = "03G"

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class InvalidPasswordError(ValueError):
    """The entered password is not 16 bytes in any accepted encoding."""


def parse_password(value: str) -> bytes:
    """Accept the 16-byte password as hex or base64.

    Hex is what most extraction tools print; base64 is how the iOS preferences
    plist stores data when exported as XML.
    """
    text = value.strip()
    compact = "".join(text.split()).replace(":", "").removeprefix("0x")
    if len(compact) == _PASSWORD_LEN * 2:
        try:
            return bytes.fromhex(compact)
        except ValueError:
            pass
    try:
        decoded = base64.b64decode(text, validate=True)
    except binascii.Error:
        decoded = b""
    if len(decoded) == _PASSWORD_LEN:
        return decoded
    raise InvalidPasswordError


class ScootScootConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a scooter."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None
        self._candidates: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ScootScootConfigEntry) -> ScootScootOptionsFlow:
        return ScootScootOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """A scooter-shaped advertisement was seen."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_credentials()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a device from everything Home Assistant can connect to."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._address = address
            self._name = self._candidates.get(address, address)
            return await self.async_step_credentials()

        configured = self._async_current_ids(include_ignore=False)
        infos = [
            info
            for info in bluetooth.async_discovered_service_info(self.hass, connectable=True)
            if info.address not in configured
        ]
        if not infos:
            return self.async_abort(reason="no_devices_found")

        # The scooter advertises only its bare serial, so nothing reliably tells
        # it apart from every other BLE device in range. List them all, with
        # anything that looks like a scooter first.
        infos.sort(key=lambda info: (not info.name.startswith(_SCOOTER_NAME_PREFIX), info.name))
        self._candidates = {info.address: info.name for info in infos}
        options = [
            SelectOptionDict(
                value=info.address,
                label=info.name if info.name == info.address else f"{info.name} ({info.address})",
            )
            for info in infos
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password and model, and prove them with a connection."""
        assert self._address is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            profile_key = user_input[CONF_PROFILE]
            try:
                password = parse_password(user_input[CONF_PASSWORD])
            except InvalidPasswordError:
                errors[CONF_PASSWORD] = "invalid_password_format"
            else:
                info, error = await self._async_try_connect(self._address, profile_key, password)
                if error:
                    errors["base"] = error
                else:
                    assert info is not None
                    return self.async_create_entry(
                        title=profiles.get(profile_key).name,
                        data={
                            CONF_ADDRESS: self._address,
                            CONF_PASSWORD: password.hex(),
                            CONF_PROFILE: profile_key,
                            CONF_SERIAL: info.serial,
                        },
                    )

        profile_options = [
            SelectOptionDict(value=profile.key, label=profile.name) for profile in profiles.ALL
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_PROFILE, default=profiles.ALL[0].key): SelectSelector(
                    SelectSelectorConfig(options=profile_options, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": self._name or self._address},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The stored password stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                password = parse_password(user_input[CONF_PASSWORD])
            except InvalidPasswordError:
                errors[CONF_PASSWORD] = "invalid_password_format"
            else:
                _, error = await self._async_try_connect(
                    entry.data[CONF_ADDRESS], entry.data[CONF_PROFILE], password
                )
                if error:
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_PASSWORD: password.hex()}
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def _async_try_connect(
        self, address: str, profile_key: str, password: bytes
    ) -> tuple[DeviceInfo | None, str | None]:
        """Authenticate once. Returns identity, or a translation key for the error."""
        ble_device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is None:
            return None, "not_in_range"

        scooter = Scooter(ble_device, profiles.get(profile_key), Credentials(password=password))
        try:
            return await scooter.identify(), None
        except AuthenticationFailed:
            return None, "invalid_auth"
        except PairingRequired:
            return None, "not_paired"
        except VehicleBusy:
            return None, "busy"
        except ScootScootError as err:
            _LOGGER.debug("could not connect to %s: %s", address, err)
            return None, "cannot_connect"
        except Exception:
            _LOGGER.exception("unexpected error connecting to %s", address)
            return None, "unknown"


class ScootScootOptionsFlow(OptionsFlowWithReload):
    """Polling and staleness settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SLOW_INTERVAL,
                        default=options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL),
                    ): _minutes(5, 240),
                    vol.Required(
                        CONF_FAST_INTERVAL,
                        default=options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL),
                    ): _minutes(1, 60),
                    vol.Required(
                        CONF_STALE_AFTER,
                        default=options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=168,
                            step=1,
                            unit_of_measurement="h",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )


def _minutes(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="min",
            mode=NumberSelectorMode.BOX,
        )
    )
