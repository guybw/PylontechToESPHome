"""The Pylontech (ESPHome bridge) integration."""

from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from yarl import URL

from .bridge import PylontechBridge
from .const import (
    CONF_CELL_SENSORS,
    CONF_ENCRYPTION_KEY,
    CONF_PROXY_NAME,
    CONF_SYNC_TIME,
    DEFAULT_PORT,
    DEFAULT_PROXY_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
)
from .coordinator import STORE_VERSION, PylontechDataUpdateCoordinator
from .dashboard import async_create_dashboard, take_dashboard_request
from .frontend import async_register_frontend
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

    _backfill_serial_port(hass, entry, bridge)

    entry.runtime_data = coordinator
    _prune_optional_entities(hass, entry)
    # Stack device must exist first: module devices reference it by registry id.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Pylontech",
        manufacturer="Pylontech",
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Bundled battery card: in the card picker without adding a resource.
    try:
        await async_register_frontend(hass)
    except Exception as err:  # noqa: BLE001 - never let the card stop the integration
        LOGGER.warning("Could not register the bundled battery card: %s", err)

    if take_dashboard_request(hass, entry.data[CONF_HOST]):
        # Ticked in the setup form. The entities are registered by now; wait
        # for HA to finish starting so the dashboards component is ready.
        async def _create_dashboard(_hass: HomeAssistant) -> None:
            await async_create_dashboard(hass, entry)

        entry.async_on_unload(async_at_started(hass, _create_dashboard))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    async_register_services(hass)
    return True


def _backfill_serial_port(
    hass: HomeAssistant, entry: PylontechConfigEntry, bridge: PylontechBridge
) -> None:
    """Add the ESPHome ``esphome-hass://`` URL to entries created before 0.2.0.

    Without it Home Assistant keeps listing the ESP's serial port as unused
    (Settings -> System -> Hardware). Matching URL lets it show as in use.
    Re-adding the integration is not needed.
    """
    if entry.data.get(CONF_DEVICE):
        return
    info = bridge.esphome_device_info
    mac = getattr(info, "mac_address", None)
    want = format_mac(mac) if mac else None
    esp = next(
        (
            e
            for e in hass.config_entries.async_entries("esphome")
            if (want and e.unique_id == want)
            or e.data.get(CONF_HOST) == entry.data[CONF_HOST]
        ),
        None,
    )
    if esp is None:
        return
    url = str(
        URL.build(
            scheme="esphome-hass",
            host="esphome",
            path=f"/{esp.entry_id}",
            query={"port_name": entry.data.get(CONF_PROXY_NAME, DEFAULT_PROXY_NAME)},
        )
    )
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DEVICE: url})


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


async def async_remove_entry(hass: HomeAssistant, entry: PylontechConfigEntry) -> None:
    """Delete the stored energy counters / balance history with the entry."""
    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: PylontechConfigEntry) -> None:
    """Reload when options (scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
