import pytest

from collector.config import ConfigError, load_config


def write(tmp_path, text):
    path = tmp_path / "c.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_config_loads(cfg):
    assert cfg.label == "KI_DB_Sync"
    assert cfg.flush_interval_s == 30
    assert cfg.registry_sync_interval_s == 600
    assert cfg.ha_url == "ws://ha.test/api/websocket"
    assert cfg.database == ":memory:"
    assert cfg.exceptions["sensor.fabians_s22_presence_tracker_area_last_seen"]["debounce_s"] == 60


def test_missing_url_raises(tmp_path):
    with pytest.raises(ConfigError, match="url"):
        load_config(write(tmp_path, "label: X\n"), env={})


def test_missing_label_raises(tmp_path):
    with pytest.raises(ConfigError, match="label"):
        load_config(write(tmp_path, "home_assistant: {url: 'ws://x'}\n"), env={})


def test_unknown_policy_key_raises(tmp_path):
    text = "home_assistant: {url: 'ws://x'}\nlabel: X\npolicies:\n  climate: {bogus: 1}\n"
    with pytest.raises(ConfigError, match="bogus"):
        load_config(write(tmp_path, text), env={})


def test_unknown_exception_key_raises(tmp_path):
    text = "home_assistant: {url: 'ws://x'}\nlabel: X\nexceptions:\n  sensor.a: {nope: 1}\n"
    with pytest.raises(ConfigError, match="nope"):
        load_config(write(tmp_path, text), env={})
