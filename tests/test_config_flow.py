"""Config, reauth, and options flows."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from scootscoot import (
    AuthenticationFailed,
    ConnectionFailed,
    PairingRequired,
    VehicleBusy,
)

from custom_components.scootscoot.config_flow import InvalidPasswordError, parse_password
from custom_components.scootscoot.const import (
    CONF_FAST_INTERVAL,
    CONF_PROFILE,
    CONF_SERIAL,
    CONF_SLOW_INTERVAL,
    CONF_STALE_AFTER,
    DOMAIN,
)

from .conftest import ADDRESS, PASSWORD, SERIAL, service_info

pytestmark = pytest.mark.usefixtures("scooter", "ble_device")

DISCOVERED = "custom_components.scootscoot.config_flow.bluetooth.async_discovered_service_info"


@pytest.fixture(autouse=True)
def no_setup() -> None:
    """Flow tests stop at entry creation."""
    with patch("custom_components.scootscoot.async_setup_entry", return_value=True):
        yield


@pytest.mark.parametrize(
    "text",
    [
        PASSWORD.hex(),
        PASSWORD.hex().upper(),
        "0x" + PASSWORD.hex(),
        " ".join(PASSWORD.hex()[i : i + 2] for i in range(0, 32, 2)),
        ":".join(PASSWORD.hex()[i : i + 2] for i in range(0, 32, 2)),
        base64.b64encode(PASSWORD).decode(),
    ],
)
def test_parse_password_accepts_hex_and_base64(text: str) -> None:
    assert parse_password(text) == PASSWORD


@pytest.mark.parametrize("text", ["", "abc", PASSWORD.hex()[:-2], "zz" * 16, "c2hvcnQ="])
def test_parse_password_rejects_anything_else(text: str) -> None:
    with pytest.raises(InvalidPasswordError):
        parse_password(text)


async def test_bluetooth_discovery_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["description_placeholders"] == {"name": SERIAL}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROFILE: "gt3_pro", CONF_PASSWORD: PASSWORD.hex()}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Segway SuperScooter GT3 Pro"
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_PASSWORD: PASSWORD.hex(),
        CONF_PROFILE: "gt3_pro",
        CONF_SERIAL: SERIAL,
    }
    assert result["result"].unique_id == ADDRESS


async def test_bluetooth_discovery_of_configured_scooter_aborts(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_picks_from_connectable_devices(hass: HomeAssistant) -> None:
    other = service_info(name="Kitchen Thermometer", address="11:22:33:44:55:66")
    with patch(DISCOVERED, return_value=[other, service_info()]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    options = result["data_schema"].schema[CONF_ADDRESS].config["options"]
    # The scooter-shaped name sorts first.
    assert [option["value"] for option in options] == [ADDRESS, "11:22:33:44:55:66"]
    assert options[0]["label"] == f"{SERIAL} ({ADDRESS})"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "credentials"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROFILE: "gt3_pro", CONF_PASSWORD: base64.b64encode(PASSWORD).decode()},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == PASSWORD.hex()


async def test_user_with_nothing_discovered_aborts(hass: HomeAssistant) -> None:
    with patch(DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_malformed_password_is_caught_before_connecting(
    hass: HomeAssistant, scooter: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROFILE: "gt3_pro", CONF_PASSWORD: "hunter2"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PASSWORD: "invalid_password_format"}


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (AuthenticationFailed("nope"), "invalid_auth"),
        (PairingRequired("unpaired"), "not_paired"),
        (VehicleBusy("app"), "busy"),
        (ConnectionFailed("gone"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_connection_errors_are_shown_and_recoverable(
    hass: HomeAssistant, scooter: MagicMock, error: Exception, key: str
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    scooter.error = error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROFILE: "gt3_pro", CONF_PASSWORD: PASSWORD.hex()}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    scooter.error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROFILE: "gt3_pro", CONF_PASSWORD: PASSWORD.hex()}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_scooter_out_of_range(hass: HomeAssistant, ble_device: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    ble_device.return_value = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROFILE: "gt3_pro", CONF_PASSWORD: PASSWORD.hex()}
    )
    assert result["errors"] == {"base": "not_in_range"}


async def test_reauth_replaces_the_password(
    hass: HomeAssistant, config_entry: MockConfigEntry, scooter: MagicMock
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    scooter.error = AuthenticationFailed("still wrong")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "ff" * 16}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    scooter.error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "ee" * 16}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "ee" * 16


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SLOW_INTERVAL: 30, CONF_FAST_INTERVAL: 1, CONF_STALE_AFTER: 0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SLOW_INTERVAL: 30,
        CONF_FAST_INTERVAL: 1,
        CONF_STALE_AFTER: 0,
    }
