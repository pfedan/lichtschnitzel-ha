"""Setup: found by mDNS (_lichtschnitzel._tcp) or entered by address; a password only if the lamp
has one for "control"."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # Home Assistant before 2025.1
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .api import AuthFailed, CannotConnect, LichtschnitzelClient, LichtschnitzelError
from .const import CONF_PASSWORD, DEFAULT_PORT, DOMAIN


class LichtschnitzelConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host = ""
        self._port = DEFAULT_PORT
        self._name = ""

    async def _check(self, host: str, port: int, password: str | None) -> dict:
        client = LichtschnitzelClient(async_get_clientsession(self.hass), host, port, password)
        return await client.check_access()

    def _entry(self, info: dict, password: str | None) -> ConfigFlowResult:
        data = {CONF_HOST: self._host, CONF_PORT: self._port}
        if password:
            data[CONF_PASSWORD] = password
        return self.async_create_entry(title=info.get("name") or self._name or self._host, data=data)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = int(user_input.get(CONF_PORT) or DEFAULT_PORT)
            password = user_input.get(CONF_PASSWORD) or None
            try:
                info = await self._check(self._host, self._port, password)
            except AuthFailed:
                errors[CONF_PASSWORD] = "invalid_auth" if password else "password_needed"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except LichtschnitzelError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(info["id"]))
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})
                return self._entry(info, password)
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=self._host): str,
            vol.Optional(CONF_PORT, default=self._port): int,
            vol.Optional(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._name = (discovery_info.properties or {}).get("name") or discovery_info.name.split(".")[0]
        try:
            info = await LichtschnitzelClient(async_get_clientsession(self.hass), self._host, self._port).info()
        except LichtschnitzelError:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(str(info["id"]))
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})
        self._name = info.get("name") or self._name
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD) or None
            try:
                info = await self._check(self._host, self._port, password)
            except AuthFailed:
                errors[CONF_PASSWORD] = "invalid_auth" if password else "password_needed"
            except LichtschnitzelError:
                errors["base"] = "cannot_connect"
            else:
                return self._entry(info, password)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD): str}),
            description_placeholders={"name": self._name, "host": f"{self._host}:{self._port}"},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._host = entry_data[CONF_HOST]
        self._port = entry_data.get(CONF_PORT, DEFAULT_PORT)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD) or None
            try:
                await self._check(self._host, self._port, password)
            except AuthFailed:
                errors[CONF_PASSWORD] = "invalid_auth"
            except LichtschnitzelError:
                errors["base"] = "cannot_connect"
            else:
                entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: password or ""})
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}), errors=errors)
