"""Services for the Pylontech integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from . import protocol
from .bridge import PylontechConnectionError
from .const import COMMAND_TIMEOUT, DOMAIN

SERVICE_GET_LOG = "get_log"
SERVICE_SET_TIME = "set_time"

ATTR_SOURCE = "source"
ATTR_COUNT = "count"
ATTR_DATETIME = "datetime"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

_GET_LOG_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SOURCE, default="event"): vol.In(["event", "history"]),
        vol.Optional(ATTR_COUNT, default=20): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=50)
        ),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

_SET_TIME_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DATETIME): cv.datetime,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_LOG):
        return

    def _resolve_coordinator(call: ServiceCall):
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if entry_id:
            entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError("No loaded Pylontech config entry found")
        if len(entries) > 1:
            raise ServiceValidationError(
                "Multiple Pylontech entries — pass config_entry_id"
            )
        return entries[0].runtime_data

    async def _get_log(call: ServiceCall) -> ServiceResponse:
        coordinator = _resolve_coordinator(call)
        source: str = call.data[ATTR_SOURCE]
        count: int = call.data[ATTR_COUNT]
        bridge = coordinator.bridge

        try:
            latest = protocol.parse_data_record(
                await bridge.async_command(f"data {source}", timeout=COMMAND_TIMEOUT)
            )
            top = latest.get("index")
            if top is None:
                return {"source": source, "count": 0, "records": []}
            raws = await bridge.async_commands(
                [f"data {source} {i}" for i in range(top, max(-1, top - count), -1)],
                timeout=COMMAND_TIMEOUT,
            )
        except PylontechConnectionError as err:
            raise ServiceValidationError(str(err)) from err

        records = [
            rec
            for raw in raws
            if (rec := protocol.parse_data_record(raw)).get("index") is not None
        ]
        return {"source": source, "count": len(records), "records": records}

    async def _set_time(call: ServiceCall) -> ServiceResponse:
        coordinator = _resolve_coordinator(call)
        when = call.data.get(ATTR_DATETIME) or dt_util.now()
        try:
            await coordinator.bridge.async_set_time(when)
            readback = await coordinator.bridge.async_command(
                "time", timeout=COMMAND_TIMEOUT
            )
        except PylontechConnectionError as err:
            raise ServiceValidationError(str(err)) from err
        line = next(
            (l.strip() for l in readback.splitlines() if l.strip().startswith("Ds3231")),
            readback.strip(),
        )
        return {"set_to": when.isoformat(), "battery_time": line}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_LOG,
        _get_log,
        schema=_GET_LOG_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_TIME,
        _set_time,
        schema=_SET_TIME_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
