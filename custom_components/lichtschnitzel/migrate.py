"""Entities of effect parameters from before API version 2 keep their history and their settings.

Their unique ids named the effect by its class and the parameter by its path
("screen0-AuroraEffect-Speed", "screen1-NovaMissileEffect-Flying Emitters/Period Length"); now both
go by their ids ("screen0-aurora-speed", "screen1-nova_missile-period_length"). The ids of all other
entities are unchanged."""
from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .hub import param_path

_LOGGER = logging.getLogger(__name__)

# The effects the firmware had before the rewrite.
LEGACY_EFFECTS = {
    "SingleDotEffect": "single_dot",
    "SparkleEffect": "sparkle",
    "SpriteParticleEffect": "sprite_particle",
    "PlasmaEffect": "plasma",
    "SimpleSpriteEffect": "simple_sprite",
    "GravityFieldEffect": "gravity_field",
    "NovaMissileEffect": "nova_missile",
    "ColorNodeEffect": "color_node",
    "SingleColorEffect": "single_color",
    "FlameEffect": "flame",
    "AuroraEffect": "aurora",
    "ThunderstormEffect": "thunderstorm",
    "RaindropEffect": "raindrop",
    "LavaLampEffect": "lava_lamp",
    "FireflyEffect": "firefly",
    "MeteorEffect": "meteor",
}
OLD_ID = re.compile(r"^(?P<head>.*-screen\d+)-(?P<effect>\w+Effect)-(?P<path>.+)$")


@callback
def async_migrate_unique_ids(hass: HomeAssistant, entry: ConfigEntry, effects: list[dict]) -> None:
    """Renames the unique ids of this entry's parameter entities. A parameter the firmware no longer
    has keeps its old id: its entity stays in the list as no longer provided, to be removed by hand."""
    registry = er.async_get(hass)
    by_id = {e["id"]: e for e in effects}
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        m = OLD_ID.match(item.unique_id)
        effect = by_id.get(LEGACY_EFFECTS.get(m["effect"], "")) if m else None
        if effect is None:
            continue
        param = next((p for p in effect.get("params", []) if param_path(p) == m["path"]), None)
        if param is None:
            continue
        new_id = f"{m['head']}-{effect['id']}-{param['id']}"
        if registry.async_get_entity_id(item.domain, item.platform, new_id):
            continue  # both exist: leave them alone
        _LOGGER.debug("Lichtschnitzel: %s becomes %s", item.unique_id, new_id)
        registry.async_update_entity(item.entity_id, new_unique_id=new_id)
