from datetime import datetime, timedelta, timezone

from collector.filtering import ChangeFilter
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def climate_rule() -> EntityRule:
    return EntityRule(
        entity_id="climate.bt",
        kind="climate",
        domain="heating",
        attrs=("temperature", "preset_mode", "hvac_action", "current_temperature", "window_open"),
        change_attrs=("temperature", "preset_mode", "window_open"),
        delta_attrs={"current_temperature": 0.5},
    )


def climate(seconds, state="heat", **attrs) -> Change:
    base = {"temperature": 17, "preset_mode": "none", "hvac_action": "idle",
            "current_temperature": 20.0, "window_open": False, "heating_power": 0.01}
    base.update(attrs)
    return Change(at(seconds), "climate.bt", state, base)


def test_first_change_is_always_stored_and_trimmed_to_whitelist():
    f = ChangeFilter(climate_rule())
    out = f.feed(climate(0))
    assert len(out) == 1
    assert "heating_power" not in out[0].attrs
    assert out[0].attrs["temperature"] == 17


def test_unchanged_repeat_is_dropped():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0))
    assert f.feed(climate(5)) == []


def test_setpoint_preset_mode_and_window_changes_are_stored():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0))
    assert len(f.feed(climate(10, temperature=21))) == 1
    assert len(f.feed(climate(20, temperature=21, preset_mode="comfort"))) == 1
    assert len(f.feed(climate(30, temperature=21, preset_mode="comfort", window_open=True))) == 1
    assert len(f.feed(climate(40, state="off", temperature=21, preset_mode="comfort", window_open=True))) == 1


def test_hvac_action_alone_does_not_trigger_storing():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0))
    assert f.feed(climate(10, hvac_action="heating")) == []


def test_current_temperature_drift_accumulates_against_last_stored_value():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0, current_temperature=20.0))
    assert f.feed(climate(10, current_temperature=20.3)) == []
    assert len(f.feed(climate(20, current_temperature=20.6))) == 1
    assert f.feed(climate(30, current_temperature=20.9)) == []


def numeric_rule(delta=0.5, heartbeat=None) -> EntityRule:
    return EntityRule(entity_id="sensor.t", kind="env", domain="shared",
                      numeric_state_delta=delta, heartbeat_s=heartbeat)


def num(seconds, state) -> Change:
    return Change(at(seconds), "sensor.t", state)


def test_numeric_sensor_threshold_and_boundary():
    f = ChangeFilter(numeric_rule(0.5))
    assert len(f.feed(num(0, "19.1"))) == 1
    assert f.feed(num(1, "19.4")) == []
    assert len(f.feed(num(2, "19.6"))) == 1   # genau 0,5 Abstand zählt trotz Gleitkomma-Fehler


def test_numeric_sensor_stores_unavailable_transitions():
    f = ChangeFilter(numeric_rule(0.5))
    f.feed(num(0, "19.0"))
    assert len(f.feed(num(1, "unavailable"))) == 1
    assert f.feed(num(2, "unavailable")) == []
    assert len(f.feed(num(3, "19.1"))) == 1


def test_heartbeat_stores_unchanged_value_after_interval():
    f = ChangeFilter(numeric_rule(0.5, heartbeat=1800))
    f.feed(num(0, "19.0"))
    assert f.feed(num(1799, "19.0")) == []
    assert len(f.feed(num(1800, "19.0"))) == 1


def test_every_change_policy_stores_each_state_change_only():
    rule = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
    f = ChangeFilter(rule)
    assert len(f.feed(Change(at(0), "input_boolean.x", "off"))) == 1
    assert f.feed(Change(at(1), "input_boolean.x", "off")) == []
    assert len(f.feed(Change(at(2), "input_boolean.x", "on"))) == 1


def test_filter_can_be_seeded_with_last_stored_change():
    f = ChangeFilter(numeric_rule(0.5), last=num(0, "19.0"))
    assert f.feed(num(5, "19.2")) == []


def debounce_rule() -> EntityRule:
    return EntityRule(entity_id="sensor.room", kind="presence", domain="shared", debounce_s=60)


def room(seconds, state) -> Change:
    return Change(at(seconds), "sensor.room", state)


def test_debounce_suppresses_flapping_room_changes():
    f = ChangeFilter(debounce_rule())
    assert f.feed(room(0, "Büro")) == []        # noch nicht bestätigt
    assert f.flush_pending()[0].state == "Büro"  # erster Wert wird bestätigt (Ende des Stroms)
    assert f.feed(room(100, "Flur")) == []
    assert f.feed(room(110, "Büro")) == []       # zurück innerhalb von 60 s -> Flur verworfen
    assert f.tick(at(300)) == []                 # Büro ist unverändert, nichts zu speichern


def test_debounce_confirms_room_held_long_enough_on_next_event():
    f = ChangeFilter(debounce_rule())
    f.feed(room(0, "Büro"))
    f.flush_pending()
    f.feed(room(100, "Flur"))
    out = f.feed(room(170, "Küche"))             # Flur hielt 70 s -> bestätigt
    assert [c.state for c in out] == ["Flur"]


def test_debounce_confirms_via_tick():
    f = ChangeFilter(debounce_rule())
    f.feed(room(0, "Büro"))
    assert f.tick(at(59)) == []
    assert [c.state for c in f.tick(at(61))] == ["Büro"]
    assert f.tick(at(200)) == []
