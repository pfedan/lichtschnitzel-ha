"""Lichtschnitzel LED controllers in Home Assistant.

Talks to the lamp's own HTTP and WebSocket API (firmware-esp32/API.md in pfedan/lichtschnitzel):
one WebSocket per lamp keeps the state live, writes go the same way.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthFailed, LichtschnitzelClient, LichtschnitzelError
from .const import CONF_PASSWORD, DEFAULT_PORT, PLATFORMS
from .hub import LichtschnitzelHub

type LichtschnitzelConfigEntry = ConfigEntry[LichtschnitzelHub]


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry) -> bool:
    client = LichtschnitzelClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data.get(CONF_PASSWORD) or None,
    )
    hub = LichtschnitzelHub(client)
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
