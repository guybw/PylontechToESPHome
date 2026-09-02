"""Polling coordinator for the Pylontech serial bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol
from .bridge import PylontechBridge, PylontechConnectionError
from .const import (
    COMMAND_TIMEOUT,
    CONF_CELL_SENSORS,
    DOMAIN,
    LOGGER,
    STAT_REFRESH_INTERVAL,
)


@dataclass
class PylontechData:
    """One poll's worth of parsed data."""

    system: dict[str, Any] = field(default_factory=dict)
    modules: dict[int, dict[str, Any]] = field(default_factory=dict)
    stat: dict[str, Any] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)
    cells: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    cell_summary: dict[int, dict[str, Any]] = field(default_factory=dict)
    last_poll: datetime | None = None


class PylontechDataUpdateCoordinator(DataUpdateCoordinator[PylontechData]):
    """Fetch pwrsys/pwr every interval, stat occasionally, info once."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        bridge: PylontechBridge,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=entry,
        )
        self.bridge = bridge
        self.info: dict[str, Any] = {}
        self.module_info: dict[int, dict[str, Any]] = {}
        self._stat: dict[str, Any] = {}
        self._last_stat = 0.0
        self._empty_polls = 0

    async def _async_setup(self) -> None:
        try:
            await self.bridge.async_start()
        except PylontechConnectionError as err:
            raise ConfigEntryNotReady(str(err)) from err

        # A battery that was fully powered off stays silent on the 115200
        # console until it gets a 1200-baud wake frame. A failed setup rebuilds
        # the coordinator, so the empty-poll wake in _async_update_data never
        # counts up to its threshold during setup retries — probe once here and
        # wake straight away if the console is silent.
        try:
            probe = await self.bridge.async_command("pwr", timeout=COMMAND_TIMEOUT)
        except PylontechConnectionError:
            probe = ""
        if not protocol.parse_pwr(probe):
            LOGGER.debug("battery silent at setup, sending 1200-baud wake frame")
            try:
                await self.bridge.async_wake()
            except PylontechConnectionError as err:
                LOGGER.debug("setup wake failed: %s", err)

        try:
            self.info = protocol.parse_info(
                await self.bridge.async_command("info", timeout=COMMAND_TIMEOUT)
            )
        except PylontechConnectionError as err:
            LOGGER.debug("initial `info` fetch failed: %s", err)

    async def _async_update_data(self) -> PylontechData:
        try:
            return await self._poll()
        except UpdateFailed:
            self._empty_polls += 1
            # A silent battery after a power-off needs a 1200-baud wake frame;
            # try it every other failed cycle (bounded).
            if 2 <= self._empty_polls <= 8 and self._empty_polls % 2 == 0:
                try:
                    await self.bridge.async_wake()
                except PylontechConnectionError as wake_err:
                    LOGGER.debug("wake attempt failed: %s", wake_err)
            raise

    async def _poll(self) -> PylontechData:
        try:
            pwrsys_raw = await self.bridge.async_command("pwrsys", timeout=COMMAND_TIMEOUT)
            pwr_raw = await self.bridge.async_command("pwr", timeout=COMMAND_TIMEOUT)
        except PylontechConnectionError as err:
            raise UpdateFailed(str(err)) from err

        system = protocol.parse_pwrsys(pwrsys_raw)
        modules = protocol.parse_pwr(pwr_raw)
        if system.get("voltage") is None and not modules:
            raise UpdateFailed("no data parsed from pwrsys/pwr")

        self._empty_polls = 0

        # cell-balance health verdict (per module + stack)
        for mod in modules.values():
            mod["health"], mod["health_condition"] = protocol.assess_cell_health(
                mod.get("cell_voltage_delta"),
                mod.get("current"),
                mod.get("soc"),
                mod.get("temperature"),
            )
        system["health"] = protocol.worst_health(
            [m["health"] for m in modules.values()]
        )

        # Per-module identity (`info N`). Models/barcodes are static, so fetch
        # once — and again only if a new module has appeared. Mixed stacks are
        # common (e.g. one US3000C + several US2000B), so `info` (master only)
        # must not be reused for every module.
        if modules and len(self.module_info) < len(modules):
            for num in sorted(modules):
                if num in self.module_info:
                    continue
                try:
                    self.module_info[num] = protocol.parse_info(
                        await self.bridge.async_command(
                            f"info {num}", timeout=COMMAND_TIMEOUT
                        )
                    )
                except PylontechConnectionError as err:
                    LOGGER.debug("`info %s` failed: %s", num, err)

        now = self.hass.loop.time()
        if now - self._last_stat > STAT_REFRESH_INTERVAL.total_seconds():
            try:
                self._stat = protocol.parse_stat(
                    await self.bridge.async_command("stat", timeout=COMMAND_TIMEOUT)
                )
                self._last_stat = now
            except PylontechConnectionError as err:
                LOGGER.debug("`stat` refresh failed: %s", err)

        if not self.info:
            try:
                self.info = protocol.parse_info(
                    await self.bridge.async_command("info", timeout=COMMAND_TIMEOUT)
                )
            except PylontechConnectionError:
                pass

        cells: dict[int, list[dict[str, Any]]] = {}
        cell_summary: dict[int, dict[str, Any]] = {}
        if self.config_entry.options.get(CONF_CELL_SENSORS) and modules:
            for num in sorted(modules):
                try:
                    parsed = protocol.parse_bat(
                        await self.bridge.async_command(
                            f"bat {num}", timeout=COMMAND_TIMEOUT
                        )
                    )
                except PylontechConnectionError as err:
                    LOGGER.debug("`bat %s` failed: %s", num, err)
                    continue
                if parsed:
                    cells[num] = parsed
                    cell_summary[num] = protocol.summarise_cells(parsed)

        return PylontechData(
            system=system,
            modules=modules,
            stat=self._stat,
            info=self.info,
            cells=cells,
            cell_summary=cell_summary,
            last_poll=dt_util.utcnow(),
        )

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.bridge.async_stop()
