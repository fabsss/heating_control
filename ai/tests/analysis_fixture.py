from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from collector.db import Database
from collector.models import Change, EntityRule

BERLIN = ZoneInfo("Europe/Berlin")
ROOM = "r_living"
FIRST_MONDAY = date(2026, 3, 16)     # die sechs Wochen enthalten den Wechsel auf Sommerzeit (29.03.)
WEEKS = 6


def local_utc(day: date, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BERLIN).astimezone(timezone.utc)


def build_habit_db(db: Database, *, weeks: int = WEEKS, with_noise: bool = True) -> None:
    """Sechs Wochen mit einer bekannten Gewohnheit.

    Jeden Werktag kommt Svenja um 17:20 (lokale Zeit) heim und stellt um 17:32 im Wohnzimmer
    den Sollwert auf 21 (Preset comfort). Um 08:00 setzt die Automatik auf 17 (eco).
    Zwei Eingriffe von Fabian samstags um 12:00 (Sollwert 19) sind Rauschen.
    Erwartet: 30 Eingriffe mit Sollwert 21 und 2 mit Sollwert 19.
    """
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode")),
                     ROOM, now)
    for entity_id in ("person.fabian", "person.svenja"):
        db.upsert_entity(EntityRule(entity_id, "person", "shared"), None, now)
    db.upsert_user("u-fabian", "person.fabian", "Fabian")
    db.upsert_user("u-svenja", "person.svenja", "Svenja")
    changes: list[Change] = []
    for n in range(weeks * 7):
        day = FIRST_MONDAY + timedelta(days=n)
        changes.append(Change(local_utc(day, "07:00"), "person.svenja", "not_home"))
        changes.append(Change(local_utc(day, "08:00"), "climate.bt", "heat",
                              {"temperature": 17, "preset_mode": "eco"}, f"auto{n}", None, f"parent{n}"))
        if day.weekday() < 5:
            changes.append(Change(local_utc(day, "17:20"), "person.svenja", "home"))
            changes.append(Change(local_utc(day, "17:32"), "climate.bt", "heat",
                                  {"temperature": 21, "preset_mode": "comfort"}, f"man{n}", "u-svenja", None))
            changes.append(Change(local_utc(day, "22:30"), "person.svenja", "not_home"))
        elif with_noise and day.weekday() == 5 and n in (5, 12):
            changes.append(Change(local_utc(day, "12:00"), "climate.bt", "heat",
                                  {"temperature": 19, "preset_mode": "none"}, f"noise{n}", "u-fabian", None))
    db.insert_changes(changes, "live")
