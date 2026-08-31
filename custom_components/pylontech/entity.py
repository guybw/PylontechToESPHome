"""Base entities for the Pylontech integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PylontechDataUpdateCoordinator


def module_numbers(coordinator: PylontechDataUpdateCoordinator) -> list[int]:
    """Module numbers to build entities for: 1..total, else whatever `pwr` saw."""
    data = coordinator.data
    total = data.system.get("modules_total") or data.system.get("modules_present")
    if isinstance(total, int) and total > 0:
        return list(range(1, total + 1))
    return sorted(data.modules)


class PylontechEntity(CoordinatorEntity[PylontechDataUpdateCoordinator]):
    """Base entity: bound to the stack ("system") device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PylontechDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        count = coordinator.data.system.get("modules_present") if coordinator.data else None
        # The stack is an aggregate, not one physical unit — don't label it with
        # the master module's model/firmware (misleading on a mixed stack).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Pylontech",
            manufacturer="Pylontech",
            model=f"Battery stack ({count} modules)" if count else "Battery stack",
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.bridge.available


class PylontechModuleEntity(PylontechEntity):
    """Base entity for one battery module, linked under the system device."""

    def __init__(
        self, coordinator: PylontechDataUpdateCoordinator, module: int
    ) -> None:
        super().__init__(coordinator)
        self.module = module
        entry_id = coordinator.config_entry.entry_id
        # `info` only describes the master module, so it must NOT be applied to
        # every module (mixed stacks exist). Use per-module info if we have it.
        mod_info = coordinator.module_info.get(module, {})
        model = mod_info.get("model")
        if model and mod_info.get("specification"):
            model = f"{model} ({mod_info['specification']})"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_module_{module}")},
            name=f"Battery {module}",
            manufacturer="Pylontech",
            model=model,
            sw_version=mod_info.get("main_soft_version"),
            hw_version=mod_info.get("board_version"),
            serial_number=mod_info.get("barcode"),
            via_device=(DOMAIN, entry_id),
        )

    @property
    def module_data(self) -> dict | None:
        return self.coordinator.data.modules.get(self.module)

    @property
    def available(self) -> bool:
        return super().available and self.module_data is not None
