"""Effect parameters as entities: one per screen, effect and parameter, so automations keep
working when the screen shows another effect for a while (the entity is unavailable meanwhile).
They are disabled until switched on in the entity list: trying out many effects on many screens
would otherwise leave hundreds of entities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.const import EntityCategory

from .const import NUM_SCREENS
from .entity import ScreenEntity
from .hub import LichtschnitzelHub


@dataclass(frozen=True)
class Param:
    screen: int
    effect: str  # id of the effect, e.g. "aurora"
    param: str  # id of the parameter, e.g. "speed"
    kind: str  # "float", "uint" or "color"


class ParameterTracker:
    """Finds parameters of the effects the screens run that have no entity yet."""

    def __init__(self, hub: LichtschnitzelHub, kinds: set[str]) -> None:
        self.hub = hub
        self.kinds = kinds
        self.seen: set[Param] = set()

    def new(self) -> list[Param]:
        out = []
        for i in range(NUM_SCREENS):
            effect = self.hub.effect(self.hub.screen(i).get("effect"))
            if effect is None or not self.hub.screen_used(i):
                continue
            for p in effect.get("params", []):
                item = Param(i, effect["id"], p["id"], p["type"])
                if p["type"] in self.kinds and item not in self.seen:
                    self.seen.add(item)
                    out.append(item)
        return out


class EffectParameter(ScreenEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: LichtschnitzelHub, param: Param) -> None:
        super().__init__(hub, param.screen, f"{param.effect}-{param.param}", None)
        self.param = param

    def _definition(self) -> dict:
        effect = self.hub.effect(self.param.effect) or {}
        return next((p for p in effect.get("params", []) if p["id"] == self.param.param), {})

    @property
    def name(self) -> str:
        effect = self.hub.effect(self.param.effect) or {}
        p = self._definition()
        words = [self.hub.screen_label(self.screen), effect.get("name", self.param.effect), p.get("group", ""), p.get("name", self.param.param)]
        return " ".join(w for w in words if w)

    @property
    def available(self) -> bool:
        # While the screen's own effect is this one. (A screen that shows another screen's effect
        # keeps its own; its values are kept and can be set, they show once it is on its own again.)
        return super().available and self.hub.screen(self.screen).get("effect") == self.param.effect and bool(self._definition())

    @property
    def lamp_value(self) -> Any:
        """The value on the lamp, None while the screen runs another effect."""
        if self.hub.screen(self.screen).get("effect") != self.param.effect:
            return None
        return (self.hub.screen(self.screen).get("params") or {}).get(self.param.param)

    async def async_send(self, value: Any) -> None:
        await self.hub.async_patch_screen(self.screen, {"params": {self.param.param: value}})
