"""Lichtschnitzel LED controllers in Home Assistant.

Talks to the lamp's own HTTP and WebSocket API (firmware-esp32/API.md in pfedan/lichtschnitzel):
one WebSocket per lamp keeps the state live, writes go the same way.
"""
from __future__ import annotations

import json
from pathlib import Path

import voluptuous as vol
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_platform_entity_service
from homeassistant.helpers.typing import ConfigType

from .api import AuthFailed, LichtschnitzelClient, LichtschnitzelError
from .const import CARD_URL, CONF_PASSWORD, DEFAULT_PORT, DOMAIN, PLATFORMS
from .hub import LichtschnitzelHub

type LichtschnitzelConfigEntry = ConfigEntry[LichtschnitzelHub]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_FILE = Path(__file__).parent / "frontend" / "lichtschnitzel-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_parameter",
        entity_domain=LIGHT_DOMAIN,
        schema={
            vol.Required("parameter"): cv.string,
            vol.Required("value"): vol.Any([vol.Coerce(int)], vol.Coerce(float), cv.string),
        },
        func="async_set_parameter",
    )
    await _register_card(hass)
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Serves the Lichtschnitzel card and loads it in every dashboard (no resource to add by hand)."""
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    if hass.http is None or DATA_EXTRA_MODULE_URL not in hass.data:
        return  # no frontend (tests, or a setup without the UI)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), False)])
    version = await hass.async_add_executor_job(
        lambda: json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry) -> bool:
    client = LichtschnitzelClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data.get(CONF_PASSWORD) or None,
    )
    hub = LichtschnitzelHub(client)
    hub.ident = entry.unique_id
    try:
        await hub.async_load()
    except AuthFailed as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except LichtschnitzelError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hub.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.stop()
    return ok
