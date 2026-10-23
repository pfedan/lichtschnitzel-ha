"""Mirroring per screen (Screen <i>/Mirror)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .entity import ScreenEntity, new_screens
from .hub import LichtschnitzelHub


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    done: set[int] = set()

    @callback
    def discover() -> None:
        add([MirrorSwitch(hub, i) for i in new_screens(hub, done)])

    discover()
    entry.async_on_unload(hub.add_tree_listener(discover))


class MirrorSwitch(ScreenEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:swap-horizontal"

    def __init__(self, hub: LichtschnitzelHub, screen: int) -> None:
        super().__init__(hub, screen, "mirror", "mirrored")

    @property
    def is_on(self) -> bool:
        return bool(self.hub.value(f"{self.base}/Mirror", 0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.async_set(f"{self.base}/Mirror", 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set(f"{self.base}/Mirror", 0)
