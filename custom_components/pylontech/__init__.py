"""The Pylontech (ESPHome serial bridge) integration."""

from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .bridge import PylontechBridge
from .const import (
    CONF_CELL_SENSORS,
    CONF_ENCRYPTION_KEY,
    CONF_PROXY_NAME,
    CONF_SYNC_TIME,
    DEFAULT_PORT,
    DEFAULT_PROXY_NAME,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import PylontechDataUpdateCoordinator
from .services import async_register_services

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

type PylontechConfigEntry = ConfigEntry[PylontechDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PylontechConfigEntry) -> bool:
    """Set up Pylontech from a config entry."""
    bridge = PylontechBridge(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data[CONF_ENCRYPTION_KEY],
        entry.data.get(CONF_PROXY_NAME, DEFAULT_PROXY_NAME),
        sync_time=entry.options.get(CONF_SYNC_TIME, False),
    )
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = PylontechDataUpdateCoordinator(hass, entry, bridge, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    _prune_optional_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    async_register_services(hass)
    return True


# per-cell / weakest-cell / balancing entities exist only while the option is on
_OPTIONAL_CELL_UID = re.compile(
    r"_module_\d+_(cell_\d+_voltage|weakest_cell|balancing)$"
)


def _prune_optional_entities(hass: HomeAssistant, entry: PylontechConfigEntry) -> None:
    """Drop cell-sensor entities from the registry when the option is off."""
    if entry.options.get(CONF_CELL_SENSORS):
        return
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if _OPTIONAL_CELL_UID.search(entity.unique_id):
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: PylontechConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: PylontechConfigEntry) -> None:
    """Reload when options (scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
