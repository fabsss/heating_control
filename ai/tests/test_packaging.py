from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_image_contains_the_execution_package():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY execution ./execution" in dockerfile


def test_compose_has_a_second_service_for_the_execution():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["execution"]
    assert service["image"] == "home-ai-collector"
    assert service["command"] == ["python", "-m", "execution", "serve"]
    env = service["environment"]
    for name in ("HA_URL", "HA_WRITE_TOKEN", "MQTT_HOST", "MQTT_PORT", "EXECUTION_CONFIG", "ANALYSIS_CONFIG",
                 "DB_PATH", "OPENROUTER_API_KEY"):
        assert name in env
    assert "/mnt/data/nobackup/homeassistant/ai:/data" in service["volumes"]
    assert "HA_WRITE_TOKEN" not in compose["services"]["collector"]["environment"]


def test_env_example_names_the_new_variables():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("HA_WRITE_TOKEN", "MQTT_HOST", "MQTT_USER", "MQTT_PASSWORD"):
        assert name in text


def test_requirements_list_the_broker_client():
    assert "aiomqtt" in (ROOT / "requirements.txt").read_text(encoding="utf-8")
