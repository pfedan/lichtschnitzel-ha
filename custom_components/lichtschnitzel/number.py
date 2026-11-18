"""Numeric effect parameters (Speed, Palette, ...) with the limits the lamp gives for them."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .params import EffectParameter, ParameterTracker


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    params = ParameterTracker(hub, {"float", "uint"})

    @callback
    def discover() -> None:
        add([ParameterNumber(hub, p) for p in params.new()])

    discover()
    entry.async_on_unload(hub.add_screen_listener(discover))


class ParameterNumber(EffectParameter, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float | None:
        v = self.lamp_value
        if isinstance(v, float):  # the lamp's floats are 32 bit: 0.1 may arrive as 0.10000000149
            v = float(f"{v:.7g}")
        return v

    @property
    def native_min_value(self) -> float:
        return float(self._definition().get("min", 0))

    @property
    def native_max_value(self) -> float:
        return float(self._definition().get("max", 255))

    @property
    def native_step(self) -> float:
        if self.param.kind == "uint":
            return 1
        span = self.native_max_value - self.native_min_value
        return span / 100 if span > 0 else 0.01

    def _snapshot(self) -> object:
        return (self.available, self.native_value, self.name)

    async def async_set_native_value(self, value: float) -> None:
        await self.async_send(int(round(value)) if self.param.kind == "uint" else float(value))
