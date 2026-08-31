"""Sensor platform for the Pylontech integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import PylontechConfigEntry
from .const import CONF_CELL_SENSORS
from .coordinator import PylontechData, PylontechDataUpdateCoordinator
from .entity import PylontechEntity, PylontechModuleEntity, module_numbers
from .protocol import HEALTH_STATES

# Ah has no HA unit constant / device class.
_AMP_HOUR = "Ah"


@dataclass(frozen=True, kw_only=True)
class PylontechSystemSensorDescription(SensorEntityDescription):
    """System sensor with a getter over a full poll."""

    value_fn: Callable[[PylontechData], StateType]
    attributes_fn: Callable[[PylontechData], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class PylontechModuleSensorDescription(SensorEntityDescription):
    """Per-module sensor with a getter over one module's dict."""

    value_fn: Callable[[dict[str, Any]], StateType]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _sys(key: str) -> Callable[[PylontechData], StateType]:
    return lambda data: data.system.get(key)


def _mod(key: str) -> Callable[[dict[str, Any]], StateType]:
    return lambda module: module.get(key)


SYSTEM_SENSORS: tuple[PylontechSystemSensorDescription, ...] = (
    PylontechSystemSensorDescription(
        key="soc",
        translation_key="soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_sys("soc"),
    ),
    PylontechSystemSensorDescription(
        key="voltage",
        translation_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_sys("voltage"),
    ),
    PylontechSystemSensorDescription(
        key="current",
        translation_key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_sys("current"),
    ),
    PylontechSystemSensorDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_sys("power"),
    ),
    PylontechSystemSensorDescription(
        key="soh",
        translation_key="soh",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sys("soh"),
    ),
    PylontechSystemSensorDescription(
        key="remaining_capacity",
        translation_key="remaining_capacity",
        native_unit_of_measurement=_AMP_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_sys("remaining_capacity"),
    ),
    PylontechSystemSensorDescription(
        key="full_charge_capacity",
        translation_key="full_charge_capacity",
        native_unit_of_measurement=_AMP_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sys("full_charge_capacity"),
    ),
    PylontechSystemSensorDescription(
        key="cell_voltage_min",
        translation_key="cell_voltage_min",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=_sys("cell_voltage_min"),
    ),
    PylontechSystemSensorDescription(
        key="cell_voltage_max",
        translation_key="cell_voltage_max",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=_sys("cell_voltage_max"),
    ),
    PylontechSystemSensorDescription(
        key="cell_voltage_avg",
        translation_key="cell_voltage_avg",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sys("cell_voltage_avg"),
    ),
    PylontechSystemSensorDescription(
        key="cell_voltage_delta",
        translation_key="cell_voltage_delta",
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-heart-variant",
        value_fn=_sys("cell_voltage_delta"),
    ),
    PylontechSystemSensorDescription(
        key="temperature_min",
        translation_key="temperature_min",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_sys("temperature_min"),
    ),
    PylontechSystemSensorDescription(
        key="temperature_max",
        translation_key="temperature_max",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_sys("temperature_max"),
    ),
    PylontechSystemSensorDescription(
        key="temperature_avg",
        translation_key="temperature_avg",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sys("temperature_avg"),
    ),
    PylontechSystemSensorDescription(
        key="modules_present",
        translation_key="modules_present",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sys("modules_present"),
    ),
    PylontechSystemSensorDescription(
        key="cycle_count",
        translation_key="cycle_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.stat.get("cycle_count"),
    ),
    PylontechSystemSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_poll,
    ),
    PylontechSystemSensorDescription(
        key="health",
        translation_key="health",
        device_class=SensorDeviceClass.ENUM,
        options=HEALTH_STATES,
        icon="mdi:heart-pulse",
        value_fn=lambda data: data.system.get("health"),
    ),
)

MODULE_SENSORS: tuple[PylontechModuleSensorDescription, ...] = (
    PylontechModuleSensorDescription(
        key="soc",
        translation_key="soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_mod("soc"),
    ),
    PylontechModuleSensorDescription(
        key="voltage",
        translation_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_mod("voltage"),
    ),
    PylontechModuleSensorDescription(
        key="current",
        translation_key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_mod("current"),
    ),
    PylontechModuleSensorDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_mod("power"),
    ),
    PylontechModuleSensorDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_mod("temperature"),
    ),
    PylontechModuleSensorDescription(
        key="cell_voltage_min",
        translation_key="cell_voltage_min",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=_mod("cell_voltage_min"),
    ),
    PylontechModuleSensorDescription(
        key="cell_voltage_max",
        translation_key="cell_voltage_max",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=_mod("cell_voltage_max"),
    ),
    PylontechModuleSensorDescription(
        key="cell_voltage_delta",
        translation_key="cell_voltage_delta",
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-heart-variant",
        value_fn=_mod("cell_voltage_delta"),
    ),
    PylontechModuleSensorDescription(
        key="cell_temp_min",
        translation_key="cell_temp_min",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mod("cell_temp_min"),
    ),
    PylontechModuleSensorDescription(
        key="cell_temp_max",
        translation_key="cell_temp_max",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mod("cell_temp_max"),
    ),
    PylontechModuleSensorDescription(
        key="mos_temperature",
        translation_key="mos_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mod("mos_temperature"),
    ),
    PylontechModuleSensorDescription(
        key="state",
        translation_key="module_state",
        value_fn=_mod("base_state"),
    ),
    PylontechModuleSensorDescription(
        key="health",
        translation_key="health",
        device_class=SensorDeviceClass.ENUM,
        options=HEALTH_STATES,
        icon="mdi:heart-pulse",
        value_fn=_mod("health"),
        attributes_fn=lambda m: {
            "cell_voltage_delta_mv": m.get("cell_voltage_delta"),
            "condition": m.get("health_condition"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PylontechConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Pylontech sensors."""
    coordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        PylontechSystemSensor(coordinator, desc) for desc in SYSTEM_SENSORS
    ]
    for module in module_numbers(coordinator):
        entities.extend(
            PylontechModuleSensor(coordinator, module, desc) for desc in MODULE_SENSORS
        )

    if coordinator.config_entry.options.get(CONF_CELL_SENSORS):
        for module in module_numbers(coordinator):
            entities.append(PylontechWeakestCellSensor(coordinator, module))
            for cell in range(_cell_count(coordinator, module)):
                entities.append(PylontechCellVoltageSensor(coordinator, module, cell))

    async_add_entities(entities)


def _cell_count(coordinator: PylontechDataUpdateCoordinator, module: int) -> int:
    from_poll = len(coordinator.data.cells.get(module, []))
    if from_poll:
        return from_poll
    return coordinator.module_info.get(module, {}).get("cell_count") or 15


class PylontechSystemSensor(PylontechEntity, SensorEntity):
    """A stack-level sensor."""

    entity_description: PylontechSystemSensorDescription

    def __init__(
        self,
        coordinator: PylontechDataUpdateCoordinator,
        description: PylontechSystemSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


class PylontechModuleSensor(PylontechModuleEntity, SensorEntity):
    """A per-module sensor."""

    entity_description: PylontechModuleSensorDescription

    def __init__(
        self,
        coordinator: PylontechDataUpdateCoordinator,
        module: int,
        description: PylontechModuleSensorDescription,
    ) -> None:
        super().__init__(coordinator, module)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_module_{module}_{description.key}"
        )

    @property
    def native_value(self) -> StateType:
        data = self.module_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.module_data
        if data is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(data)


class PylontechCellVoltageSensor(PylontechModuleEntity, SensorEntity):
    """Voltage of one cell within a module (opt-in via `bat N` polling)."""

    _attr_translation_key = "cell_voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: PylontechDataUpdateCoordinator, module: int, cell: int
    ) -> None:
        super().__init__(coordinator, module)
        self.cell = cell
        self._attr_translation_placeholders = {"cell": str(cell + 1)}
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_module_{module}_cell_{cell}_voltage"
        )

    def _cell(self) -> dict[str, Any] | None:
        for entry in self.coordinator.data.cells.get(self.module, ()):
            if entry.get("index") == self.cell:
                return entry
        return None

    @property
    def native_value(self) -> StateType:
        cell = self._cell()
        return None if cell is None else cell.get("voltage")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cell = self._cell()
        return None if cell is None else {"balancing": cell.get("balancing")}

    @property
    def available(self) -> bool:
        return super().available and self._cell() is not None


class PylontechWeakestCellSensor(PylontechModuleEntity, SensorEntity):
    """Which cell in a module is lowest, with spread / balancing attributes."""

    _attr_translation_key = "weakest_cell"
    _attr_icon = "mdi:battery-alert-variant-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: PylontechDataUpdateCoordinator, module: int
    ) -> None:
        super().__init__(coordinator, module)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_module_{module}_weakest_cell"
        )

    def _summary(self) -> dict[str, Any]:
        return self.coordinator.data.cell_summary.get(self.module, {})

    @property
    def native_value(self) -> StateType:
        weakest = self._summary().get("weakest_cell")
        return None if weakest is None else weakest + 1

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        s = self._summary()
        if not s:
            return None
        return {
            "voltage": s.get("weakest_cell_voltage"),
            "delta_from_max_mv": s.get("weakest_cell_delta_mv"),
            "spread_mv": s.get("spread_mv"),
            "strongest_cell": (s["strongest_cell"] + 1) if "strongest_cell" in s else None,
            "balancing_cells": [c + 1 for c in s.get("balancing_cells", [])],
        }

    @property
    def available(self) -> bool:
        return super().available and bool(self._summary())
