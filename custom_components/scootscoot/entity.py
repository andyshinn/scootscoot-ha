"""Base entity for ScootScoot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import ScootScootCoordinator
from .data import Snapshot


@dataclass(frozen=True, kw_only=True)
class ScootScootEntityDescription(EntityDescription):
    """What an entity reads, and how fresh that reading is."""

    #: When the reading behind this entity was taken. None means the entity is
    #: about the link itself and is always available.
    stamp_fn: Callable[[Snapshot], datetime | None] | None
    #: Register the entity depends on. Profiles without it get no entity.
    register: str | None = None


def battery_stamp(snapshot: Snapshot) -> datetime | None:
    return snapshot.battery_at


def vehicle_stamp(snapshot: Snapshot) -> datetime | None:
    return snapshot.vehicle_at


def motor_stamp(snapshot: Snapshot) -> datetime | None:
    return snapshot.motor_at


def seen_stamp(snapshot: Snapshot) -> datetime | None:
    return snapshot.seen_at


class ScootScootEntity(CoordinatorEntity[ScootScootCoordinator]):
    """Keeps showing the last reading through failed polls, until it is stale."""

    _attr_has_entity_name = True
    entity_description: ScootScootEntityDescription

    def __init__(
        self,
        coordinator: ScootScootCoordinator,
        description: ScootScootEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}-{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        # Deliberately not coordinator.last_update_success: a poll that finds
        # the phone app holding the scooter says nothing about the battery.
        stamp_fn = self.entity_description.stamp_fn
        if stamp_fn is None:
            return True
        return self.coordinator.is_fresh(stamp_fn(self.coordinator.data))


def supported[D: ScootScootEntityDescription](
    coordinator: ScootScootCoordinator, descriptions: tuple[D, ...]
) -> list[D]:
    """Drop descriptions whose register this scooter's profile lacks."""
    profile = coordinator.profile
    return [
        description
        for description in descriptions
        if description.register is None or profile.has_register(description.register)
    ]
