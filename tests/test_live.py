"""Against a real lamp, the simulator of the firmware (lz_sim) or the Effektlabor behind
lab_bridge.py; skipped unless LICHTSCHNITZEL_LIVE=<host>[:<port>] is set. Switches the first screen
off and on, changes its effect, brightness and a parameter, and puts everything back.

    LICHTSCHNITZEL_LIVE=127.0.0.1:8080 pytest tests/test_live.py

The lamp must let Home Assistant in (web UI: Settings, Home Assistant; lz_sim --home-assistant).
"""
from __future__ import annotations

import asyncio
import os

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lichtschnitzel.const import DOMAIN

from .test_entities import wait_for

LIVE = os.environ.get("LICHTSCHNITZEL_LIVE", "")
pytestmark = pytest.mark.skipif(not LIVE, reason="LICHTSCHNITZEL_LIVE not set")


async def test_live(hass: HomeAssistant, socket_enabled) -> None:
    host, _, port = LIVE.partition(":")
    entry = MockConfigEntry(domain=DOMAIN, data={"host": host, "port": int(port or 80),
                                                 "password": os.environ.get("LICHTSCHNITZEL_PASSWORD", "")})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hub = entry.runtime_data
    reg = er.async_get(hass)
    lights = [e.entity_id for e in reg.entities.values() if e.config_entry_id == entry.entry_id and e.domain == "light"
              and e.unique_id.endswith("-light")]
    assert lights, "no screen with LEDs"
    eid = sorted(lights)[0]
    screen = int(reg.async_get(eid).unique_id.rsplit("-", 2)[-2].removeprefix("screen"))
    await wait_for(hass, lambda: hass.states.get(eid).state != "unavailable", 5)
    before = dict(hass.states.get(eid).attributes)
    was_on = hass.states.get(eid).state == "on"
    label = hub.screen(screen).get("label", "")
    assert hub.info["api"] == 2 and before["effect_list"] == [e["name"] for e in hub.effects]

    async def on_lamp() -> dict:
        return (await hub.client.state())["screens"][screen]

    try:
        await hass.services.async_call("light", "turn_off", {"entity_id": eid}, blocking=True)
        await wait_for(hass, lambda: hass.states.get(eid).state == "off", 5)
        assert (await on_lamp())["on"] is False
        # An effect with a number to set, other than the one the screen shows.
        other = next(e for e in hub.effects if e["name"] != before.get("effect")
                     and any(p["type"] == "float" and not p.get("trigger") for p in e["params"]))
        await hass.services.async_call("light", "turn_on", {"entity_id": eid, "effect": other["name"], "brightness": 100}, blocking=True)
        await wait_for(hass, lambda: (s := hass.states.get(eid)).state == "on" and s.attributes.get("effect") == other["name"]
                       and s.attributes.get("brightness") == 100, 5)
        now = await on_lamp()
        assert (now["on"], now["effect"], now["brightness"], now["source"]) == (True, other["id"], 100, screen)
        # A parameter over the WebSocket: the lamp has it, limited to its range, and the attribute shows it.
        param = next(p for p in other["params"] if p["type"] == "float" and not p.get("trigger"))
        was = now["params"][param["id"]]
        await hass.services.async_call("lichtschnitzel", "set_parameter", {"entity_id": eid, "parameter": param["id"], "value": 1e9}, blocking=True)
        shown = lambda: next(p for p in hass.states.get(eid).attributes["parameters"] if p["key"] == param["id"])["value"]
        await wait_for(hass, lambda: shown() == pytest.approx(param["max"]), 5)
        for _ in range(50):
            if (await on_lamp())["params"][param["id"]] == pytest.approx(param["max"]):
                break
            await asyncio.sleep(0.05)
        assert (await on_lamp())["params"][param["id"]] == pytest.approx(param["max"])
        await hass.services.async_call("lichtschnitzel", "set_parameter", {"entity_id": eid, "parameter": param["id"], "value": was}, blocking=True)
        # A change from elsewhere (here: a plain request) arrives as an event.
        await hub.client.patch_screen(screen, {"label": "Live-Test"})
        await wait_for(hass, lambda: "Live-Test" in hass.states.get(eid).attributes["friendly_name"], 5)
    finally:
        await hub.client.patch_screen(screen, {"label": label})
        data = {"entity_id": eid, "brightness": before.get("brightness") or 255}
        if before.get("effect"):
            data["effect"] = before["effect"]
        await hass.services.async_call("light", "turn_on", data, blocking=True)
        if not was_on:
            await hass.services.async_call("light", "turn_off", {"entity_id": eid}, blocking=True)
        await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
