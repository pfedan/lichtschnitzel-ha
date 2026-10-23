"""Fixtures: a fake lamp on 127.0.0.1 and the integration set up against it."""
from __future__ import annotations

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lichtschnitzel.const import DOMAIN

from .fake_lamp import FakeLamp


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def lamp(socket_enabled):
    fake = FakeLamp()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def locked_lamp(socket_enabled):
    fake = FakeLamp(password="geheim")
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def entry(hass, lamp):
    e = MockConfigEntry(domain=DOMAIN, unique_id="aa:bb:cc:dd:ee:ff", data={CONF_HOST: "127.0.0.1", CONF_PORT: lamp.port})
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    yield e
    await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
