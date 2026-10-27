"""What the integration knows about a scooter, and since when.

Pure by design, like the library's models module: merging a poll into the last
snapshot is where the "keep the last value" behaviour lives, so it stays out of
the coordinator and gets tested on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime
from typing import Any

from scootscoot import BatteryState, BoardRole, DeviceInfo, MotorState, ScooterState, VehicleState

from .const import CHARGING_CURRENT_A, IDENTIFY_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The latest reading of each board, each stamped with when it was taken.

    A group is replaced only when its board answers. A parked scooter's VCU and
    MCU are asleep, so their last ride telemetry is kept rather than blanked;
    the timestamps let entities decide when a value is too old to show.
    """

    battery: BatteryState | None = None
    battery_at: datetime | None = None
    cell_voltages: tuple[float, ...] | None = None
    time_to_full_min: int | None = None
    vehicle: VehicleState | None = None
    vehicle_at: datetime | None = None
    motor: MotorState | None = None
    motor_at: datetime | None = None
    info: DeviceInfo | None = None
    info_at: datetime | None = None
    #: Last successful connection, whichever boards answered.
    seen_at: datetime | None = None
    #: Whether the VCU answered on the latest successful poll -- i.e. the
    #: scooter is switched on.
    powered_on: bool = False

    @property
    def charging(self) -> bool | None:
        """Whether charge is flowing into the pack, or None if unknown."""
        if self.battery is None or self.battery.current is None:
            return None
        return self.battery.current < CHARGING_CURRENT_A


def merge(
    previous: Snapshot,
    state: ScooterState,
    *,
    now: datetime,
    cell_voltages: tuple[float, ...] | None = None,
    time_to_full_min: int | None = None,
    info: DeviceInfo | None = None,
) -> Snapshot:
    """Fold one successful poll into the previous snapshot."""
    snapshot = replace(
        previous,
        seen_at=now,
        powered_on=BoardRole.VCU in state.responding,
    )
    if state.battery is not None:
        snapshot = replace(
            snapshot,
            battery=state.battery,
            battery_at=now,
            cell_voltages=cell_voltages,
            time_to_full_min=time_to_full_min,
        )
    if state.vehicle is not None:
        snapshot = replace(snapshot, vehicle=state.vehicle, vehicle_at=now)
    if state.motor is not None:
        snapshot = replace(snapshot, motor=state.motor, motor_at=now)
    if info is not None:
        snapshot = replace(snapshot, info=merge_info(previous.info, info), info_at=now)
    return snapshot


def merge_info(previous: DeviceInfo | None, current: DeviceInfo) -> DeviceInfo:
    """Overlay a fresh identity read on the last one, field by field.

    identify() returns None for every field held by a sleeping board, so a read
    from a parked scooter must not erase firmware versions learned earlier.
    """
    if previous is None or previous.serial != current.serial:
        return current
    updates = {
        field.name: value
        for field in fields(current)
        if (value := getattr(current, field.name)) is not None
    }
    return replace(previous, **updates)


def needs_identity(snapshot: Snapshot, *, now: datetime, powered_on: bool) -> bool:
    """Whether this poll should also read identity.

    Most identity registers live on the VCU, which only answers while the
    scooter is on, so beyond the very first read this waits for it to wake.
    """
    if snapshot.info is None or snapshot.info_at is None:
        return True
    if not powered_on:
        return False
    return snapshot.info.vcu_firmware is None or now - snapshot.info_at >= IDENTIFY_INTERVAL


# --- persistence -------------------------------------------------------------
#
# Stored as plain JSON so a restart does not blank every sensor until the next
# successful poll, which for a scooter that is out riding could be hours.


def dump(snapshot: Snapshot) -> dict[str, Any]:
    """Serialize a snapshot to JSON-safe primitives."""
    return {field.name: _dump(getattr(snapshot, field.name)) for field in fields(snapshot)}


def load(data: dict[str, Any] | None) -> Snapshot:
    """Rebuild a snapshot, or start empty if the stored one is unreadable."""
    if not data:
        return Snapshot()
    try:
        return Snapshot(
            battery=_load_group(BatteryState, data.get("battery")),
            battery_at=_load_datetime(data.get("battery_at")),
            cell_voltages=_load_tuple(data.get("cell_voltages")),
            time_to_full_min=data.get("time_to_full_min"),
            vehicle=_load_group(VehicleState, data.get("vehicle")),
            vehicle_at=_load_datetime(data.get("vehicle_at")),
            motor=_load_group(MotorState, data.get("motor")),
            motor_at=_load_datetime(data.get("motor_at")),
            info=_load_info(data.get("info")),
            info_at=_load_datetime(data.get("info_at")),
            seen_at=_load_datetime(data.get("seen_at")),
            powered_on=bool(data.get("powered_on", False)),
        )
    except (TypeError, ValueError, AttributeError):
        # A library upgrade that renames a field must not break setup; the
        # next poll repopulates everything.
        _LOGGER.warning("discarding unreadable stored scooter state", exc_info=True)
        return Snapshot()


def _dump(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _dump(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_dump(item) for item in value]
    return value


def _load_group[T](cls: type[T], data: dict[str, Any] | None) -> T | None:
    if data is None:
        return None
    known = {field.name for field in fields(cls)}  # type: ignore[arg-type]
    return cls(**{key: _load_value(value) for key, value in data.items() if key in known})


def _load_info(data: dict[str, Any] | None) -> DeviceInfo | None:
    info = _load_group(DeviceInfo, data)
    if info is not None and isinstance(info.activated_on, str):
        info = replace(info, activated_on=date.fromisoformat(info.activated_on))
    return info


def _load_value(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _load_tuple(value: list[float] | None) -> tuple[float, ...] | None:
    return tuple(value) if value is not None else None


def _load_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None
