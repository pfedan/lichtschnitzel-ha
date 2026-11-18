"""The integration against the fake lamp: entities, writes, live changes."""
from __future__ import annotations

import asyncio

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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
    assert s0.attributes["effect_list"] == ["Single Color", "Aurora", "Nova Missile"]
    assert hass.states.get("light.testlampe_screen_1").attributes["effect"] == "Single Color"
    assert hass.states.get("light.testlampe_screen_2") is None  # screens without LEDs get no entities

    await hass.services.async_call("light", "turn_off", {"entity_id": "light.testlampe_screen_0"}, blocking=True)
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").state == "off")
    await wait_for(hass, lambda: (0, {"on": False}) in lamp.writes)

    # Effect, brightness and switching on arrive as one change of the screen.
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.testlampe_screen_0", "effect": "Single Color", "brightness": 128}, blocking=True)
    assert lamp.writes[-1] == (0, {"effect": "single_color", "source": 0, "brightness": 128, "on": True})
    s0 = hass.states.get("light.testlampe_screen_0")
    assert s0.state == "on" and s0.attributes["brightness"] == 128 and s0.attributes["effect"] == "Single Color"
    assert [p["key"] for p in s0.attributes["parameters"]] == ["color"]  # the answer brought the new effect's values

    await hass.services.async_call("light", "turn_off", {"entity_id": "light.testlampe"}, blocking=True)
    await wait_for(hass, lambda: lamp.lamp["on"] is False)
    assert hass.states.get("light.testlampe").state == "off"
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.testlampe", "brightness": 77}, blocking=True)
    await wait_for(hass, lambda: lamp.lamp == {"on": True, "brightness": 77})
    assert hass.states.get("light.testlampe").attributes["brightness"] == 77


async def test_live_changes(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    # A label set elsewhere (web UI) renames the entity.
    await lamp.patch_screen(0, {"label": "Flur"})
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").attributes.get("friendly_name") == "Testlampe Flur")
    # A screen that gets LEDs gets entities; one that loses them becomes unavailable, not removed.
    await lamp.patch_screen(2, {"begin": 150, "length": 10, "effect": "aurora"})
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_2") is not None)
    await lamp.patch_screen(2, {"length": 0})
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_2").state == "unavailable")
    # A change at the lamp itself (a button on the board) arrives the same way.
    await lamp.patch_screen(1, {"effect": "aurora"})
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_1").attributes.get("effect") == "Aurora")
    await lamp.patch_lamp({"brightness": 40})
    await wait_for(hass, lambda: hass.states.get("light.testlampe").attributes.get("brightness") == 40)


async def test_missed_changes(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    """What changed while the WebSocket was not open shows through the revision of the first status."""
    await connected(hass)
    for ws in list(lamp.clients):
        await ws.close()
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").state == "unavailable")
    await connected(hass)
    # Between reading the state and the WebSocket opening: nobody was told.
    lamp.show(1, "aurora")
    lamp.revision += 1
    for ws in list(lamp.clients):
        await ws.close()
    hub = entry.runtime_data
    real_connect = hub.client.ws_connect

    async def connect_late():
        lamp.show(0, "single_color")
        lamp.revision += 1
        return await real_connect()

    hub.client.ws_connect = connect_late
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").attributes.get("effect") == "Single Color", timeout=6)
    assert hass.states.get("light.testlampe_screen_1").attributes.get("effect") == "Aurora"


async def test_parameters_and_sensors(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    reg = er.async_get(hass)
    speed = next(e for e in reg.entities.values() if e.unique_id.endswith("screen0-aurora-speed"))
    assert speed.disabled_by is not None  # parameters stay off until switched on
    assert speed.original_name == "Screen 0 Aurora Speed"
    color = next(e for e in reg.entities.values() if e.unique_id.endswith("screen1-single_color-color"))
    assert color.domain == "light"
    assert hass.states.get("sensor.testlampe_led_current").state == "1250"
    assert hass.states.get("binary_sensor.testlampe_efuse_tripped").state == "off"


async def test_preset_button(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    await hass.services.async_call("button", "press", {"entity_id": "button.testlampe_screen_0_next_preset"}, blocking=True)
    await wait_for(hass, lambda: {p["key"]: p["value"] for p in hass.states.get("light.testlampe_screen_0").attributes["parameters"]}["palette"] == 1)
    # An effect without presets has no use for the button.
    await lamp.patch_screen(0, {"effect": "nova_missile"})
    await wait_for(hass, lambda: hass.states.get("button.testlampe_screen_0_next_preset").state == "unavailable")
    assert hass.states.get("button.testlampe_screen_0_default_values").state != "unavailable"


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
    assert {p["key"]: p for p in params}["speed"] == {"key": "speed", "name": "Speed", "group": "", "type": "float", "value": 1.0, "min": 0.2, "max": 3.0}
    assert [p["key"] for p in hass.states.get("light.testlampe_screen_1").attributes["parameters"]] == ["color"]

    async def set_param(entity: str, parameter: str, value) -> None:
        await hass.services.async_call(
            "lichtschnitzel", "set_parameter", {"entity_id": entity, "parameter": parameter, "value": value}, blocking=True
        )

    await set_param("light.testlampe_screen_0", "speed", 9)  # limited to the maximum
    await wait_for(hass, lambda: (0, {"params": {"speed": 3.0}}) in lamp.writes)
    await set_param("light.testlampe_screen_0", "palette", 1.6)
    await wait_for(hass, lambda: (0, {"params": {"palette": 2}}) in lamp.writes)
    await set_param("light.testlampe_screen_1", "color", "#102030")
    await wait_for(hass, lambda: (1, {"params": {"color": [16, 32, 48]}}) in lamp.writes)
    # The lamp does not echo a change that came over this side's WebSocket; the value shows anyway.
    assert hass.states.get("light.testlampe_screen_1").attributes["parameters"][0]["value"] == [16, 32, 48]
    # The status after it moves the revision; that alone is no reason to read the state again.
    reads = lamp.state_reads
    await lamp.status()
    await wait_for(hass, lambda: entry.runtime_data.state["revision"] == lamp.revision)
    assert lamp.state_reads == reads

    # What a parameter was called before API version 2 still finds it (automations written then).
    await set_param("light.testlampe_screen_0", "Speed", 2)
    await wait_for(hass, lambda: (0, {"params": {"speed": 2.0}}) in lamp.writes)

    with pytest.raises(ServiceValidationError):
        await set_param("light.testlampe_screen_0", "color", [1, 2, 3])  # not a parameter of Aurora
    with pytest.raises(ServiceValidationError):
        await set_param("light.testlampe", "speed", 1)  # the master switch has no effect

    # A screen showing another screen's effect sets the parameters of that instance.
    await lamp.patch_screen(1, {"source": 0})
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_1").attributes.get("effect") == "Aurora")
    await set_param("light.testlampe_screen_1", "speed", 0.5)
    await wait_for(hass, lambda: (0, {"params": {"speed": 0.5}}) in lamp.writes)

    # Groups, and something that happens once: it has no value, setting it says where.
    await lamp.patch_screen(0, {"effect": "nova_missile"})
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_0").attributes.get("effect") == "Nova Missile")
    params = {p["key"]: p for p in hass.states.get("light.testlampe_screen_0").attributes["parameters"]}
    assert params["period_length"]["group"] == "Flying Emitters"
    assert params["spawn_collide_particle"]["trigger"] is True
    await set_param("light.testlampe_screen_0", "Flying Emitters/Period Length", 20)
    await wait_for(hass, lambda: (0, {"params": {"period_length": 20.0}}) in lamp.writes)
    await set_param("light.testlampe_screen_0", "spawn_collide_particle", 0.4)
    await wait_for(hass, lambda: (0, {"params": {"spawn_collide_particle": 0.4}}) in lamp.writes)


async def test_parameter_entities(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    """A parameter switched on in the entity list: a number or a colour, available while its effect runs."""
    await connected(hass)
    reg = er.async_get(hass)
    speed = next(e for e in reg.entities.values() if e.unique_id.endswith("screen0-aurora-speed"))
    color = next(e for e in reg.entities.values() if e.unique_id.endswith("screen1-single_color-color"))
    reg.async_update_entity(speed.entity_id, disabled_by=None)
    reg.async_update_entity(color.entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await connected(hass)
    state = hass.states.get(speed.entity_id)
    assert float(state.state) == 1.0 and state.attributes["min"] == 0.2 and state.attributes["max"] == 3.0
    await hass.services.async_call("number", "set_value", {"entity_id": speed.entity_id, "value": 2.5}, blocking=True)
    await wait_for(hass, lambda: (0, {"params": {"speed": 2.5}}) in lamp.writes)
    assert float(hass.states.get(speed.entity_id).state) == 2.5
    assert hass.states.get(color.entity_id).attributes["rgb_color"] == (255, 120, 30)
    await hass.services.async_call("light", "turn_on", {"entity_id": color.entity_id, "rgb_color": [0, 10, 200]}, blocking=True)
    await wait_for(hass, lambda: (1, {"params": {"color": [0, 10, 200]}}) in lamp.writes)
    # Another effect on the screen: the entity waits; back again, it has the value the screen remembered.
    await lamp.patch_screen(0, {"effect": "single_color"})
    await wait_for(hass, lambda: hass.states.get(speed.entity_id).state == "unavailable")
    await lamp.patch_screen(0, {"effect": "aurora"})
    await wait_for(hass, lambda: hass.states.get(speed.entity_id).state != "unavailable")
    assert float(hass.states.get(speed.entity_id).state) == 2.5


async def test_entities_from_before_api_2(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    """Parameter entities made before API version 2 keep their entity id: their unique ids are renamed."""
    await connected(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    reg = er.async_get(hass)
    for e in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        reg.async_remove(e.entity_id)
    head = "aa:bb:cc:dd:ee:ff-screen0"
    old = {
        "number.wohnzimmer_tempo": f"{head}-AuroraEffect-Speed",
        "number.wohnzimmer_periode": f"{head}-NovaMissileEffect-Flying Emitters/Period Length",
        "number.wohnzimmer_weg": f"{head}-SingleDotEffect-Position",  # a parameter that is gone
        "light.wohnzimmer_farbe": "aa:bb:cc:dd:ee:ff-screen1-SingleColorEffect-myColor",
    }
    for entity_id, unique_id in old.items():
        domain, name = entity_id.split(".")
        reg.async_get_or_create(domain, "lichtschnitzel", unique_id, suggested_object_id=name, config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await connected(hass)
    assert reg.async_get("number.wohnzimmer_tempo").unique_id == f"{head}-aurora-speed"
    assert reg.async_get("number.wohnzimmer_periode").unique_id == f"{head}-nova_missile-period_length"
    assert reg.async_get("light.wohnzimmer_farbe").unique_id == "aa:bb:cc:dd:ee:ff-screen1-single_color-color"
    assert reg.async_get("number.wohnzimmer_weg").unique_id == f"{head}-SingleDotEffect-Position"
    assert float(hass.states.get("number.wohnzimmer_tempo").state) == 1.0  # the old entity is the live one


async def test_old_firmware(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    """A lamp that speaks another API version is not set up, and says why."""
    from homeassistant.config_entries import ConfigEntryState

    lamp.api = 1
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.config_entries.async_get_entry(entry.entry_id)
    assert state.state is ConfigEntryState.SETUP_RETRY
    assert "API version 1" in (state.reason or "")


async def test_another_lamp_after_reconnect(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    """The Effektlabor switching to a 3D scene: it drops the connection and comes back with another
    id, other screens and names. The entities stay on the one device, new screens get entities."""
    await connected(hass)
    for ws in list(lamp.clients):
        await ws.close()
    lamp.info_id = "lz.scene.laube"
    lamp.uptime = 1
    lamp.screens[0]["label"] = "Pergola"
    lamp.screens[4].update(begin=150, length=30)
    lamp.show(4, "aurora")
    lamp.revision = 1
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_4") is not None, timeout=6)
    assert hass.states.get("light.testlampe_screen_0").attributes["friendly_name"] == "Testlampe Pergola"
    devices = {e.device_id for e in er.async_get(hass).entities.values() if e.config_entry_id == entry.entry_id}
    assert len(devices) == 1


async def test_scenes(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    sel = "select.testlampe_scene"
    await wait_for(hass, lambda: hass.states.get(sel) is not None and hass.states.get(sel).state != "unavailable")
    assert hass.states.get(sel).attributes["options"] == []

    # Scenes stored in the web UI arrive live.
    await lamp.store_scene(0, "Alltag")
    await lamp.patch_screen(0, {"brightness": 40})
    await lamp.patch_screen(1, {"length": 0})
    await lamp.store_scene(3, "Abend")
    await wait_for(hass, lambda: hass.states.get(sel).attributes["options"] == ["Alltag", "Abend"])
    assert hass.states.get(sel).state == "Abend"

    # Choosing an option switches all screens.
    await hass.services.async_call("select", "select_option", {"entity_id": sel, "option": "Alltag"}, blocking=True)
    await wait_for(hass, lambda: hass.states.get(sel).state == "Alltag")
    await wait_for(hass, lambda: hass.states.get("light.testlampe_screen_1").state != "unavailable")
    assert lamp.screens[0]["brightness"] == 255
    assert hass.states.get("light.testlampe_screen_0").attributes["brightness"] == 255

    # A change to a screen: the scene stays visible as "(changed)", and its plain name loads it again.
    await lamp.patch_screen(0, {"brightness": 10})
    await lamp.status()
    await wait_for(hass, lambda: hass.states.get(sel).state == "Alltag (changed)")
    assert hass.states.get(sel).attributes["options"] == ["Alltag", "Abend", "Alltag (changed)"]
    assert hass.states.get(sel).attributes["modified"] is True
    assert hass.states.get(sel).attributes["active"] == "Alltag"
    await hass.services.async_call("select", "select_option", {"entity_id": sel, "option": "Alltag (changed)"}, blocking=True)
    assert lamp.screens[0]["brightness"] == 10  # choosing the changed state keeps it

    # Overwrite the scene with the changed screens.
    btn = "button.testlampe_overwrite_scene"
    assert hass.states.get(btn).state != "unavailable"
    await hass.services.async_call("button", "press", {"entity_id": btn}, blocking=True)
    await wait_for(hass, lambda: hass.states.get(sel).state == "Alltag")
    assert lamp.scenes[0][1][0]["brightness"] == 10

    # The service by index and by name (ignoring case).
    await hass.services.async_call("lichtschnitzel", "activate_scene", {"entity_id": sel, "index": 3}, blocking=True)
    await wait_for(hass, lambda: hass.states.get(sel).state == "Abend")
    assert lamp.screens[0]["brightness"] == 40
    await hass.services.async_call("lichtschnitzel", "activate_scene", {"entity_id": sel, "name": "alltag"}, blocking=True)
    await wait_for(hass, lambda: hass.states.get(sel).state == "Alltag")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("lichtschnitzel", "activate_scene", {"entity_id": sel, "name": "Party"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("lichtschnitzel", "activate_scene", {"entity_id": sel}, blocking=True)


async def test_notify(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    # Two screens in one action: one request, so they start in step.
    await hass.services.async_call(
        "lichtschnitzel",
        "notify",
        {"entity_id": ["light.testlampe_screen_0", "light.testlampe_screen_1"], "pattern": "pulse", "rgb_color": [255, 160, 0],
         "brightness_pct": 50, "duration": 5, "period": 1.5, "start": -6, "length": 4},
        blocking=True,
    )
    assert lamp.notifications == [
        ("/api/notify", {"pattern": "pulse", "ms": 5000, "color": [255, 160, 0], "brightness": 128, "period": 1500, "start": -6, "length": 4, "screens": [0, 1]})
    ]
    # The lamp's own light: every screen; defaults.
    await hass.services.async_call("lichtschnitzel", "notify", {"entity_id": "light.testlampe"}, blocking=True)
    assert lamp.notifications[-1] == ("/api/notify", {"pattern": "flash", "ms": 3000})
    # Duration 0 ends it.
    await hass.services.async_call("lichtschnitzel", "notify", {"entity_id": "light.testlampe_screen_1", "duration": 0}, blocking=True)
    assert lamp.notifications[-1] == ("/api/notify", {"pattern": "flash", "ms": 0, "screens": [1]})
    lamp.can_notify = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("lichtschnitzel", "notify", {"entity_id": "light.testlampe_screen_0"}, blocking=True)


async def test_indicators(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    await hass.services.async_call(
        "lichtschnitzel",
        "set_indicator",
        {"entity_id": ["light.testlampe_screen_0", "light.testlampe_screen_1"], "id": "waschmaschine", "pattern": "pulse",
         "rgb_color": [0, 120, 255], "progress": 40, "priority": 2},
        blocking=True,
    )
    assert lamp.notifications[-1] == (
        "/api/indicators",
        {"id": "waschmaschine", "pattern": "pulse", "ms": 0, "color": [0, 120, 255], "progress": 40.0, "priority": 2, "screens": [0, 1]},
    )
    await hass.services.async_call("lichtschnitzel", "clear_indicator", {"entity_id": "light.testlampe_screen_1", "id": "waschmaschine"}, blocking=True)
    assert lamp.notifications[-1] == ("/api/indicators/clear", {"id": "waschmaschine", "screens": [1]})
    await hass.services.async_call("lichtschnitzel", "clear_indicator", {"entity_id": "light.testlampe"}, blocking=True)
    assert lamp.notifications[-1] == ("/api/indicators/clear", {})
    with pytest.raises(vol.Invalid):  # an indicator needs an id
        await hass.services.async_call("lichtschnitzel", "set_indicator", {"entity_id": "light.testlampe_screen_0"}, blocking=True)


async def test_clear_all_indicators(hass: HomeAssistant, lamp: FakeLamp, entry) -> None:
    await connected(hass)
    lamp.indicator_count = 3
    await lamp.status()
    await wait_for(hass, lambda: hass.states.get("sensor.testlampe_indicators").state == "3")
    await hass.services.async_call("button", "press", {"entity_id": "button.testlampe_clear_indicators"}, blocking=True)
    assert lamp.notifications[-1] == ("/api/indicators/clear", {})
