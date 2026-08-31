"""Binary sensor platform for the Pylontech integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PylontechConfigEntry
from .const import CONF_CELL_SENSORS
from .coordinator import PylontechDataUpdateCoordinator
from .entity import PylontechEntity, PylontechModuleEntity, module_numbers


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PylontechConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Pylontech binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        PylontechSystemCharging(coordinator),
        PylontechSystemProblem(coordinator),
    ]
    entities.extend(
        PylontechModuleProblem(coordinator, module)
        for module in module_numbers(coordinator)
    )
    if coordinator.config_entry.options.get(CONF_CELL_SENSORS):
        entities.extend(
            PylontechModuleBalancing(coordinator, module)
            for module in module_numbers(coordinator)
        )
    async_add_entities(entities)


class PylontechSystemCharging(PylontechEntity, BinarySensorEntity):
    """On when the stack current is positive (charging)."""

    _attr_translation_key = "charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator: PylontechDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_charging"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.system.get("charging")


class PylontechSystemProblem(PylontechEntity, BinarySensorEntity):
    """On when any module reports a non-normal state."""

    _attr_translation_key = "problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: PylontechDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_problem"

    @property
    def is_on(self) -> bool | None:
        modules = self.coordinator.data.modules
        if not modules:
            return None
        return any(m.get("problem") for m in modules.values())


class PylontechModuleProblem(PylontechModuleEntity, BinarySensorEntity):
    """On when this module reports a non-normal state."""

    _attr_translation_key = "problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: PylontechDataUpdateCoordinator, module: int
    ) -> None:
        super().__init__(coordinator, module)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_module_{module}_problem"
        )

    @property
    def is_on(self) -> bool | None:
        data = self.module_data
        return None if data is None else data.get("problem")


class PylontechModuleBalancing(PylontechModuleEntity, BinarySensorEntity):
    """On when any cell in this module is balancing (opt-in cell polling)."""

    _attr_translation_key = "balancing"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self, coordinator: PylontechDataUpdateCoordinator, module: int
    ) -> None:
        super().__init__(coordinator, module)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_module_{module}_balancing"
        )

    @property
    def is_on(self) -> bool | None:
        summary = self.coordinator.data.cell_summary.get(self.module)
        if summary is None:
            return None
        return bool(summary.get("balancing_cells"))
