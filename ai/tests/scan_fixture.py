from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from collector.db import Database
from collector.models import Change, EntityRule
from tests.analysis_fixture import FIRST_MONDAY, ROOM, local_utc

__all__ = ["ROOM", "build_scan_db", "after_arrival"]


def after_arrival(offsets: Callable[[int], list[int]], arrival: str = "17:00"):
    """Eingriffe an Werktagen, `offsets(n)` Minuten nach der Ankunft."""
    def make(n: int, day: date) -> list[datetime]:
        if day.weekday() >= 5:
            return []
        return [local_utc(day, arrival) + timedelta(minutes=minutes) for minutes in offsets(n)]
    return make


def build_scan_db(db: Database, interventions: Callable[[int, date], list[datetime]], *, weeks: int = 6,
                  arrival: str = "17:00", alarm_time: str | None = None) -> None:
    """Sechs Wochen: Svenja ist werktags von 07:00 bis zur Ankunft weg und bis 22:30 zu Hause.

    `interventions(n, day)` liefert die UTC-Zeitpunkte der Eingriffe von Svenja an Tag n.
    Mit `alarm_time` gibt es einen Wecker, der jeden Abend um 20:00 auf die Weckzeit des Folgetags gestellt wird.
    """
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode")), ROOM, now)
    db.upsert_entity(EntityRule("person.svenja", "person", "shared"), None, now)
    db.upsert_user("u-svenja", "person.svenja", "Svenja")
    if alarm_time:
        db.upsert_entity(EntityRule("sensor.next_alarm", "env", "shared"), None, now)
    changes: list[Change] = []
    for n in range(weeks * 7):
        day = FIRST_MONDAY + timedelta(days=n)
        changes.append(Change(local_utc(day, "07:00"), "person.svenja", "not_home"))
        changes.append(Change(local_utc(day, "08:00"), "climate.bt", "heat",
                              {"temperature": 17, "preset_mode": "eco"}, f"auto{n}", None, f"parent{n}"))
        if day.weekday() < 5:
            changes.append(Change(local_utc(day, arrival), "person.svenja", "home"))
            changes.append(Change(local_utc(day, "22:30"), "person.svenja", "not_home"))
        if alarm_time:
            changes.append(Change(local_utc(day - timedelta(days=1), "20:00"), "sensor.next_alarm",
                                  local_utc(day, alarm_time).isoformat()))
        for k, ts in enumerate(interventions(n, day)):
            changes.append(Change(ts, "climate.bt", "heat", {"temperature": 21 + k, "preset_mode": "comfort"},
                                  f"man{n}_{k}", "u-svenja", None))
    db.insert_changes(changes, "live")
