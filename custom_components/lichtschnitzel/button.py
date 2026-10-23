"""Buttons: next preset and default values per screen, save and restart for the lamp."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .entity import LichtschnitzelEntity, ScreenEntity, new_screens
from .hub import LichtschnitzelHub


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    add([SaveButton(hub), RestartButton(hub)])
    done: set[int] = set()

    @callback
    def discover() -> None:
        for i in new_screens(hub, done):
            add([PresetButton(hub, i), ResetButton(hub, i)])

    discover()
    entry.async_on_unload(hub.add_tree_listener(discover))


class PresetButton(ScreenEntity, ButtonEntity):
    _attr_icon = "mdi:palette"

    def __init__(self, hub: LichtschnitzelHub, screen: int) -> None:
        super().__init__(hub, screen, "next-preset", "next preset")

    @property
    def available(self) -> bool:
        return super().available and self.hub.effect_of(self.screen) is not None

    async def async_press(self) -> None:
        await self.hub.client.next_preset(self.screen)
        self.hub.reload_tree_soon(0)  # a preset changes values without "changed" messages


class ResetButton(ScreenEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore"

    def __init__(self, hub: LichtschnitzelHub, screen: int) -> None:
        super().__init__(hub, screen, "reset-effect", "default values")

    @property
    def available(self) -> bool:
        return super().available and self.hub.effect_of(self.screen) is not None

    async def async_press(self) -> None:
        await self.hub.client.reset_effect(self.screen)
        self.hub.reload_tree_soon(0)


class SaveButton(LichtschnitzelEntity, ButtonEntity):
    """Writes the current state to the lamp's flash, so it starts with it (POST /api/save)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "save"
    _attr_icon = "mdi:content-save"

    def __init__(self, hub: LichtschnitzelHub) -> None:
        super().__init__(hub, "save")

    async def async_press(self) -> None:
        await self.hub.client.save()


class RestartButton(LichtschnitzelEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: LichtschnitzelHub) -> None:
        super().__init__(hub, "restart")

    async def async_press(self) -> None:
        await self.hub.client.reboot()
