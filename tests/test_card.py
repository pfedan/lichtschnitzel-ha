"""The card as a dashboard resource: created once, updated when the card changes, YAML left alone."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.lichtschnitzel import _register_resource
from custom_components.lichtschnitzel.const import CARD_URL


def _resources(hass: HomeAssistant):
    data = hass.data["lovelace"]
    return data["resources"] if isinstance(data, dict) else data.resources


async def test_resource_kept_up_to_date(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "lovelace", {})
    res = _resources(hass)
    await _register_resource(hass, f"{CARD_URL}?v=one")
    await _register_resource(hass, f"{CARD_URL}?v=one")
    items = [r for r in res.async_items() if r["url"].startswith(CARD_URL)]
    assert [(r["type"], r["url"]) for r in items] == [("module", f"{CARD_URL}?v=one")]

    await _register_resource(hass, f"{CARD_URL}?v=two")
    items = [r for r in res.async_items() if r["url"].startswith(CARD_URL)]
    assert [r["url"] for r in items] == [f"{CARD_URL}?v=two"]


async def test_yaml_resources_untouched(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"resource_mode": "yaml"}})
    await _register_resource(hass, f"{CARD_URL}?v=one")  # no error, nothing to add
