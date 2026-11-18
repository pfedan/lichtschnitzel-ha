"""Lights: the whole lamp (its own switch and brightness), one per screen with its effect, and the
colour parameters of effects (disabled until switched on in the entity list)."""
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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LichtschnitzelConfigEntry
from .api import LichtschnitzelError
from .const import DOMAIN
from .entity import LichtschnitzelEntity, ScreenEntity, new_screens
from .params import EffectParameter, ParameterTracker
from .hub import LichtschnitzelHub


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    add([LampLight(hub)])
    done: set[int] = set()
    params = ParameterTracker(hub, {"color"})

    @callback
    def discover() -> None:
        add([ScreenLight(hub, i) for i in new_screens(hub, done)])
        add([ColorParameter(hub, p) for p in params.new()])

    discover()
    entry.async_on_unload(hub.add_screen_listener(discover))


class NoParameters:
    async def async_set_parameter(self, parameter: str, value: Any) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_screen", translation_placeholders={"entity": self.entity_id}
        )

    async def async_notify(self, **kwargs: Any) -> None:
        self._not_a_screen()

    async def async_set_indicator(self, **kwargs: Any) -> None:
        self._not_a_screen()

    async def async_clear_indicator(self, **kwargs: Any) -> None:
        self._not_a_screen()

    def _not_a_screen(self) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_screen", translation_placeholders={"entity": self.entity_id}
        )


def look_body(kwargs: dict[str, Any], pattern: str, duration: float) -> dict[str, Any]:
    """The fields shared by notify and set_indicator, as the firmware takes them."""
    body: dict[str, Any] = {
        "pattern": kwargs.get("pattern", pattern),
        "ms": round(kwargs.get("duration", duration) * 1000),
    }
    if "rgb_color" in kwargs:
        body["color"] = [int(c) for c in kwargs["rgb_color"]]
    if "brightness_pct" in kwargs:
        body["brightness"] = round(kwargs["brightness_pct"] * 255 / 100)
    if "period" in kwargs:
        body["period"] = round(kwargs["period"] * 1000)
    if "start" in kwargs:
        body["start"] = int(kwargs["start"])
    if "length" in kwargs:
        body["length"] = int(kwargs["length"])
    return body


def notify_body(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The body of POST /api/notify from the fields of lichtschnitzel.notify."""
    return look_body(kwargs, "flash", 3)


def indicator_body(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The body of POST /api/indicators from the fields of lichtschnitzel.set_indicator."""
    body = look_body(kwargs, "solid", 0)
    body["id"] = kwargs["id"]
    if "progress" in kwargs:
        body["progress"] = kwargs["progress"]
    if "priority" in kwargs:
        body["priority"] = int(kwargs["priority"])
    return body


async def send_overlay(call: Any, screen: int | None, body: dict[str, Any]) -> None:
    """Runs a hub call for notifications or indicators; firmware without them gets a clear error."""
    try:
        await call(screen, body)
    except LichtschnitzelError as err:
        if err.status != 404:
            raise
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="notify_unsupported") from err


class OverlayActions:
    """lichtschnitzel.notify, set_indicator and clear_indicator for one screen (overlay_screen) or,
    with None, the whole lamp."""

    overlay_screen: int | None = None
    hub: LichtschnitzelHub

    async def async_notify(self, **kwargs: Any) -> None:
        await send_overlay(self.hub.async_notify, self.overlay_screen, notify_body(kwargs))

    async def async_set_indicator(self, **kwargs: Any) -> None:
        await send_overlay(self.hub.async_set_indicator, self.overlay_screen, indicator_body(kwargs))

    async def async_clear_indicator(self, **kwargs: Any) -> None:
        body = {"id": kwargs["id"]} if kwargs.get("id") else {}
        await send_overlay(self.hub.async_clear_indicator, self.overlay_screen, body)


class LampLight(OverlayActions, NoParameters, LichtschnitzelEntity, LightEntity):
    """The master switch: off pauses all effects, the brightness scales every screen."""

    _attr_name = None  # the device's name
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, hub: LichtschnitzelHub) -> None:
        super().__init__(hub, "lamp")


    @property
    def is_on(self) -> bool:
        return bool(self.hub.lamp.get("on", True))

    @property
    def brightness(self) -> int:
        return int(self.hub.lamp.get("brightness", 255))

    def _snapshot(self) -> object:
        return (self.available, self.is_on, self.brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:
        patch: dict[str, Any] = {"on": True}
        if ATTR_BRIGHTNESS in kwargs:
            patch["brightness"] = int(kwargs[ATTR_BRIGHTNESS])
        await self.hub.async_patch_lamp(patch)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_patch_lamp({"on": False})


class ScreenLight(OverlayActions, ScreenEntity, LightEntity):
    """A screen: on/off (off frees the effect instance, its parameters are kept), brightness, effect.
    The parameters of the effect it shows are an attribute, for the Lichtschnitzel card; the
    service lichtschnitzel.set_parameter sets them."""

    _attr_supported_features = LightEntityFeature.EFFECT
    _unrecorded_attributes = frozenset({"parameters", "screen"})

    def __init__(self, hub: LichtschnitzelHub, screen: int) -> None:
        super().__init__(hub, screen, "light", None)
        self.overlay_screen = screen

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool:
        return bool(self.hub.screen(self.screen).get("on", True))

    @property
    def brightness(self) -> int:
        return int(self.hub.screen(self.screen).get("brightness", 255))

    @property
    def effect_list(self) -> list[str]:
        return [e["name"] for e in self.hub.effects]

    @property
    def effect(self) -> str | None:
        e = self.hub.effect_of(self.screen)
        return e["name"] if e else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"screen": self.screen, "parameters": self.hub.parameters(self.screen)}

    def _snapshot(self) -> object:
        return (self.available, self.is_on, self.brightness, self.effect, self.name, self.extra_state_attributes)

    async def async_set_parameter(self, parameter: str, value: Any) -> None:
        try:
            await self.hub.async_set_parameter(self.screen, parameter, value)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="bad_parameter", translation_placeholders={"error": str(err)}
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Everything in one step, so the screen never shows a state in between.
        patch: dict[str, Any] = {}
        if ATTR_EFFECT in kwargs:
            effect = next((e["id"] for e in self.hub.effects if e["name"] == kwargs[ATTR_EFFECT]), None)
            if effect is not None:
                # Like the web UI: choosing an effect gives the screen its own instance again.
                patch.update(effect=effect, source=self.screen)
        if ATTR_BRIGHTNESS in kwargs:
            patch["brightness"] = int(kwargs[ATTR_BRIGHTNESS])
        if not self.is_on:
            patch["on"] = True
        if patch:
            await self.hub.async_patch_screen(self.screen, patch)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_patch_screen(self.screen, {"on": False})


class ColorParameter(NoParameters, EffectParameter, LightEntity):
    """A colour of an effect, e.g. "color" of Single Color. Off is black."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        v = self.lamp_value
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
        await self.async_send([int(c) for c in rgb])

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_send([0, 0, 0])
