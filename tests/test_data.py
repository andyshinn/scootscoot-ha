"""Snapshot merging, charging detection, and persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from scootscoot import BoardRole

from custom_components.scootscoot.data import (
    Snapshot,
    dump,
    load,
    merge,
    merge_info,
    needs_identity,
)

from .conftest import (
    BATTERY,
    BATTERY_ONLY,
    CELLS,
    INFO_FULL,
    INFO_PARKED,
    PARKED,
    POWERED_ON,
    VEHICLE,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=15)


def test_parked_poll_keeps_the_last_motor_reading() -> None:
    ridden = merge(Snapshot(), POWERED_ON, now=T0)
    parked = merge(ridden, PARKED, now=T1)

    assert parked.battery_at == T1
    assert parked.vehicle_at == T1
    assert parked.motor == POWERED_ON.motor
    assert parked.motor_at == T0
    assert parked.seen_at == T1
    assert parked.powered_on is False


def test_a_poll_without_the_vcu_keeps_last_ride_telemetry() -> None:
    ridden = merge(Snapshot(), POWERED_ON, now=T0)
    later = merge(ridden, BATTERY_ONLY, now=T1)

    assert later.vehicle == VEHICLE
    assert later.vehicle_at == T0
    assert later.battery_at == T1


def test_powered_on_follows_the_mcu_not_the_vcu() -> None:
    assert merge(Snapshot(), POWERED_ON, now=T0).powered_on is True
    # The VCU answers on a parked scooter; that alone must not read as on.
    assert BoardRole.VCU in PARKED.responding
    assert merge(Snapshot(), PARKED, now=T0).powered_on is False


def test_cells_and_time_to_full_travel_with_the_battery() -> None:
    snapshot = merge(Snapshot(), PARKED, now=T0, cell_voltages=CELLS, time_to_full_min=95)
    assert snapshot.cell_voltages == CELLS
    assert snapshot.time_to_full_min == 95


def test_charging_is_negative_current_beyond_rest_noise() -> None:
    def at(current: float | None) -> bool | None:
        return Snapshot(battery=replace(BATTERY, current=current)).charging

    assert at(-0.06) is False  # what the pack reads at rest
    assert at(5.0) is False  # discharging
    assert at(-3.2) is True
    assert at(None) is None
    assert Snapshot().charging is None


def test_merge_info_keeps_fields_a_sleeping_mcu_did_not_return() -> None:
    assert merge_info(INFO_FULL, INFO_PARKED) == INFO_FULL
    assert merge_info(None, INFO_PARKED) == INFO_PARKED


def test_merge_info_replaces_a_different_scooter_outright() -> None:
    other = replace(INFO_PARKED, serial="03GGG0000000000")
    assert merge_info(INFO_FULL, other) == other


def test_identity_is_read_first_even_when_parked() -> None:
    assert needs_identity(Snapshot(), now=T0, powered_on=False)


def test_identity_waits_for_power_only_for_the_motor_part_number() -> None:
    partial = Snapshot(info=INFO_PARKED, info_at=T0)
    assert not needs_identity(partial, now=T1, powered_on=False)
    assert needs_identity(partial, now=T1, powered_on=True)

    full = Snapshot(info=INFO_FULL, info_at=T0)
    assert not needs_identity(full, now=T1, powered_on=True)


def test_identity_is_reread_daily_and_when_the_vcu_was_missed() -> None:
    full = Snapshot(info=INFO_FULL, info_at=T0)
    assert needs_identity(full, now=T0 + timedelta(days=1), powered_on=False)

    missed = Snapshot(info=replace(INFO_PARKED, vcu_firmware=None), info_at=T0)
    assert needs_identity(missed, now=T1, powered_on=False)


def test_dump_and_load_round_trip() -> None:
    snapshot = merge(
        Snapshot(),
        POWERED_ON,
        now=T0,
        cell_voltages=CELLS,
        time_to_full_min=0,
        info=INFO_FULL,
    )
    assert load(dump(snapshot)) == snapshot


def test_load_tolerates_missing_and_unreadable_data() -> None:
    assert load(None) == Snapshot()
    assert load({}) == Snapshot()
    assert load({"battery": {"soc_percent": 50, "renamed_field": 1}}).battery is not None
    assert load({"battery_at": "not a date"}) == Snapshot()
