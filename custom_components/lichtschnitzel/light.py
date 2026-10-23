"""Lights: the whole lamp (On, Brightness of the FramebufferManager), one per screen with its effect,
and the colour parameters of effects (disabled until switched on in the entity list)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .const import FBM, NO_EFFECT
from .entity import LichtschnitzelEntity, ScreenEntity, new_screens
from .params import EffectParameter, ParameterTracker
from .hub import LichtschnitzelHub


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    if hub.has(f"{FBM}/On"):
        add([LampLight(hub)])
    done: set[int] = set()
    params = ParameterTracker(hub, {"Rgb"})

    @callback
    def discover() -> None:
        add([ScreenLight(hub, i) for i in new_screens(hub, done)])
        add([ColorParameter(hub, p) for p in params.new()])

    discover()
    entry.async_on_unload(hub.add_tree_listener(discover))


class LampLight(LichtschnitzelEntity, LightEntity):
    """The master switch: off pauses all effects, the brightness scales every screen."""

    _attr_name = None  # the device's name
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, hub: LichtschnitzelHub) -> None:
        super().__init__(hub, "lamp")

    @property
    def is_on(self) -> bool:
        return bool(self.hub.value(f"{FBM}/On", 1))

    @property
    def brightness(self) -> int:
        return int(self.hub.value(f"{FBM}/Brightness", 255))

    def _snapshot(self) -> object:
        return (self.available, self.is_on, self.brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            await self.hub.async_set(f"{FBM}/Brightness", int(kwargs[ATTR_BRIGHTNESS]))
        await self.hub.async_set(f"{FBM}/On", 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set(f"{FBM}/On", 0)


class ScreenLight(ScreenEntity, LightEntity):
    """A screen: on/off (off frees the effect instance, its parameters are kept), brightness, effect."""

    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, hub: LichtschnitzelHub, screen: int) -> None:
        super().__init__(hub, screen, "light", None)

    @property
    def _has_levels(self) -> bool:
        return self.hub.has(f"{self.base}/On")

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.BRIGHTNESS if self._has_levels else ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return {self.color_mode}

    @property
    def is_on(self) -> bool:
        if self._has_levels:
            return bool(self.hub.value(f"{self.base}/On", 1))
        return self.hub.effect_of(self.screen) is not None  # older firmware: on = shows an effect

    @property
    def brightness(self) -> int | None:
        return int(self.hub.value(f"{self.base}/Brightness", 255)) if self._has_levels else None

    @property
    def effect_list(self) -> list[str]:
        return [e["name"] for e in self.hub.effects]

    @property
    def effect(self) -> str | None:
        e = self.hub.effect_of(self.screen)
        return e["name"] if e else None

    def _snapshot(self) -> object:
        return (self.available, self.is_on, self.brightness, self.effect, self.name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_EFFECT in kwargs:
            idx = next((e["index"] for e in self.hub.effects if e["name"] == kwargs[ATTR_EFFECT]), None)
            if idx is not None:
                # Like the web UI: choosing an effect gives the screen its own instance again.
                if self.hub.value(f"{self.base}/Source", self.screen) != self.screen:
                    await self.hub.async_set(f"{self.base}/Source", self.screen)
                await self.hub.async_set(f"{self.base}/EffectIndex", idx)
        if not self._has_levels:
            return
        if ATTR_BRIGHTNESS in kwargs:
            await self.hub.async_set(f"{self.base}/Brightness", int(kwargs[ATTR_BRIGHTNESS]))
        if not self.hub.value(f"{self.base}/On", 1):
            await self.hub.async_set(f"{self.base}/On", 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._has_levels:
            await self.hub.async_set(f"{self.base}/On", 0)
        else:
            await self.hub.async_set(f"{self.base}/EffectIndex", NO_EFFECT)


class ColorParameter(EffectParameter, LightEntity):
    """A colour of an effect, e.g. "myColor" of SingleColorEffect. Off is black."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        v = self.hub.value(self.path)
        return tuple(v[:3]) if isinstance(v, list) and len(v) >= 3 else None

    @property
    def is_on(self) -> bool:
        return any(self.rgb_color or ())

    @property
    def brightness(self) -> int:
        return max(self.rgb_color or (0,)) or 0

    def _snapshot(self) -> object:
        return (self.available, self.rgb_color, self.name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        rgb = list(kwargs.get(ATTR_RGB_COLOR) or self.rgb_color or (255, 255, 255))
        if not any(rgb):
            rgb = [255, 255, 255]
        if ATTR_BRIGHTNESS in kwargs:
            top = max(rgb) or 1
            rgb = [round(c * kwargs[ATTR_BRIGHTNESS] / top) for c in rgb]
        await self.hub.async_set(self.path, rgb)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set(self.path, [0, 0, 0])
