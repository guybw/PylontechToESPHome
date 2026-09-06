"""Config flow for the Pylontech (ESPHome serial bridge) integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from yarl import URL

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

# ESPHome exposes each serial_proxy port to the `usb` integration as
# `esphome-hass://esphome/<esphome_entry_id>?port_name=<name>` (HA >= 2026.9).
ESPHOME_DOMAIN = "esphome"
ESPHOME_URL_SCHEME = "esphome-hass"
ESPHOME_NOISE_PSK = "noise_psk"
MANUAL_CHOICE = "__manual__"

STEP_MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_ENCRYPTION_KEY): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_PROXY_NAME, default=DEFAULT_PROXY_NAME): str,
    }
)


class PylontechConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup: pick a discovered ESPHome serial port, or enter it by hand."""

    VERSION = 1

    def __init__(self) -> None:
        self._ports: dict[str, str] | None = None
        self._usb_url: str | None = None
        self._usb_label: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the discovered ESPHome serial ports, or fall back to manual entry."""
        if self._ports is None:
            self._ports = await self._scan_esphome_ports()
        if not self._ports:
            return await self.async_step_manual()

        errors: dict[str, str] = {}
        if user_input is not None:
            choice = user_input[CONF_DEVICE]
            if choice == MANUAL_CHOICE:
                return await self.async_step_manual()
            resolved = self._resolve_esphome(choice)
            if resolved is None:
                errors["base"] = "cannot_connect"
            else:
                result, error = await self._validate_and_create(
                    *resolved, device_url=choice
                )
                if result is not None:
                    return result
                errors["base"] = error

        options = {**self._ports, MANUAL_CHOICE: "Enter connection details manually..."}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(options)}),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the ESP host, API encryption key and serial port name by hand."""
        errors: dict[str, str] = {}

        if user_input is not None:
            result, error = await self._validate_and_create(
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_ENCRYPTION_KEY],
                user_input[CONF_PROXY_NAME],
                device_url=None,
            )
            if result is not None:
                return result
            errors["base"] = error

        return self.async_show_form(
            step_id="manual",
            data_schema=STEP_MANUAL_SCHEMA,
            errors=errors,
        )

    async def async_step_usb(
        self, discovery_info: UsbServiceInfo
    ) -> ConfigFlowResult:
        """Handle a serial port handed over by the `usb` integration."""
        url = discovery_info.device
        if not url.startswith(f"{ESPHOME_URL_SCHEME}://"):
            return self.async_abort(reason="not_esphome_serial")
        self._usb_url = url
        self._usb_label = discovery_info.description or discovery_info.serial_number or url
        self.context["title_placeholders"] = {"name": self._usb_label}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup for a serial port that came in via `usb` discovery."""
        assert self._usb_url is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            resolved = self._resolve_esphome(self._usb_url)
            if resolved is None:
                errors["base"] = "cannot_connect"
            else:
                result, error = await self._validate_and_create(
                    *resolved, device_url=self._usb_url
                )
                if result is not None:
                    return result
                errors["base"] = error

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._usb_label or ""},
            errors=errors,
        )

    # --- helpers ------------------------------------------------------

    async def _scan_esphome_ports(self) -> dict[str, str]:
        """Return ``{esphome-hass URL: label}`` for unclaimed ESPHome serial ports."""
        try:
            from homeassistant.components.usb import async_scan_serial_ports
        except ImportError:  # HA < 2026.9
            return {}
        try:
            ports = await async_scan_serial_ports(self.hass)
        except Exception:  # noqa: BLE001 - `usb` not set up, scan failed, etc.
            LOGGER.debug("serial port scan unavailable", exc_info=True)
            return {}

        used = {
            entry.data.get(CONF_DEVICE)
            for entry in self._async_current_entries(include_ignore=False)
        }
        out: dict[str, str] = {}
        for port in ports:
            url = port.device
            if not url.startswith(f"{ESPHOME_URL_SCHEME}://") or url in used:
                continue
            label = port.description or port.serial_number or url
            esp = self.hass.config_entries.async_get_entry(
                URL(url).path.lstrip("/")
            )
            if esp is not None:
                label = f"{esp.title}: {label}"
            out[url] = label
        return out

    def _resolve_esphome(
        self, url: str
    ) -> tuple[str, int, str | None, str] | None:
        """Turn an ``esphome-hass://`` URL into (host, api port, noise PSK, port name)."""
        parsed = URL(url)
        entry_id = parsed.path.lstrip("/")
        port_name = parsed.query.get("port_name")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != ESPHOME_DOMAIN or not port_name:
            return None
        return (
            entry.data[CONF_HOST],
            entry.data.get(CONF_PORT, DEFAULT_PORT),
            entry.data.get(ESPHOME_NOISE_PSK),
            port_name,
        )

    async def _validate_and_create(
        self,
        host: str,
        api_port: int,
        encryption_key: str | None,
        proxy_name: str,
        *,
        device_url: str | None,
    ) -> tuple[ConfigFlowResult | None, str]:
        """Open the bridge once to check it, then create the entry.

        Returns ``(entry_result, "")`` on success or ``(None, error_key)``.
        Raises ``AbortFlow`` if the ESP is already configured.
        """
        bridge = PylontechBridge(host, api_port, encryption_key or "", proxy_name)
        device_info = None
        try:
            await bridge.async_start()
            device_info = bridge.esphome_device_info
        except PylontechConnectionError:
            return None, "cannot_connect"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error validating Pylontech bridge")
            return None, "unknown"
        finally:
            await bridge.async_stop()

        mac = getattr(device_info, "mac_address", None)
        if mac:
            await self.async_set_unique_id(format_mac(mac))
            self._abort_if_unique_id_configured()
        else:
            self._async_abort_entries_match({CONF_HOST: host})

        data: dict[str, Any] = {
            CONF_HOST: host,
            CONF_PORT: api_port,
            CONF_ENCRYPTION_KEY: encryption_key or "",
            CONF_PROXY_NAME: proxy_name,
        }
        if device_url is not None:
            data[CONF_DEVICE] = device_url
        return (
            self.async_create_entry(title=f"Pylontech ({host})", data=data),
            "",
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
