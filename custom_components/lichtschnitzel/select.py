"""The scene of a lamp: named sets of all screens, switched with one select (POST /api/scenes/activate)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .const import DOMAIN
from .entity import LichtschnitzelEntity
from .hub import LichtschnitzelHub


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    add([SceneSelect(entry.runtime_data)])


class SceneSelect(LichtschnitzelEntity, SelectEntity):
    """Options are the lamp's scenes by name. The current option is the active scene. Once a screen
    changed it is an extra option "<name> (geändert)", so the scene stays visible and choosing its
    plain name loads it again."""

    _attr_translation_key = "scene"
    _attr_icon = "mdi:palette-swatch-variant"

    def __init__(self, hub: LichtschnitzelHub) -> None:
        super().__init__(hub, "scene")

    def _modified_option(self) -> str | None:
        scene = self.hub.active_scene()
        if scene is None or not self.hub.scenes.get("modified"):
            return None
        word = "geändert" if (self.hass.config.language or "").startswith("de") else "changed"
        return f"{scene['name']} ({word})"

    @property
    def options(self) -> list[str]:
        names = self.hub.scene_names()
        extra = self._modified_option()
        return names + [extra] if extra and extra not in names else names

    @property
    def current_option(self) -> str | None:
        scene = self.hub.active_scene()
        if scene is None:
            return None
        return self._modified_option() or scene["name"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        scenes = self.hub.scenes
        scene = self.hub.active_scene()
        return {
            "index": scene["index"] if scene else None,
            "active": scene["name"] if scene else None,
            "modified": bool(scenes.get("modified")),
            "scenes": [{"index": s["index"], "name": s["name"]} for s in scenes.get("scenes", [])],
        }

    def _snapshot(self) -> object:
        return (self.available, self.current_option, tuple(self.options), repr(self.extra_state_attributes))

    async def async_select_option(self, option: str) -> None:
        if option == self._modified_option():
            return  # the screens as they are
        await self.async_activate_scene(name=option)

    async def async_activate_scene(self, index: int | None = None, name: str | None = None) -> None:
        """Service lichtschnitzel.activate_scene: by index or by name."""
        if (index is None) == (name is None):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="scene_index_or_name")
        try:
            await self.hub.async_activate_scene(index, name)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_scene",
                translation_placeholders={"scene": str(index if index is not None else name)},
            ) from err
