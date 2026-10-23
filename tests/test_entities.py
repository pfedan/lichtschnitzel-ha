"""The integration against the fake lamp: entities, writes, live changes."""
from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
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
