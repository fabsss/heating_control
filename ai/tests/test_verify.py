import pytest

from collector.backfill import backfill_ha_db
from collector.models import EntityInfo
from collector.rules import resolve_rule
from collector.verify import verify_logged_events
from tests.ha_fixture import CTX, USER_FABIAN, USER_SVENJA, build_ha_db

ATTRS = {"temperature": 17, "preset_mode": "none", "hvac_action": "idle",
         "current_temperature": 20.0, "window_open": False}


def climate_row(ts, state="heat", temperature=17, **extra):
    return {"entity_id": "climate.bt", "state": state, "ts": ts,
            "attrs": {**ATTRS, "temperature": temperature}, **extra}


def logged(ts, entity_id, change_types, actor_id):
    return {"ts": ts, "data": {"entity_id": entity_id, "change_types": change_types, "actor_id": actor_id}}


@pytest.fixture
def prepared(cfg, db, tmp_path):
    ha = build_ha_db(
        tmp_path / "home-assistant_v2.db",
        [
            climate_row(1000.0),
            climate_row(2000.0, temperature=21, ctx=CTX, user=USER_FABIAN),
            climate_row(3000.0, state="off", temperature=21, ctx=CTX, user=USER_SVENJA),
        ],
        events=[
            logged(2000.5, "climate.bt", ["setpoint"], USER_FABIAN),     # passt
            logged(3000.2, "climate.bt", ["hvac_mode"], USER_SVENJA),    # passt
            logged(4000.0, "climate.bt", ["setpoint"], USER_FABIAN),     # keine passende Zeile
            logged(5000.0, "climate.other", ["setpoint"], USER_FABIAN),  # Entität nicht aufgezeichnet
            logged(6000.0, "climate.bt", ["setpoint"], "unknown"),       # nicht manuell
        ],
    )
    rules = {"climate.bt": resolve_rule(EntityInfo("climate.bt"), cfg)}
    backfill_ha_db(ha, db, rules, {"climate.bt": "buero"})
    return ha


def test_report_counts_matches_untracked_and_unmatched(db, prepared):
    report = verify_logged_events(prepared, db)
    assert report.logged_manual == 4
    assert report.matched == 2
    assert report.untracked_entity == 1
    assert [item["entity_id"] for item in report.unmatched] == ["climate.bt"]
    assert report.extra_manual == 0
    assert report.match_rate == pytest.approx(2 / 3)


def test_wrong_actor_does_not_match(db, prepared, tmp_path, cfg):
    other = build_ha_db(
        tmp_path / "other.db",
        [climate_row(1000.0), climate_row(2000.0, temperature=21, ctx=CTX, user=USER_FABIAN)],
        events=[logged(2000.5, "climate.bt", ["setpoint"], USER_SVENJA)],
    )
    report = verify_logged_events(other, db)
    assert (report.logged_manual, report.matched) == (1, 0)


def test_tolerance_is_respected(db, prepared, tmp_path):
    late = build_ha_db(
        tmp_path / "late.db",
        [climate_row(1000.0), climate_row(2000.0, temperature=21, ctx=CTX, user=USER_FABIAN)],
        events=[logged(2060.0, "climate.bt", ["setpoint"], USER_FABIAN)],
    )
    assert verify_logged_events(late, db, tolerance_s=5).matched == 0
    assert verify_logged_events(late, db, tolerance_s=120).matched == 1


def test_extra_manual_actions_are_counted(db, prepared, tmp_path):
    none_logged = build_ha_db(tmp_path / "none.db", [climate_row(1000.0)], events=[])
    report = verify_logged_events(none_logged, db)
    assert report.logged_manual == 0
    assert report.extra_manual == 2
    assert report.match_rate == 1.0
