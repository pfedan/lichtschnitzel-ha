"""Buttons: next preset and default values per screen; save, restart, overwriting the active scene and
clearing all indicators for the lamp."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .const import DOMAIN
from .entity import LichtschnitzelEntity, ScreenEntity, new_screens
from .hub import LichtschnitzelHub
from .light import send_overlay


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    add([SaveButton(hub), RestartButton(hub), ClearIndicatorsButton(hub), OverwriteSceneButton(hub)])
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


class ClearIndicatorsButton(LichtschnitzelEntity, ButtonEntity):
    """Clears every indicator on every screen of the lamp (POST /api/indicators/clear)."""

    _attr_translation_key = "clear_indicators"
    _attr_icon = "mdi:notification-clear-all"

    def __init__(self, hub: LichtschnitzelHub) -> None:
        super().__init__(hub, "clear-indicators")

    async def async_press(self) -> None:
        await send_overlay(self.hub.async_clear_indicator, None, {})


class OverwriteSceneButton(LichtschnitzelEntity, ButtonEntity):
    """Stores the screens as they are now in the scene that was chosen last (under its name)."""

    _attr_translation_key = "overwrite_scene"
    _attr_icon = "mdi:content-save-edit"

    def __init__(self, hub: LichtschnitzelHub) -> None:
        super().__init__(hub, "overwrite-scene")

    @property
    def available(self) -> bool:
        return super().available and self.hub.active_scene() is not None

    async def async_press(self) -> None:
        try:
            await self.hub.async_overwrite_scene()
        except ValueError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_active_scene") from err
