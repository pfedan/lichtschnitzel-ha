"""Effect parameters as entities: one per screen, effect and parameter, so automations keep
working when the screen shows another effect for a while (the entity is unavailable meanwhile).
They are disabled until switched on in the entity list: trying out many effects on many screens
would otherwise leave hundreds of entities."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import EntityCategory

from .const import NUM_SCREENS
from .entity import ScreenEntity
from .hub import LichtschnitzelHub


@dataclass(frozen=True)
class Param:
    screen: int
    effect: str  # EffectFactory key, e.g. "AuroraEffect"
    rel: str  # path inside the instance, e.g. "Speed" or "Flying Emitters/Period Length"
    kind: str  # "float", "uint32" or "Rgb"


def _walk(node: dict, prefix: str, kinds: set[str], out: list[tuple[str, str]]) -> None:
    for key, p in node.items():
        if key == "name" or not isinstance(p, dict) or p.get("internal"):
            continue
        if p.get("type") == "pobj":
            _walk(p, prefix + key + "/", kinds, out)
        elif p.get("type") in kinds:
            out.append((prefix + key, p["type"]))


class ParameterTracker:
    """Finds parameters of the current instances that have no entity yet."""

    def __init__(self, hub: LichtschnitzelHub, kinds: set[str]) -> None:
        self.hub = hub
        self.kinds = kinds
        self.seen: set[Param] = set()

    def new(self) -> list[Param]:
        out = []
        for i in range(NUM_SCREENS):
            effects = self.hub.node(f"{self.hub.screen_path(i)}/Effects")
            if not isinstance(effects, dict) or not self.hub.screen_used(i):
                continue
            for key, inst in effects.items():
                if not isinstance(inst, dict) or inst.get("type") != "pobj":
                    continue
                found: list[tuple[str, str]] = []
                _walk(inst, "", self.kinds, found)
                for rel, kind in found:
                    p = Param(i, key, rel, kind)
                    if p not in self.seen:
                        self.seen.add(p)
                        out.append(p)
        return out


class EffectParameter(ScreenEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: LichtschnitzelHub, param: Param) -> None:
        super().__init__(hub, param.screen, f"{param.effect}-{param.rel}", None)
        self.param = param
        self.path = f"{self.base}/Effects/{param.effect}/{param.rel}"

    @property
    def name(self) -> str:
        fx = next((e["name"] for e in self.hub.effects if e.get("key") == self.param.effect), self.param.effect)
        rel = self.param.rel.replace("/", " ")
        return f"{self.hub.screen_label(self.screen)} {fx} {rel}"

    @property
    def available(self) -> bool:
        return super().available and self.hub.has(self.path)
