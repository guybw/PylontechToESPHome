"""Polling coordinator for the Pylontech serial bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store
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

STORE_VERSION = 1
STORE_SAVE_DELAY = 120
# energy is not integrated across a gap longer than this (restart, lost link)
MIN_ENERGY_GAP = 300


@dataclass
class PylontechData:
    """One poll's worth of parsed data."""

    system: dict[str, Any] = field(default_factory=dict)
    modules: dict[int, dict[str, Any]] = field(default_factory=dict)
    stat: dict[str, Any] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)
    cells: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    cell_summary: dict[int, dict[str, Any]] = field(default_factory=dict)
    balance: dict[str, Any] = field(default_factory=dict)
    energy: dict[str, float] = field(default_factory=dict)
    tracking: dict[str, Any] = field(default_factory=dict)
    hv: bool = False
    last_poll: datetime | None = None


class PylontechDataUpdateCoordinator(DataUpdateCoordinator[PylontechData]):
    """Fetch pwrsys/pwr every interval, stat occasionally, info once.

    Also keeps the state that has to outlive a restart: the energy counters,
    the full-charge tracker and a running force-balance session.
    """

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
        self._module_cycles: dict[int, int] = {}
        self._last_stat = 0.0
        self._empty_polls = 0
        # force-balance session; mutated in place so PylontechData sees changes
        self.balance: dict[str, Any] = {"state": protocol.BALANCE_IDLE}
        # True once a `pwr` reply shows the high-voltage layout (experimental)
        self.hv = False
        # lifetime energy in Wh and the full-charge tracker; both persisted
        self.energy: dict[str, float] = {"charged": 0.0, "discharged": 0.0}
        self.tracker: dict[str, Any] = {}
        self._prev_power: float | None = None
        self._prev_time = 0.0
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._store_loaded = False

    def _store_data(self) -> dict[str, Any]:
        return {
            "energy": self.energy,
            "tracker": self.tracker,
            "balance": protocol.balance_dump(self.balance),
        }

    def _save_soon(self) -> None:
        if self._store_loaded:
            self._store.async_delay_save(self._store_data, STORE_SAVE_DELAY)

    def balance_start(self) -> None:
        """Start (or restart) a force-balance session."""
        self.balance.clear()
        self.balance.update(state=protocol.BALANCE_CHARGING, started=dt_util.utcnow())
        self._save_soon()
        self.async_update_listeners()

    def balance_stop(self) -> None:
        """Abandon the force-balance session."""
        self.balance.clear()
        self.balance["state"] = protocol.BALANCE_IDLE
        self._save_soon()
        self.async_update_listeners()

    async def _balance_tick(
        self, system: dict[str, Any], modules: dict[int, dict[str, Any]]
    ) -> None:
        if self.balance["state"] not in (
            protocol.BALANCE_CHARGING,
            protocol.BALANCE_HOLDING,
        ):
            return
        protocol.balance_step(
            self.balance,
            dt_util.utcnow(),
            system.get("soc"),
            {
                num: mod["cell_voltage_delta"]
                for num, mod in modules.items()
                if mod.get("cell_voltage_delta") is not None
            },
        )
        # Session just ended short of target: name the cell furthest from average.
        num = self.balance.get("module")
        if num is None or "finished" not in self.balance:
            return
        self.balance["serial_number"] = self.module_info.get(num, {}).get("barcode")
        if self.hv:
            return  # `bat N` is not a per-module command on a high-voltage stack
        try:
            cells = protocol.parse_bat(
                await self.bridge.async_command(f"bat {num}", timeout=COMMAND_TIMEOUT)
            )
        except PylontechConnectionError as err:
            LOGGER.debug("`bat %s` failed: %s", num, err)
            return
        if not cells:
            return
        avg = sum(c["voltage"] for c in cells) / len(cells)
        cell = max(cells, key=lambda c: abs(c["voltage"] - avg))
        self.balance.update(
            cell=cell["index"] + 1,
            cell_voltage=cell["voltage"],
            cell_from_average_mv=round((cell["voltage"] - avg) * 1000),
        )

    async def _async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self.energy.update(stored.get("energy") or {})
        self.tracker = stored.get("tracker") or {}
        self.balance.clear()
        self.balance.update(protocol.balance_load(stored.get("balance")))
        self._store_loaded = True

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
        if not protocol.parse_pwr(probe) and not protocol.is_hv_pwr(probe):
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
            pwr_raw = await self.bridge.async_command("pwr", timeout=COMMAND_TIMEOUT)
            self.hv = protocol.is_hv_pwr(pwr_raw)
            if self.hv:
                # High-voltage stack: one stack row in `pwr`, modules in `unit`.
                system = protocol.parse_pwr_hv(pwr_raw)
                modules = protocol.parse_unit_hv(
                    await self.bridge.async_command("unit", timeout=COMMAND_TIMEOUT)
                )
                system["modules_present"] = len(modules)
            else:
                system = protocol.parse_pwrsys(
                    await self.bridge.async_command("pwrsys", timeout=COMMAND_TIMEOUT)
                )
                modules = protocol.parse_pwr(pwr_raw)
        except PylontechConnectionError as err:
            raise UpdateFailed(str(err)) from err

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
        if self.hv:
            await self._hv_identity(modules)
        elif modules and len(self.module_info) < len(modules):
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
        if not self.hv and now - self._last_stat > STAT_REFRESH_INTERVAL.total_seconds():
            try:
                self._stat = protocol.parse_stat(
                    await self.bridge.async_command("stat", timeout=COMMAND_TIMEOUT)
                )
                self._last_stat = now
                # Plain `stat` is the master's only; each module counts its own cycles.
                for num in sorted(modules):
                    mod_stat = protocol.parse_stat(
                        await self.bridge.async_command(
                            f"stat {num}", timeout=COMMAND_TIMEOUT
                        )
                    )
                    if "cycle_count" in mod_stat:
                        self._module_cycles[num] = mod_stat["cycle_count"]
            except PylontechConnectionError as err:
                LOGGER.debug("`stat` refresh failed: %s", err)

        for num, mod in modules.items():
            mod["cycle_count"] = self._module_cycles.get(num)

        if not self.info and not self.hv:
            try:
                self.info = protocol.parse_info(
                    await self.bridge.async_command("info", timeout=COMMAND_TIMEOUT)
                )
            except PylontechConnectionError:
                pass

        cells: dict[int, list[dict[str, Any]]] = {}
        cell_summary: dict[int, dict[str, Any]] = {}
        want_cells = self.config_entry.options.get(CONF_CELL_SENSORS) and modules
        if want_cells and self.hv:
            # one `bat` lists every cell of the stack
            try:
                cells = protocol.parse_bat_hv(
                    await self.bridge.async_command("bat", timeout=COMMAND_TIMEOUT),
                    len(modules),
                )
            except PylontechConnectionError as err:
                LOGGER.debug("`bat` failed: %s", err)
            cell_summary = {
                num: protocol.summarise_cells(parsed) for num, parsed in cells.items()
            }
        elif want_cells:
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

        await self._balance_tick(system, modules)

        # stored energy, time to full / empty
        remaining = system.get("remaining_capacity")
        voltage = system.get("voltage")
        system["stored_energy"] = (
            None
            if remaining is None or voltage is None
            else round(remaining * voltage / 1000, 2)
        )
        system["time_to_full"], system["time_to_empty"] = protocol.time_remaining(
            remaining, system.get("full_charge_capacity"), system.get("current")
        )

        # energy in / out
        power = system.get("power")
        protocol.integrate_energy(
            self.energy,
            self._prev_power,
            power,
            now - self._prev_time,
            max(3 * self.update_interval.total_seconds(), MIN_ENERGY_GAP),
        )
        self._prev_power, self._prev_time = power, now

        # every full charge is recorded, with or without a force-balance session
        now_ts = dt_util.utcnow().timestamp()
        protocol.track_full_charge(
            self.tracker,
            now_ts,
            system.get("soc"),
            {
                num: mod["cell_voltage_delta"]
                for num, mod in modules.items()
                if mod.get("cell_voltage_delta") is not None
            },
        )
        self._save_soon()

        return PylontechData(
            system=system,
            modules=modules,
            stat=self._stat,
            info=self.info,
            cells=cells,
            cell_summary=cell_summary,
            balance=self.balance,
            energy=self.energy,
            tracking=protocol.balance_summary(self.tracker, now_ts),
            hv=self.hv,
            last_poll=dt_util.utcnow(),
        )

    async def _hv_identity(self, modules: dict[int, dict[str, Any]]) -> None:
        """Management-unit identity and module barcodes for a high-voltage stack."""
        if self.info or not modules:
            return
        try:
            raw = await self.bridge.async_command("info", timeout=COMMAND_TIMEOUT)
        except PylontechConnectionError as err:
            LOGGER.debug("`info` failed: %s", err)
            return
        self.info = protocol.parse_info(raw)
        # `info` counts BMUs from the top of the tower, `unit` from the bottom
        barcodes = protocol.parse_info_bmus(raw)
        for num in modules:
            self.module_info[num] = {"barcode": barcodes.get(len(modules) - num)}

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self._store_loaded:
            await self._store.async_save(self._store_data())
        await self.bridge.async_stop()
