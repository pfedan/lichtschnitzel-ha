"""Problems of the supply on the Lichtschnitzel board: eFuse tripped, rails not good."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .entity import LichtschnitzelEntity
from .hub import LichtschnitzelHub


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    if isinstance(hub.info.get("supply"), dict):
        add([SupplyProblem(hub, "efuseFault", "efuse", False), SupplyProblem(hub, "railsGood", "rails", True)])


class SupplyProblem(LichtschnitzelEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: LichtschnitzelHub, field: str, key: str, good_when_true: bool) -> None:
        super().__init__(hub, key)
        self._attr_translation_key = key
        self.field = field
        self.good_when_true = good_when_true

    @property
    def is_on(self) -> bool | None:
        v = (self.hub.info.get("supply") or {}).get(self.field)
        return None if v is None else bool(v) != self.good_when_true
