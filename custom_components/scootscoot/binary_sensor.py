"""Binary sensors for ScootScoot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ScootScootConfigEntry, ScootScootCoordinator
from .data import Snapshot
from .entity import (
    ScootScootEntity,
    ScootScootEntityDescription,
    battery_stamp,
    seen_stamp,
    supported,
    vehicle_stamp,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ScootScootBinarySensorEntityDescription(
    BinarySensorEntityDescription, ScootScootEntityDescription
):
    """Describes a ScootScoot binary sensor."""

    value_fn: Callable[[ScootScootCoordinator, Snapshot], bool | None]


def _fault(snapshot: Snapshot) -> bool | None:
    if snapshot.vehicle is None or snapshot.vehicle.error_code is None:
        return None
    return snapshot.vehicle.error_code != 0


BINARY_SENSORS: tuple[ScootScootBinarySensorEntityDescription, ...] = (
    ScootScootBinarySensorEntityDescription(
        key="charging",
        register="rBMSCur",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        stamp_fn=battery_stamp,
        value_fn=lambda _, s: s.charging,
    ),
    ScootScootBinarySensorEntityDescription(
        key="powered_on",
        translation_key="powered_on",
        device_class=BinarySensorDeviceClass.POWER,
        stamp_fn=seen_stamp,
        value_fn=lambda _, s: s.powered_on,
    ),
    ScootScootBinarySensorEntityDescription(
        key="fault",
        translation_key="fault",
        register="rErrorCode",
        device_class=BinarySensorDeviceClass.PROBLEM,
        stamp_fn=vehicle_stamp,
        value_fn=lambda _, s: _fault(s),
    ),
    # Whether the latest poll got through. Always available: "unreachable" is
    # the answer, not the absence of one.
    ScootScootBinarySensorEntityDescription(
        key="connection",
        translation_key="connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        stamp_fn=None,
        value_fn=lambda coordinator, _: coordinator.last_update_success,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ScootScootConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ScootScoot binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        ScootScootBinarySensor(coordinator, description)
        for description in supported(coordinator, BINARY_SENSORS)
    )


class ScootScootBinarySensor(ScootScootEntity, BinarySensorEntity):
    """A yes/no fact about the scooter."""

    entity_description: ScootScootBinarySensorEntityDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)
