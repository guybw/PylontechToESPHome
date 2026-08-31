"""Config flow for the Pylontech (ESPHome serial bridge) integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac

from .bridge import PylontechBridge, PylontechConnectionError
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
    MIN_SCAN_INTERVAL,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_ENCRYPTION_KEY): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_PROXY_NAME, default=DEFAULT_PROXY_NAME): str,
    }
)


class PylontechConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup: ESP host + API encryption key + serial port name."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            bridge = PylontechBridge(
                host,
                user_input[CONF_PORT],
                user_input[CONF_ENCRYPTION_KEY],
                user_input[CONF_PROXY_NAME],
            )
            device_info = None
            try:
                await bridge.async_start()
                device_info = bridge.esphome_device_info
            except PylontechConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error validating Pylontech bridge")
                errors["base"] = "unknown"
            else:
                mac = getattr(device_info, "mac_address", None)
                if mac:
                    await self.async_set_unique_id(format_mac(mac))
                    self._abort_if_unique_id_configured()
                else:
                    self._async_abort_entries_match({CONF_HOST: host})
                return self.async_create_entry(
                    title=f"Pylontech ({host})", data=user_input
                )
            finally:
                await bridge.async_stop()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> PylontechOptionsFlow:
        return PylontechOptionsFlow()


class PylontechOptionsFlow(OptionsFlow):
    """Adjust the poll interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)
                    ),
                    vol.Optional(
                        CONF_CELL_SENSORS,
                        default=opts.get(CONF_CELL_SENSORS, False),
                    ): bool,
                    vol.Optional(
                        CONF_SYNC_TIME,
                        default=opts.get(CONF_SYNC_TIME, False),
                    ): bool,
                }
            ),
        )
