"""Numeric effect parameters (Speed, Palette, ...) with the limits from the lamp's tree."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .params import EffectParameter, ParameterTracker


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    params = ParameterTracker(hub, {"float", "uint32"})

    @callback
    def discover() -> None:
        add([ParameterNumber(hub, p) for p in params.new()])

    discover()
    entry.async_on_unload(hub.add_tree_listener(discover))


class ParameterNumber(EffectParameter, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def _node(self) -> dict:
        n = self.hub.node(self.path)
        return n if isinstance(n, dict) else {}

    @property
    def native_value(self) -> float | None:
        return self._node().get("val")

    @property
    def native_min_value(self) -> float:
        return float(self._node().get("min", 0))

    @property
    def native_max_value(self) -> float:
        return float(self._node().get("max", 255))

    @property
    def native_step(self) -> float:
        if self.param.kind == "uint32":
            return 1
        span = self.native_max_value - self.native_min_value
        return span / 100 if span > 0 else 0.01

    def _snapshot(self) -> object:
        return (self.available, self.native_value, self.name)

    async def async_set_native_value(self, value: float) -> None:
        await self.hub.async_set(self.path, int(round(value)) if self.param.kind == "uint32" else float(value))
