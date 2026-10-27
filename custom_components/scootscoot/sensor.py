"""Sensors for ScootScoot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import ScootScootConfigEntry, ScootScootCoordinator
from .data import Snapshot
from .entity import (
    ScootScootEntity,
    ScootScootEntityDescription,
    battery_stamp,
    motor_stamp,
    supported,
    vehicle_stamp,
)

#: The library reports capacity in amp-hours, which Home Assistant has no unit
#: constant or device class for.
AMP_HOURS = "Ah"

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ScootScootSensorEntityDescription(SensorEntityDescription, ScootScootEntityDescription):
    """Describes a ScootScoot sensor."""

    value_fn: Callable[[Snapshot], StateType | datetime]


def _power(snapshot: Snapshot) -> float | None:
    battery = snapshot.battery
    if battery is None or battery.voltage is None or battery.current is None:
        return None
    return round(battery.voltage * battery.current, 1)


def _temperatures(snapshot: Snapshot) -> tuple[int | float, ...]:
    if snapshot.battery is None or snapshot.battery.temperatures is None:
        return ()
    return snapshot.battery.temperatures


def _cells(snapshot: Snapshot) -> tuple[float, ...]:
    return snapshot.cell_voltages or ()


def _cell_delta(snapshot: Snapshot) -> float | None:
    cells = _cells(snapshot)
    return round(max(cells) - min(cells), 3) if cells else None


BATTERY_SENSORS: tuple[ScootScootSensorEntityDescription, ...] = (
    ScootScootSensorEntityDescription(
        key="battery",
        register="rBmsSOC",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.battery.soc_percent if s.battery else None,
    ),
    ScootScootSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        register="rBMSVolt",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.battery.voltage if s.battery else None,
    ),
    ScootScootSensorEntityDescription(
        key="battery_current",
        translation_key="battery_current",
        register="rBMSCur",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.battery.current if s.battery else None,
    ),
    ScootScootSensorEntityDescription(
        key="battery_power",
        translation_key="battery_power",
        register="rBMSCur",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        stamp_fn=battery_stamp,
        value_fn=_power,
    ),
    ScootScootSensorEntityDescription(
        key="remaining_capacity",
        translation_key="remaining_capacity",
        register="rBmsRB",
        native_unit_of_measurement=AMP_HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.battery.capacity_remaining_ah if s.battery else None,
    ),
    ScootScootSensorEntityDescription(
        key="full_capacity",
        translation_key="full_capacity",
        register="rBmsCapFull",
        native_unit_of_measurement=AMP_HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.battery.capacity_full_ah if s.battery else None,
    ),
    ScootScootSensorEntityDescription(
        key="charge_cycles",
        translation_key="charge_cycles",
        register="rBmsCycleCountLT",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.battery.cycles if s.battery else None,
    ),
    ScootScootSensorEntityDescription(
        key="time_to_full",
        translation_key="time_to_full",
        register="rTimeFull",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.time_to_full_min,
    ),
    # Unmapped: exposed raw so its values can be matched against the charger.
    ScootScootSensorEntityDescription(
        key="charge_status",
        translation_key="charge_status",
        register="rChargeStatus",
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=battery_stamp,
        value_fn=lambda s: s.battery.charge_status if s.battery else None,
    ),
    ScootScootSensorEntityDescription(
        key="battery_temperature_max",
        translation_key="battery_temperature_max",
        register="rBmsTemps",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        stamp_fn=battery_stamp,
        value_fn=lambda s: max(temps) if (temps := _temperatures(s)) else None,
    ),
    ScootScootSensorEntityDescription(
        key="battery_temperature_min",
        translation_key="battery_temperature_min",
        register="rBmsTemps",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        stamp_fn=battery_stamp,
        value_fn=lambda s: min(temps) if (temps := _temperatures(s)) else None,
    ),
    ScootScootSensorEntityDescription(
        key="cell_voltage_max",
        translation_key="cell_voltage_max",
        register="rBmsCellVolFrequence",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=battery_stamp,
        value_fn=lambda s: max(cells) if (cells := _cells(s)) else None,
    ),
    ScootScootSensorEntityDescription(
        key="cell_voltage_min",
        translation_key="cell_voltage_min",
        register="rBmsCellVolFrequence",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=battery_stamp,
        value_fn=lambda s: min(cells) if (cells := _cells(s)) else None,
    ),
    ScootScootSensorEntityDescription(
        key="cell_voltage_delta",
        translation_key="cell_voltage_delta",
        register="rBmsCellVolFrequence",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=battery_stamp,
        value_fn=_cell_delta,
    ),
)

VEHICLE_SENSORS: tuple[ScootScootSensorEntityDescription, ...] = (
    ScootScootSensorEntityDescription(
        key="odometer",
        translation_key="odometer",
        register="rMileage",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        stamp_fn=vehicle_stamp,
        value_fn=lambda s: s.vehicle.odometer_km if s.vehicle else None,
    ),
    ScootScootSensorEntityDescription(
        key="range",
        translation_key="range",
        register="rLeftMileage",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        stamp_fn=vehicle_stamp,
        value_fn=lambda s: s.vehicle.range_km if s.vehicle else None,
    ),
    # The dashboard's figure. Usually tracks the BMS SOC; disabled to avoid two
    # battery entities that look interchangeable.
    ScootScootSensorEntityDescription(
        key="displayed_battery",
        translation_key="displayed_battery",
        register="rBattery",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        stamp_fn=vehicle_stamp,
        value_fn=lambda s: s.vehicle.battery_percent if s.vehicle else None,
    ),
    ScootScootSensorEntityDescription(
        key="body_temperature",
        translation_key="body_temperature",
        register="rBodyTemp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=vehicle_stamp,
        value_fn=lambda s: s.vehicle.body_temp_c if s.vehicle else None,
    ),
    ScootScootSensorEntityDescription(
        key="error_code",
        translation_key="error_code",
        register="rErrorCode",
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=vehicle_stamp,
        value_fn=lambda s: s.vehicle.error_code if s.vehicle else None,
    ),
    ScootScootSensorEntityDescription(
        key="warning_code",
        translation_key="warning_code",
        register="rWarnCode",
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=vehicle_stamp,
        value_fn=lambda s: s.vehicle.warning_code if s.vehicle else None,
    ),
    ScootScootSensorEntityDescription(
        key="motor_temperature",
        translation_key="motor_temperature",
        register="rMCUBodyTemp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=motor_stamp,
        value_fn=lambda s: s.motor.body_temp_c if s.motor else None,
    ),
)

LAST_SEEN = ScootScootSensorEntityDescription(
    key="last_seen",
    translation_key="last_seen",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
    stamp_fn=None,
    value_fn=lambda s: s.seen_at,
)


def _indexed_sensors(
    coordinator: ScootScootCoordinator,
) -> list[ScootScootSensorEntityDescription]:
    """One disabled sensor per cell and per pack temperature probe."""
    profile = coordinator.profile
    descriptions: list[ScootScootSensorEntityDescription] = []

    if profile.has_register("rBmsCellVolFrequence"):
        for index in range(profile.register("rBmsCellVolFrequence").count):
            descriptions.append(
                ScootScootSensorEntityDescription(
                    key=f"cell_{index + 1}_voltage",
                    translation_key="cell_voltage",
                    translation_placeholders={"index": str(index + 1)},
                    device_class=SensorDeviceClass.VOLTAGE,
                    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                    state_class=SensorStateClass.MEASUREMENT,
                    suggested_display_precision=3,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    stamp_fn=battery_stamp,
                    value_fn=lambda s, i=index: _at(_cells(s), i),
                )
            )

    if profile.has_register("rBmsTemps"):
        for index in range(profile.register("rBmsTemps").count):
            descriptions.append(
                ScootScootSensorEntityDescription(
                    key=f"battery_temperature_{index + 1}",
                    translation_key="battery_temperature",
                    translation_placeholders={"index": str(index + 1)},
                    device_class=SensorDeviceClass.TEMPERATURE,
                    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    stamp_fn=battery_stamp,
                    value_fn=lambda s, i=index: _at(_temperatures(s), i),
                )
            )

    return descriptions


def _at[T](values: tuple[T, ...], index: int) -> T | None:
    return values[index] if index < len(values) else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ScootScootConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ScootScoot sensors."""
    coordinator = entry.runtime_data
    descriptions = [
        *supported(coordinator, BATTERY_SENSORS),
        *supported(coordinator, VEHICLE_SENSORS),
        *_indexed_sensors(coordinator),
        LAST_SEEN,
    ]
    async_add_entities(ScootScootSensor(coordinator, description) for description in descriptions)


class ScootScootSensor(ScootScootEntity, SensorEntity):
    """A reading from the scooter."""

    entity_description: ScootScootSensorEntityDescription

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)
