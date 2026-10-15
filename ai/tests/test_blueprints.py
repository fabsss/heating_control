import json
from pathlib import Path

from tests.yaml_blueprint import load_blueprint

ROOT = Path(__file__).resolve().parents[2]


def test_heating_control_has_the_optional_ki_toggle():
    blueprint = load_blueprint(ROOT / "heating_control.yaml")
    assert blueprint["blueprint"]["name"] == "Heizungssteuerung (V4.29)"
    field = blueprint["blueprint"]["input"]["ki_rules_toggle"]
    assert field["default"] == []
    assert field["selector"] == {"entity": {"domain": "input_boolean", "multiple": False}}


def test_heating_control_logic_does_not_use_the_toggle():
    blueprint = load_blueprint(ROOT / "heating_control.yaml")
    body = {key: value for key, value in blueprint.items() if key != "blueprint"}
    dumped = json.dumps(body, default=str)
    assert "ki_rules_toggle" not in dumped
    assert len(blueprint["trigger"]) == 9
    assert blueprint["mode"] == "queued"
