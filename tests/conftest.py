"""Shared fixtures for ScootScoot tests."""

from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import replace
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from habluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry
from scootscoot import (
    BatteryState,
    BoardRole,
    DeviceInfo,
    MotorState,
    ScooterState,
    VehicleState,
    profiles,
)

from custom_components.scootscoot.const import CONF_PROFILE, CONF_SERIAL, DOMAIN

ADDRESS = "AA:BB:CC:DD:EE:FF"
SERIAL = "03GGG2527C0165"
PASSWORD = bytes(range(16))

BATTERY = BatteryState(
    soc_percent=81,
    voltage=74.52,
    current=-0.06,
    cycles=42,
    capacity_full_ah=19.5,
    capacity_remaining_ah=15.8,
    temperatures=(21, 22, 22, 23, 21, 22, 22, 21),
    charge_status=0,
)
VEHICLE = VehicleState(
    battery_percent=80,
    speed_kmh=0.0,
    odometer_km=1234.5,
    range_km=61.25,
    body_temp_c=24.5,
    error_code=0,
    warning_code=0,
)
MOTOR = MotorState(body_temp_c=26.0)
CELLS = tuple(3.726 + i * 0.001 for i in range(20))

# Measured on a switched-off GT3: the VCU still answers in full; only the MCU
# is silent.
PARKED = ScooterState(
    serial=SERIAL,
    profile_key="gt3_pro",
    responding=frozenset({BoardRole.BMS1, BoardRole.VCU}),
    battery=BATTERY,
    vehicle=VEHICLE,
)
#: Only the battery answered: a VCU read that failed even after its retry.
BATTERY_ONLY = ScooterState(
    serial=SERIAL,
    profile_key="gt3_pro",
    responding=frozenset({BoardRole.BMS1}),
    battery=BATTERY,
)
POWERED_ON = ScooterState(
    serial=SERIAL,
    profile_key="gt3_pro",
    responding=frozenset({BoardRole.BMS1, BoardRole.BLE, BoardRole.VCU, BoardRole.MCU}),
    battery=BATTERY,
    vehicle=VEHICLE,
    motor=MOTOR,
)
INFO_FULL = DeviceInfo(
    serial=SERIAL,
    part_number="AA.00.0000.00",
    vcu_firmware="1.2.2",
    mcu_firmware="2.1.1",
    bms_firmware="1.0.7",
    ble_firmware="0.2.8",
    activated_on=date(2025, 6, 1),
    motor_part_number="BB.00.0000.00",
)
#: Everything but the motor part number, which lives on the sleeping MCU.
INFO_PARKED = replace(INFO_FULL, motor_part_number=None)


def service_info(name: str = SERIAL, address: str = ADDRESS) -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=["6e400001-b5a3-f393-e0a9-e50e24dcca9e"],
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=None,
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
def auto_enable_bluetooth(enable_bluetooth: None) -> None:
    """The integration depends on Home Assistant's Bluetooth stack."""


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Segway SuperScooter GT3 Pro",
        unique_id=ADDRESS,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_PASSWORD: PASSWORD.hex(),
            CONF_PROFILE: "gt3_pro",
            CONF_SERIAL: SERIAL,
        },
    )


class FakeSession:
    """Stands in for ScooterSession; reads come from a dict."""

    def __init__(self, state: ScooterState, info: DeviceInfo, reads: dict[str, Any]) -> None:
        self.profile = profiles.GT3_PRO
        self.poll = AsyncMock(return_value=state)
        self.identify = AsyncMock(return_value=info)
        self.read = AsyncMock(side_effect=lambda name: reads.get(name))


class FakeScooter:
    """Configurable stand-in for scootscoot.Scooter.

    Tests set `state`, `info`, `reads` or `error` on the class-level `control`
    object; every Scooter the integration constructs then behaves accordingly.
    """

    def __init__(self, control: MagicMock) -> None:
        self._control = control

    def session(self) -> Any:
        control = self._control

        class _Ctx:
            async def __aenter__(self) -> FakeSession:
                control.sessions += 1
                if control.error is not None:
                    raise control.error
                session = FakeSession(control.state, control.info, control.reads)
                control.last_session = session
                return session

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()

    async def identify(self) -> DeviceInfo:
        if self._control.error is not None:
            raise self._control.error
        return self._control.info


@pytest.fixture
def scooter() -> Generator[MagicMock]:
    """Patch Scooter everywhere the integration constructs one."""
    control = MagicMock()
    control.state = PARKED
    control.info = INFO_PARKED
    control.reads = {"rBmsCellVolFrequence": CELLS, "rTimeFull": 0}
    control.error = None
    control.sessions = 0

    def factory(*_args: Any, **_kwargs: Any) -> FakeScooter:
        return FakeScooter(control)

    with (
        patch("custom_components.scootscoot.coordinator.Scooter", side_effect=factory),
        patch("custom_components.scootscoot.config_flow.Scooter", side_effect=factory),
    ):
        yield control


@pytest.fixture
def ble_device() -> Generator[MagicMock]:
    """Make the scooter reachable; set return_value to None to take it away."""
    # Both modules reach it through the same homeassistant.components.bluetooth
    # module object, so one patch covers them.
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        return_value=BLEDevice(ADDRESS, SERIAL, {}),
    ) as mock:
        yield mock


@pytest.fixture(autouse=True)
def no_stale_connection_cleanup() -> Generator[None]:
    """Setup would otherwise try to reach BlueZ over D-Bus."""
    with patch("custom_components.scootscoot.close_stale_connections_by_address"):
        yield
