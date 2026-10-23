"""The integration against the fake lamp: entities, writes, live changes."""
from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .fake_lamp import FakeLamp


async def wait_for(hass: HomeAssistant, cond, timeout: float = 3.0) -> None:
    for _ in range(int(timeout / 0.02)):
        await hass.async_block_till_done()
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


async def connected(hass: HomeAssistant) -> None:
    await wait_for(hass, lambda: (s := hass.states.get("light.testlampe_screen_0")) is not None and s.state != "unavailable")


async def test_lights(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    master = hass.states.get("light.testlampe")
    assert master.state == "on" and master.attributes["brightness"] == 255
    s0 = hass.states.get("light.testlampe_screen_0")
    assert s0.attributes["effect"] == "Aurora"
    assert s0.attributes["effect_list"] == ["Single Color", "Aurora"]
    assert hass.states.get("light.testlampe_screen_1").attributes["effect"] == "Single Color"
    assert hass.states.get("light.testlampe_screen_2") is None  # screens without LEDs get no entities

    await hass.services.async_call("light", "turn_off", {"entity_id": "light.testlampe_screen_0"}, blocking=True)
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").state == "off")
    await wait_for(hass, lambda: ("FramebufferManager/Screen 0/On", 0) in lamp.writes)

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.testlampe_screen_0", "effect": "Single Color", "brightness": 128}, blocking=True)
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").attributes.get("effect") == "Single Color")
    s0 = hass.states.get("light.testlampe_screen_0")
    assert s0.state == "on" and s0.attributes["brightness"] == 128

    await hass.services.async_call("light", "turn_off", {"entity_id": "light.testlampe"}, blocking=True)
    await wait_for(hass, lambda: lamp.tree["FramebufferManager"]["On"]["val"] == 0)
    assert hass.states.get("light.testlampe").state == "off"


async def test_live_changes(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    # A label set elsewhere (web UI) renames the entity.
    await lamp.set("FramebufferManager/Screen 0/Label", "Flur")
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").attributes.get("friendly_name") == "Testlampe Flur")
    # A screen that gets LEDs gets entities; one that loses them becomes unavailable, not removed.
    await lamp.set("FramebufferManager/Screen 2/Length", 10)
    await lamp.set("FramebufferManager/Screen 2/EffectIndex", 1)
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_2") is not None)
    await lamp.set("FramebufferManager/Screen 2/Length", 0)
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_2").state == "unavailable")
    # A change besides property writes (a button on the lamp) shows through the revision.
    lamp.tree["FramebufferManager"]["Screen 1"]["EffectIndex"]["val"] = 1
    lamp.bind(1)
    await lamp.status()
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_1").attributes.get("effect") == "Aurora")


async def test_parameters_and_sensors(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    reg = er.async_get(hass)
    speed = next(e for e in reg.entities.values() if e.unique_id.endswith("screen0-AuroraEffect-Speed"))
    assert speed.disabled_by is not None  # parameters stay off until switched on
    color = next(e for e in reg.entities.values() if e.unique_id.endswith("screen1-SingleColorEffect-myColor"))
    assert color.domain == "light"
    assert hass.states.get("sensor.testlampe_led_current").state == "1250"
    assert hass.states.get("binary_sensor.testlampe_efuse_tripped").state == "off"


async def test_preset_button(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    await hass.services.async_call("button", "press", {"entity_id": "button.testlampe_screen_0_next_preset"}, blocking=True)
    await wait_for(hass, lambda: hass.config_entries.async_get_entry(entry.entry_id).runtime_data.value("FramebufferManager/Screen 0/Effect/Palette") == 1)


async def test_unavailable_and_back(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    for ws in list(lamp.clients):
        await ws.close()
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").state == "unavailable")
    await connected(hass)


async def test_effect_parameters_of_a_screen(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    """The parameters attribute and lichtschnitzel.set_parameter, which the Lichtschnitzel card uses."""
    await connected(hass)
    params = hass.states.get("light.testlampe_screen_0").attributes["parameters"]
    assert {p["key"]: p for p in params}["Speed"] == {"key": "Speed", "type": "float", "value": 1.0, "min": 0.2, "max": 3.0}
    assert [p["key"] for p in hass.states.get("light.testlampe_screen_1").attributes["parameters"]] == ["myColor"]

    async def set_param(entity: str, parameter: str, value) -> None:
        await hass.services.async_call(
            "lichtschnitzel", "set_parameter", {"entity_id": entity, "parameter": parameter, "value": value}, blocking=True
        )

    await set_param("light.testlampe_screen_0", "Speed", 9)  # clamped to the maximum
    await wait_for(hass, lambda: ("FramebufferManager/Screen 0/Effects/AuroraEffect/Speed", 3.0) in lamp.writes)
    await set_param("light.testlampe_screen_0", "Palette", 1.6)
    await wait_for(hass, lambda: ("FramebufferManager/Screen 0/Effects/AuroraEffect/Palette", 2) in lamp.writes)
    await set_param("light.testlampe_screen_1", "myColor", "#102030")
    await wait_for(hass, lambda: ("FramebufferManager/Screen 1/Effects/SingleColorEffect/myColor", [16, 32, 48]) in lamp.writes)
    await wait_for(
        hass,
        lambda: hass.states.get("light.testlampe_screen_1").attributes["parameters"][0]["value"] == [16, 32, 48],
    )

    with pytest.raises(ServiceValidationError):
        await set_param("light.testlampe_screen_0", "myColor", [1, 2, 3])  # not a parameter of Aurora
    with pytest.raises(ServiceValidationError):
        await set_param("light.testlampe", "Speed", 1)  # the master switch has no effect

    # A screen showing another screen's effect sets the parameters of that instance.
    await lamp.set("FramebufferManager/Screen 1/Source", 0)
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_1").attributes.get("effect") == "Aurora")
    await set_param("light.testlampe_screen_1", "Speed", 0.5)
    await wait_for(hass, lambda: ("FramebufferManager/Screen 0/Effects/AuroraEffect/Speed", 0.5) in lamp.writes)


async def test_another_lamp_after_reconnect(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    """The Effektlabor switching to a 3D scene: it drops the connection and comes back with another
    id, other screens and names. The entities stay on the one device, new screens get entities."""
    await connected(hass)
    for ws in list(lamp.clients):
        await ws.close()
    lamp.info_id = "lz.scene.laube"
    lamp.uptime = 1
    fbm = lamp.tree["FramebufferManager"]
    fbm["Screen 0"]["Label"]["val"] = "Pergola"
    fbm["Screen 4"]["Length"]["val"] = 30
    fbm["Screen 4"]["EffectIndex"]["val"] = 1
    lamp.bind(4)
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_4") is not None, timeout=6)
    assert hass.states.get("light.testlampe_screen_0").attributes["friendly_name"] == "Testlampe Pergola"
    devices = {e.device_id for e in er.async_get(hass).entities.values() if e.config_entry_id == entry.entry_id}
    assert len(devices) == 1
