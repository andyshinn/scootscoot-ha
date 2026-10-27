"""The ScootScoot integration: Segway-Ninebot scooter telemetry over BLE."""

from __future__ import annotations

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import ScootScootConfigEntry, ScootScootCoordinator, snapshot_store

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ScootScootConfigEntry) -> bool:
    """Set up a scooter from a config entry."""
    coordinator = ScootScootCoordinator(hass, entry)
    await coordinator.async_restore()
    await close_stale_connections_by_address(coordinator.address)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _async_advertised(
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        coordinator.async_scooter_seen()

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_advertised,
            bluetooth.BluetoothCallbackMatcher(address=coordinator.address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    # Not async_config_entry_first_refresh: a scooter that is out riding or
    # held by the phone app at startup is normal, not a setup failure, and the
    # restored state is worth showing in the meantime. Entities exist either
    # way, and the poll schedule takes over from here.
    entry.async_create_background_task(hass, coordinator.async_refresh(), f"{DOMAIN} first poll")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ScootScootConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ScootScootConfigEntry) -> None:
    """Delete the stored state along with the entry."""
    await snapshot_store(hass, entry.entry_id).async_remove()
