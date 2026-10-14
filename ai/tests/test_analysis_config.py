import pytest

from analysis.config import AnalysisConfigError, load_analysis_config
from tests.conftest import ROOT

MINIMAL = """
model: test/model
provider: {zdr: true, data_collection: deny}
price_per_mtok: {input: 1.0, output: 2.0}
context:
  flat_state: input_select.f
  in_bed: input_boolean.b
  office_day: binary_sensor.o
  outside_temperature: sensor.t
"""


def write(tmp_path, text):
    path = tmp_path / "a.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_config_loads():
    cfg = load_analysis_config(ROOT / "config" / "analysis.yaml")
    assert cfg.provider["zdr"] is True
    assert cfg.timezone == "Europe/Berlin"
    assert cfg.max_candidates == 20
    assert cfg.max_cost_usd == 2.0
    assert cfg.backtest.min_faelle == 5 and cfg.backtest.min_wochen == 3
    assert cfg.backtest.match_window_min == 30 and cfg.backtest.contrary_window_min == 60
    assert cfg.merge_interventions_s == 60
    assert cfg.clusters.min_size == 3 and cfg.clusters.window_min == 90
    assert cfg.room_names["office"] == "Bastelzimmer"
    assert cfg.context["flat_state"] == "input_select.flat_occupation_state"


def test_exclusion_dates_are_utc_aware():
    cfg = load_analysis_config(ROOT / "config" / "analysis.yaml")
    (exclusion,) = cfg.exclusions
    assert exclusion.room == "ee532a5799b5440691fdcc87e3354f9b"
    assert exclusion.start.utcoffset().total_seconds() == 0
    assert exclusion.end is not None and exclusion.end > exclusion.start


def test_defaults_for_minimal_file(tmp_path):
    cfg = load_analysis_config(write(tmp_path, MINIMAL))
    assert cfg.timezone == "Europe/Berlin"
    assert cfg.max_candidates == 20
    assert cfg.exclusions == ()
    assert cfg.clusters.weekday_groups["werktage"] == (0, 1, 2, 3, 4)


def test_zdr_must_stay_enabled(tmp_path):
    text = MINIMAL.replace("zdr: true", "zdr: false")
    with pytest.raises(AnalysisConfigError, match="zdr"):
        load_analysis_config(write(tmp_path, text))


def test_missing_model_raises(tmp_path):
    with pytest.raises(AnalysisConfigError, match="model"):
        load_analysis_config(write(tmp_path, MINIMAL.replace("model: test/model\n", "")))


def test_unknown_top_level_key_raises(tmp_path):
    with pytest.raises(AnalysisConfigError, match="bogus"):
        load_analysis_config(write(tmp_path, MINIMAL + "bogus: 1\n"))


def test_missing_context_entity_raises(tmp_path):
    text = MINIMAL.replace("  in_bed: input_boolean.b\n", "")
    with pytest.raises(AnalysisConfigError, match="in_bed"):
        load_analysis_config(write(tmp_path, text))


def test_unknown_timezone_raises(tmp_path):
    with pytest.raises(AnalysisConfigError, match="Zeitzone"):
        load_analysis_config(write(tmp_path, MINIMAL + "timezone: Mars/Olympus\n"))


def test_unknown_backtest_key_raises(tmp_path):
    with pytest.raises(AnalysisConfigError, match="nope"):
        load_analysis_config(write(tmp_path, MINIMAL + "backtest: {nope: 1}\n"))


def test_weekday_group_out_of_range_raises(tmp_path):
    with pytest.raises(AnalysisConfigError, match="weekday_groups"):
        load_analysis_config(write(tmp_path, MINIMAL + "clusters:\n  weekday_groups: {x: [7]}\n"))


def test_env_overrides_model_and_prices(tmp_path):
    env = {"OPENROUTER_MODEL": "vendor/other", "OPENROUTER_PRICE_INPUT": "0.5", "OPENROUTER_PRICE_OUTPUT": "2.5"}
    cfg = load_analysis_config(write(tmp_path, MINIMAL), env=env)
    assert cfg.model == "vendor/other"
    assert (cfg.price_input, cfg.price_output) == (0.5, 2.5)


def test_env_model_replaces_missing_model_in_file(tmp_path):
    text = MINIMAL.replace("model: test/model\n", "")
    cfg = load_analysis_config(write(tmp_path, text), env={"OPENROUTER_MODEL": "vendor/x"})
    assert cfg.model == "vendor/x"


def test_empty_env_values_are_ignored(tmp_path):
    cfg = load_analysis_config(write(tmp_path, MINIMAL), env={"OPENROUTER_MODEL": "  ", "OPENROUTER_PRICE_INPUT": ""})
    assert cfg.model == "test/model" and cfg.price_input == 1.0


def test_invalid_env_price_raises(tmp_path):
    with pytest.raises(AnalysisConfigError, match="OPENROUTER_PRICE_INPUT"):
        load_analysis_config(write(tmp_path, MINIMAL), env={"OPENROUTER_PRICE_INPUT": "teuer"})
