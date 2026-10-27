"""Setup, polling, keep-last-value, persistence, and diagnostics."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from scootscoot import AuthenticationFailed, VehicleBusy

from custom_components.scootscoot.const import CONF_STALE_AFTER, DOMAIN
from custom_components.scootscoot.coordinator import ScootScootCoordinator
from custom_components.scootscoot.data import Snapshot, dump, load, merge

from .conftest import (
    ADDRESS,
    BATTERY,
    INFO_FULL,
    PARKED,
    PASSWORD,
    POWERED_ON,
    service_info,
)

NAME = "segway_superscooter_gt3_pro"
BATTERY_ENTITY = f"sensor.{NAME}_battery"
ODOMETER = f"sensor.{NAME}_odometer"
CHARGING = f"binary_sensor.{NAME}_charging"
CONNECTION = f"binary_sensor.{NAME}_connection"
POWERED = f"binary_sensor.{NAME}_powered_on"

pytestmark = pytest.mark.usefixtures("scooter", "ble_device")


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> ScootScootCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry.runtime_data


def state(hass: HomeAssistant, entity_id: str) -> str:
    current = hass.states.get(entity_id)
    assert current is not None, entity_id
    return current.state


async def test_parked_scooter(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    coordinator = await setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert state(hass, BATTERY_ENTITY) == "81"
    assert state(hass, f"sensor.{NAME}_battery_voltage") == "74.52"
    assert state(hass, f"sensor.{NAME}_battery_power") == "-4.5"
    assert state(hass, f"sensor.{NAME}_cell_voltage_delta") == "0.019"
    assert state(hass, f"sensor.{NAME}_battery_temperature_max") == "23"
    assert state(hass, CHARGING) == STATE_OFF
    assert state(hass, POWERED) == STATE_OFF
    assert state(hass, CONNECTION) == STATE_ON
    # The VCU answers while parked, so ride data is current from the start.
    assert state(hass, ODOMETER) == "1234.5"
    assert coordinator.update_interval == timedelta(minutes=15)


async def test_per_cell_sensors_are_disabled_by_default(
    hass: HomeAssistant, config_entry: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    await setup(hass, config_entry)
    cells = [
        entry
        for entry in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
        if entry.translation_key == "cell_voltage"
    ]
    assert len(cells) == 20
    assert all(entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION for entry in cells)


async def test_powered_on_polls_fast_and_learns_identity(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    scooter: MagicMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    coordinator = await setup(hass, config_entry)
    scooter.state = POWERED_ON
    scooter.info = INFO_FULL
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert state(hass, ODOMETER) == "1234.5"
    assert state(hass, POWERED) == STATE_ON
    assert coordinator.update_interval == timedelta(minutes=2)

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, ADDRESS), config_entry.entry_id
    )
    assert device is not None
    assert device.sw_version == "1.2.2"
    assert device.model_id == "AA.00.0000.00"
    assert device.serial_number == "03GGG2527C0165"


async def test_charging_polls_fast(
    hass: HomeAssistant, config_entry: MockConfigEntry, scooter: MagicMock
) -> None:
    scooter.state = replace(PARKED, battery=replace(BATTERY, current=-3.4))
    coordinator = await setup(hass, config_entry)
    assert state(hass, CHARGING) == STATE_ON
    assert coordinator.update_interval == timedelta(minutes=2)


async def test_busy_scooter_keeps_last_values(
    hass: HomeAssistant, config_entry: MockConfigEntry, scooter: MagicMock
) -> None:
    coordinator = await setup(hass, config_entry)
    scooter.error = VehicleBusy("the app has it")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert state(hass, BATTERY_ENTITY) == "81"
    assert state(hass, CONNECTION) == STATE_OFF


async def test_absent_scooter_is_not_connected_to_and_returns_on_advertisement(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    scooter: MagicMock,
    ble_device: MagicMock,
) -> None:
    coordinator = await setup(hass, config_entry)
    sessions = scooter.sessions

    ble_device.return_value = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert scooter.sessions == sessions
    assert state(hass, BATTERY_ENTITY) == "81"
    assert state(hass, CONNECTION) == STATE_OFF

    # It comes home: the next advertisement triggers a poll.
    ble_device.return_value = service_info().device
    coordinator.async_scooter_seen()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert scooter.sessions == sessions + 1
    assert state(hass, CONNECTION) == STATE_ON


async def test_readings_go_unavailable_once_stale(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    scooter: MagicMock,
    ble_device: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_entry.unique_id,
        title=config_entry.title,
        data=config_entry.data,
        options={CONF_STALE_AFTER: 2},
    )
    coordinator = await setup(hass, entry)
    ble_device.return_value = None

    freezer.tick(timedelta(hours=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, BATTERY_ENTITY) == "81"

    freezer.tick(timedelta(hours=2))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, BATTERY_ENTITY) == STATE_UNAVAILABLE
    # The link sensors still answer.
    assert state(hass, CONNECTION) == STATE_OFF


async def test_rejected_password_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, scooter: MagicMock
) -> None:
    scooter.error = AuthenticationFailed("wrong")
    await setup(hass, config_entry)

    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_restores_last_state_across_restarts(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    ble_device: MagicMock,
    hass_storage: dict[str, Any],
) -> None:
    stored = merge(Snapshot(), POWERED_ON, now=dt_util.utcnow())
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": dump(stored),
    }
    ble_device.return_value = None  # out riding at startup

    await setup(hass, config_entry)
    assert state(hass, BATTERY_ENTITY) == "81"
    assert state(hass, ODOMETER) == "1234.5"
    assert state(hass, CONNECTION) == STATE_OFF


async def test_successful_poll_is_persisted(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup(hass, config_entry)
    freezer.tick(timedelta(seconds=30))
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_final_write")
    await hass.async_block_till_done()
    saved = hass_storage[f"{DOMAIN}.{config_entry.entry_id}"]["data"]
    assert load(saved).battery == BATTERY


async def test_remove_entry_deletes_stored_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    await setup(hass, config_entry)
    hass.bus.async_fire("homeassistant_final_write")
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{config_entry.entry_id}" in hass_storage

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{config_entry.entry_id}" not in hass_storage


async def test_diagnostics_redact_the_password(
    hass: HomeAssistant, config_entry: MockConfigEntry, hass_client: Any
) -> None:
    await setup(hass, config_entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
    assert PASSWORD.hex() not in str(diagnostics)
    assert diagnostics["snapshot"]["battery"]["soc_percent"] == 81
