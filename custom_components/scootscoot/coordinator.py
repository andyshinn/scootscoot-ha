"""Polls a scooter over BLE on an adaptive schedule.

Every poll is one connection: connect, authenticate, read, disconnect. The
scooter accepts a single BLE client at a time, so nothing is held between polls
and the phone app can always get in.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from scootscoot import (
    AuthenticationFailed,
    BoardRole,
    Credentials,
    DeviceProfile,
    PairingRequired,
    Scooter,
    ScooterSession,
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
    MANUFACTURER,
    POLL_TIMEOUT,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)
from .data import Snapshot, dump, load, merge, needs_identity

_LOGGER = logging.getLogger(__name__)

type ScootScootConfigEntry = ConfigEntry[ScootScootCoordinator]


def snapshot_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Where one entry's last known state lives between restarts."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


class ScootScootCoordinator(DataUpdateCoordinator[Snapshot]):
    """Owns the polling schedule and the last known state of one scooter."""

    config_entry: ScootScootConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ScootScootConfigEntry) -> None:
        options = entry.options
        self._slow = timedelta(minutes=options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL))
        self._fast = timedelta(minutes=options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL))
        stale_hours = options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER)
        self._stale_after = timedelta(hours=stale_hours) if stale_hours else None

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=self._slow,
        )

        self.address: str = entry.data[CONF_ADDRESS]
        self.profile: DeviceProfile = profiles.get(entry.data[CONF_PROFILE])
        self._credentials = Credentials(password=bytes.fromhex(entry.data[CONF_PASSWORD]))
        self._store = snapshot_store(hass, entry.entry_id)
        #: Set when the last poll found no advertisement to connect to, so the
        #: next advertisement can trigger a poll instead of waiting it out.
        self._absent = False
        # Nothing has been read this run. Restored values still show, but the
        # connectivity sensor must not claim a link it has not made.
        self.last_update_success = False

    @property
    def device_info(self) -> HADeviceInfo:
        """Device registry entry shared by every entity."""
        info = self.data.info if self.data else None
        return HADeviceInfo(
            identifiers={(DOMAIN, self.address)},
            connections={(dr.CONNECTION_BLUETOOTH, self.address)},
            name=self.config_entry.title,
            manufacturer=MANUFACTURER,
            model=self.profile.name,
            model_id=info.part_number if info else None,
            serial_number=self.config_entry.data.get(CONF_SERIAL),
            sw_version=info.vcu_firmware if info else None,
        )

    def is_fresh(self, at: datetime | None) -> bool:
        """Whether a reading taken at `at` is still recent enough to show."""
        if at is None:
            return False
        return self._stale_after is None or dt_util.utcnow() - at <= self._stale_after

    async def async_restore(self) -> None:
        """Load the last known state so a restart does not blank every sensor."""
        self.data = load(await self._store.async_load())

    @callback
    def async_scooter_seen(self) -> None:
        """Poll as soon as an absent scooter advertises again."""
        if not self._absent:
            return
        self._absent = False
        _LOGGER.debug("%s is advertising again; polling now", self.address)
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} poll on return"
        )

    @callback
    def _async_refresh_finished(self) -> None:
        # The base class stays quiet through a streak of failed polls, but
        # readings age out during one -- a scooter left at work all day -- so
        # entities still need a nudge to re-check their freshness.
        if not self.last_update_success:
            self.async_update_listeners()

    async def _async_update_data(self) -> Snapshot:
        previous = self.data or Snapshot()

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            # Out riding, or out of range of every adapter and proxy. Not worth
            # a 20 s connect attempt; the advertisement callback will wake us.
            self._absent = True
            self.update_interval = self._slow
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="not_in_range")

        scooter = Scooter(ble_device, self.profile, self._credentials)
        try:
            async with asyncio.timeout(POLL_TIMEOUT):
                snapshot = await self._poll(scooter, previous)
        except (AuthenticationFailed, PairingRequired) as err:
            # A wrong password, or a scooter that was reset and re-paired with
            # the app under a new one. Either way only the user can fix it.
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except VehicleBusy as err:
            self.update_interval = self._slow
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="busy") from err
        except (ScootScootError, TimeoutError) as err:
            self.update_interval = self._slow
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="poll_failed",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err

        self.update_interval = (
            self._fast if snapshot.charging or snapshot.powered_on else self._slow
        )
        if snapshot.info != previous.info:
            self._async_update_device(snapshot)
        self._store.async_delay_save(lambda: dump(snapshot), STORAGE_SAVE_DELAY)
        return snapshot

    async def _poll(self, scooter: Scooter, previous: Snapshot) -> Snapshot:
        now = dt_util.utcnow()
        async with scooter.session() as session:
            state = await session.poll()

            cells: tuple[float, ...] | None = None
            time_to_full: int | None = None
            if BoardRole.BMS1 in state.responding:
                # Not in the library's poll set, but cheap once connected.
                cells = _as_floats(await _read(session, "rBmsCellVolFrequence"))
                time_to_full = _as_int(await _read(session, "rTimeFull"))

            powered_on = BoardRole.VCU in state.responding
            info = None
            if needs_identity(previous, now=now, powered_on=powered_on):
                info = await session.identify()

        return merge(
            previous,
            state,
            now=now,
            cell_voltages=cells,
            time_to_full_min=time_to_full,
            info=info,
        )

    @callback
    def _async_update_device(self, snapshot: Snapshot) -> None:
        """Push identity learned after setup into the device registry."""
        info = snapshot.info
        if info is None:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier(
            (DOMAIN, self.address), self.config_entry.entry_id
        )
        if device is None:
            return
        registry.async_update_device(
            device.id,
            model_id=info.part_number,
            sw_version=info.vcu_firmware,
        )


async def _read(session: ScooterSession, name: str) -> object:
    if not session.profile.has_register(name):
        return None
    return await session.read(name)


def _as_floats(value: object) -> tuple[float, ...] | None:
    if isinstance(value, tuple):
        return tuple(float(item) for item in value)
    return None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
