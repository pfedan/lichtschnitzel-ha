"""Against a real lamp, the mock or the Effektlabor behind lab_bridge.py; skipped unless
LICHTSCHNITZEL_LIVE=<host>[:<port>] is set. Switches the first screen off and on, changes its
effect and brightness, and puts everything back.

    LICHTSCHNITZEL_LIVE=127.0.0.1:8081 pytest tests/test_live.py
"""
from __future__ import annotations

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
    base = f"FramebufferManager/Screen {screen}"
    await wait_for(hass, lambda: hass.states.get(eid).state != "unavailable", 5)
    before = dict(hass.states.get(eid).attributes)
    was_on = hass.states.get(eid).state == "on"
    try:
        await hass.services.async_call("light", "turn_off", {"entity_id": eid}, blocking=True)
        await wait_for(hass, lambda: hub.value(f"{base}/On") == 0 and hub.value(f"{base}/Effect/name") == "none", 5)
        other = next(n for n in before["effect_list"] if n != before.get("effect"))
        await hass.services.async_call("light", "turn_on", {"entity_id": eid, "effect": other, "brightness": 100}, blocking=True)
        await wait_for(hass, lambda: (s := hass.states.get(eid)).state == "on" and s.attributes.get("effect") == other
                       and s.attributes.get("brightness") == 100, 5)
        await wait_for(hass, lambda: hub.value(f"{base}/Effect/name") not in (None, "none"), 5)
    finally:
        data = {"entity_id": eid, "brightness": before.get("brightness") or 255}
        if before.get("effect"):
            data["effect"] = before["effect"]
        await hass.services.async_call("light", "turn_on", data, blocking=True)
        if not was_on:
            await hass.services.async_call("light", "turn_off", {"entity_id": eid}, blocking=True)
        await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
