"""Diagnostics for the Pylontech integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PylontechConfigEntry
from .const import CONF_ENCRYPTION_KEY

TO_REDACT = {CONF_ENCRYPTION_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PylontechConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "bridge_available": coordinator.bridge.available,
        "high_voltage": coordinator.hv,
        "info": coordinator.info,
        "module_info": coordinator.module_info,
        "tracker": coordinator.tracker,
        "data": asdict(coordinator.data) if coordinator.data else None,
    }
