"""Setup by address, with and without password."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.lichtschnitzel.const import DOMAIN

from .fake_lamp import FakeLamp


async def test_user_flow(hass: HomeAssistant, lamp: FakeLamp) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "127.0.0.1", "port": lamp.port})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Testlampe"
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"


async def test_password(hass: HomeAssistant, locked_lamp: FakeLamp) -> None:
    lamp = locked_lamp
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "127.0.0.1", "port": lamp.port})
    assert result["errors"] == {"password": "password_needed"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "127.0.0.1", "port": lamp.port, "password": "falsch"})
    assert result["errors"] == {"password": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "127.0.0.1", "port": lamp.port, "password": "geheim"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["password"] == "geheim"


async def test_unreachable(hass: HomeAssistant, socket_enabled) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "127.0.0.1", "port": 1})
    assert result["errors"] == {"base": "cannot_connect"}
