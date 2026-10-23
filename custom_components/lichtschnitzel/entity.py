"""Base entity: one device per lamp, available while the WebSocket is up."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NUM_SCREENS
from .hub import LichtschnitzelHub


def lamp_id(hub: LichtschnitzelHub) -> str:
    # Fixed for the config entry: the Effektlabor reports another id for each 3D scene, its
    # entities stay on the one device and follow the scene shown.
    return hub.ident or str(hub.info.get("id", hub.client.host))


class LichtschnitzelEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: LichtschnitzelHub, key: str) -> None:
        self.hub = hub
        self._attr_unique_id = f"{lamp_id(hub)}-{key}"
        ident = lamp_id(hub)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ident)},
            connections={(CONNECTION_NETWORK_MAC, ident)} if ident.count(":") == 5 else set(),
            name=hub.info.get("name") or "Lichtschnitzel",
            manufacturer="Lichtschnitzel",
            model="LED controller",
            sw_version=hub.info.get("version"),
            configuration_url=hub.client.base + "/",
        )
        self._last: object = None

    @property
    def available(self) -> bool:
        return self.hub.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hub.add_listener(self._changed))

    def _snapshot(self) -> object:
        """What the state shows; the state is only written when it changes."""
        return (self.available, self.state, dict(self.extra_state_attributes or {}), self.name)

    def _changed(self) -> None:
        snap = self._snapshot()
        if snap != self._last:
            self._last = snap
            self.async_write_ha_state()


class ScreenEntity(LichtschnitzelEntity):
    """Something of one screen: named after its Label, available while the screen has LEDs."""

    def __init__(self, hub: LichtschnitzelHub, screen: int, key: str, suffix: str | None) -> None:
        super().__init__(hub, f"screen{screen}-{key}")
        self.screen = screen
        self.base = hub.screen_path(screen)
        self._suffix = suffix

    @property
    def name(self) -> str:
        label = self.hub.screen_label(self.screen)
        return f"{label} {self._suffix}" if self._suffix else label

    @property
    def available(self) -> bool:
        return self.hub.connected and self.hub.screen_used(self.screen)


def new_screens(hub: LichtschnitzelHub, done: set[int]) -> list[int]:
    """Screens in use that have no entities yet; they are never removed, only unavailable."""
    out = [i for i in range(NUM_SCREENS) if i not in done and hub.screen_used(i)]
    done.update(out)
    return out
