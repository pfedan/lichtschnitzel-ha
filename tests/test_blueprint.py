"""The blueprint in blueprints/ is valid and, filled in, a valid automation."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA, ValidationStatus, async_validate_config_item
from homeassistant.components.blueprint import models
from homeassistant.core import HomeAssistant
from homeassistant.util import yaml as yaml_util

BLUEPRINT = Path(__file__).parent.parent / "blueprints" / "automation" / "lichtschnitzel" / "indicator_from_state.yaml"


async def test_indicator_blueprint(hass: HomeAssistant) -> None:
    bp = models.Blueprint(yaml_util.load_yaml(BLUEPRINT), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    filled = models.BlueprintInputs(
        bp,
        {
            "use_blueprint": {
                "path": "lichtschnitzel/indicator_from_state.yaml",
                "input": {"source": "binary_sensor.fenster", "screens": ["light.lampe_bad"], "indicator_id": "fenster"},
            }
        },
    )
    filled.validate()
    config = filled.async_substitute()
    assert config["actions"][0]["then"][0]["data"]["id"] == "fenster"
    assert config["actions"][0]["then"][0]["data"]["rgb_color"] == [0, 120, 255]
    validated = await async_validate_config_item(hass, "test", config)
    assert validated is not None and validated.validation_status == ValidationStatus.OK, getattr(validated, "validation_error", None)
