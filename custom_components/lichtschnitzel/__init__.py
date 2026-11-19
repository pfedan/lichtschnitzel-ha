"""Lichtschnitzel LED controllers in Home Assistant.

Talks to the lamp's own HTTP and WebSocket API (firmware/API.md in pfedan/lichtschnitzel, version 2):
one WebSocket per lamp keeps the state live, values are written the same way.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_platform_entity_service
from homeassistant.helpers.typing import ConfigType

from .api import AuthFailed, HomeAssistantOff, LichtschnitzelClient, LichtschnitzelError
from .migrate import async_migrate_unique_ids
from .const import CARD_URL, CONF_PASSWORD, DEFAULT_PORT, DOMAIN, NOTIFY_PATTERNS, PLATFORMS
from .hub import LichtschnitzelHub

type LichtschnitzelConfigEntry = ConfigEntry[LichtschnitzelHub]

_LOGGER = logging.getLogger(__name__)

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
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        "activate_scene",
        entity_domain=SELECT_DOMAIN,
        schema={
            vol.Exclusive("index", "scene"): vol.All(vol.Coerce(int), vol.Range(min=0, max=15)),
            vol.Exclusive("name", "scene"): cv.string,
        },
        func="async_activate_scene",
    )
    look = {
        vol.Optional("rgb_color"): vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple)),
        vol.Optional("brightness_pct"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("period"): vol.All(vol.Coerce(float), vol.Range(min=0.03, max=60)),
        vol.Optional("start"): vol.All(vol.Coerce(int), vol.Range(min=-10000, max=10000)),
        vol.Optional("length"): vol.All(vol.Coerce(int), vol.Range(min=0, max=10000)),
        vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=0, max=600)),
    }
    indicator_id = vol.All(cv.string, vol.Length(min=1, max=23))
    for name, schema in (
        ("notify", {vol.Optional("pattern", default="flash"): vol.In(NOTIFY_PATTERNS), **look}),
        (
            "set_indicator",
            {
                vol.Required("id"): indicator_id,
                vol.Optional("pattern", default="solid"): vol.In(NOTIFY_PATTERNS),
                vol.Optional("progress"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional("priority"): vol.All(vol.Coerce(int), vol.Range(min=-100, max=100)),
                **look,
            },
        ),
        ("clear_indicator", {vol.Optional("id"): indicator_id}),
    ):
        async_register_platform_entity_service(
            hass, DOMAIN, name, entity_domain=LIGHT_DOMAIN, schema=schema, func=f"async_{name}"
        )
    await _register_card(hass)
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Serves the Lichtschnitzel card and loads it in every dashboard (no resource to add by hand).

    Two ways, because the companion apps did not always run the page-wide module: the module of the
    frontend (every page) and a dashboard resource (storage mode), which the dashboard loads before
    it builds its cards. ?v= is the content's hash, so a new card is never taken from a cache."""
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    if hass.http is None or DATA_EXTRA_MODULE_URL not in hass.data:
        return  # no frontend (tests, or a setup without the UI)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), False)])
    digest = await hass.async_add_executor_job(lambda: hashlib.sha256(CARD_FILE.read_bytes()).hexdigest()[:10])
    url = f"{CARD_URL}?v={digest}"
    add_extra_js_url(hass, url)
    await _register_resource(hass, url)


async def _register_resource(hass: HomeAssistant, url: str) -> None:
    """Keeps one dashboard resource for the card at url (storage mode only; YAML resources stay the user's)."""
    data = hass.data.get("lovelace")
    resources = data.get("resources") if isinstance(data, dict) else getattr(data, "resources", None)
    if resources is None or not hasattr(resources, "async_create_item"):
        return  # no dashboards, or resources in YAML
    try:
        if not getattr(resources, "loaded", True):
            await resources.async_load()
            resources.loaded = True
        ours = [r for r in resources.async_items() if str(r.get("url", "")).split("?")[0] == CARD_URL]
        if not ours:
            await resources.async_create_item({"res_type": "module", "url": url})
            return
        if ours[0].get("url") != url:
            await resources.async_update_item(ours[0]["id"], {"res_type": "module", "url": url})
        for extra in ours[1:]:
            await resources.async_delete_item(extra["id"])
    except Exception:  # the card still loads as a frontend module
        _LOGGER.warning("Could not register the Lichtschnitzel card as a dashboard resource", exc_info=True)


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
    except HomeAssistantOff as err:
        # Retried until it is switched on in the lamp's web UI (Settings, Home Assistant).
        raise ConfigEntryNotReady("Home Assistant is switched off in the lamp's settings") from err
    except LichtschnitzelError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub
    async_migrate_unique_ids(hass, entry, hub.effects)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hub.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.stop()
    return ok
