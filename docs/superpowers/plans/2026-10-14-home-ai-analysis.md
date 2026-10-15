# KI-Analyse (Teilprojekt 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Kommando `python -m analysis` erzeugt aus den manuellen Heizungs-Eingriffen und ihrem Kontext Regelkandidaten (Zeit- oder Ereignisauslöser), lässt ein LLM über OpenRouter (ZDR) Hypothesen formulieren, prüft jeden Kandidaten deterministisch gegen die Historie und legt alles in `home_ai.sqlite` ab. Zwei umschaltbare Modi (`weekly`, `clusters`) sind vergleichbar.

**Architecture:** Neues Paket `ai/analysis/` neben `collector/` (der Collector bleibt unverändert). Stufen: Verdichtung (je Modus) → Pseudonymisierung → LLM-Aufruf → Validierung gegen ein geschlossenes Vokabular → Backtest ohne LLM → Speichern in einer Transaktion. Zeitreihen werden aus `state_changes` in Speicher geladen (`Timeline`), Eingriffe aus der View `manual_actions`.

**Tech Stack:** Python 3.12, asyncio, aiohttp (OpenRouter-Client), PyYAML, sqlite3, zoneinfo, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-13-home-ai-analysis-design.md` (Basis: `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md`, Abschnitt 14)

## Global Constraints

Diese Werte stammen aus der Spezifikation und gelten für jede Aufgabe:

- Paket `ai/analysis/`, Konfiguration `ai/config/analysis.yaml`, Schema `ai/sql/analysis.sql`; der Collector wird nicht verändert.
- Zwei Modi: `weekly` (Raster je Raum, Wochentag, Stunde) und `clusters` (Eingriffs-Cluster mit Kontext). Verdichtung unterscheidet sich je Modus, Pseudonymisierung, Validierung, Backtest und Speicherung sind identisch.
- Ein **Eingriff** ist eine Zeile der View `manual_actions` mit `actor_class = 'manual'`; Zeilen derselben Entität innerhalb von **60 Sekunden** zählen als ein Eingriff. Nur Eingriffe mit Benutzerkontext sind Lernsignal.
- Wochentage und Uhrzeiten in lokaler Zeit (**Europe/Berlin**), gespeichert wird UTC. Wochentage 0 = Montag … 6 = Sonntag.
- Pseudonymisierung: Personen als `person_1`, `person_2` (stabil nach sortierter Entity-ID), Räume im Klartext, nur verdichtete Daten; im gesendeten Text steht kein Klarname einer Person, keine Entity-ID und keine Benutzer-ID.
- LLM: OpenRouter, **Zero Data Retention bei jedem Aufruf erzwungen** (ohne passenden Anbieter bricht der Lauf ab); Schlüssel nur aus `OPENROUTER_API_KEY`; niedrige Temperatur; höchstens **20 Kandidaten** je Lauf; Kostenlimit je Lauf **2 USD** (Vorschlag); ungültige Antwort: genau **ein** Wiederholungsversuch mit der Fehlermeldung.
- Vokabular geschlossen. Zeit-Auslöser: Wochentage + Uhrzeit. Ereignisse: `person_arrives`, `person_leaves`, `flat_state`, `bed_in`, `bed_out`, `window_closed`, `office_day_start`. Bedingungen: `weekdays`, `time_window`, `outside_temp`, `person_home`. Aktion: `preset` oder `setpoint`. Unbekanntes ergibt Status `nicht_pruefbar`, nie einen Abbruch.
- Backtest (alle Werte in `analysis.yaml`, Vorschläge): Fenster **30 Minuten**, Gegenteil innerhalb **60 Minuten**, Mindestzahl **5 Fälle in 3 Wochen**, Cluster ab **3 Eingriffen** und **±45 Minuten** (Fensterbreite 90 Minuten). Status: `bestanden`, `zu_wenig_belege`, `abgelehnt`, `nicht_pruefbar`.
- Kennzahlen je Kandidat: `abdeckung`, `fehlausloesungen`, `faelle`, `wochen` (zusätzlich `ausloesungen`, `eingriffe_gesamt`).
- Tabellen: `analysis_runs`, `candidates`, `backtest_results`, `pseudonyms`. Ein Lauf schreibt in einer Transaktion; bei Fehler bleibt nichts Halbes zurück, der Lauf wird als `fehlgeschlagen` vermerkt.
- Rohdaten bleiben lokal; nur das Ergebnis der Verdichtung geht an das Modell. Kandidaten sind nur Vorschläge, nichts im Haus ändert sich durch Teil 2.
- Befehle im Plan gelten für Git Bash unter Windows (`.venv/Scripts/python`); unter Linux `.venv/bin/python`. Pfade sind relativ zum Repository-Wurzelverzeichnis, `cd ai` steht jeweils im Befehl.

## Review Focus

Fünf Eingaben beziehungsweise Fehlerfälle, die die Spezifikation voraussetzt, aber nicht ausdrücklich nennt. Jede Zeile hat einen Test in der genannten Aufgabe.

1. **Antwort des Modells in Markdown-Zäunen oder ohne `candidates`-Schlüssel** wird toleriert beziehungsweise genau einmal nachgefragt; danach bricht der Lauf ab und es bleibt kein halber Lauf in der Datenbank. → Aufgabe 7 (`test_parse_candidates_json_strips_markdown_fences`, `test_second_invalid_answer_raises`) und Aufgabe 8 (`test_failed_run_leaves_no_candidates`).
2. **Kandidat mit unbekanntem Raum, Auslöser, Preset oder Unsinnswert** (Sollwert 99 °C, Uhrzeit `25:99`, Person `person_9`) wird als `nicht_pruefbar` gespeichert, nie als Abbruch. → Aufgabe 5 (`test_parse_candidate_marks_garbage_as_untestable`).
3. **Fehlende Kontextdaten** (kein Wohnungszustand, keine Fensterkontakte im Raum, kein Außensensor) lassen Verdichtung und Backtest nicht abstürzen; betroffene Kandidaten sind `nicht_pruefbar`. → Aufgabe 4 (`test_snapshot_tolerates_missing_context`) und Aufgabe 6 (`test_missing_window_sensors_make_candidate_untestable`).
4. **Sommerzeitwechsel:** Zeitauslöser und Wochentagsmuster verschieben sich nicht um eine Stunde. → Aufgabe 6 (`test_time_trigger_follows_local_time_across_dst`) und Aufgabe 8 (`test_known_habit_is_found_across_dst_change`).
5. **Klarnamen im Prompt:** Namen aus `users`, Entity-IDs der Personen und Benutzer-IDs tauchen weder im gesendeten Text noch in gespeicherten Prompts auf. → Aufgabe 2 (`test_leaks_finds_names_ids_and_user_ids`) und Aufgabe 8 (`test_prompt_contains_no_real_names`).

---

## Dateistruktur

```
ai/
  analysis/
    __init__.py
    __main__.py             # python -m analysis ...
    models.py               # Intervention, Candidate, Status-Konstanten
    config.py               # AnalysisConfig, load_analysis_config
    store.py                # Store: Tabellen analysis_runs/candidates/backtest_results/pseudonyms
    pseudonym.py            # Pseudonyms, build_pseudonyms
    timeline.py             # Timeline, load_timeline
    interventions.py        # load_interventions, excluded
    context.py              # ContextProvider: Kontext zu einem Zeitpunkt
    features.py             # build_weekly, build_clusters, known_presets
    vocabulary.py           # geschlossenes Vokabular, parse_candidate(s)
    backtest.py             # Backtester, BacktestResult
    prompts.py              # Systemprompt, build_messages
    llm.py                  # OpenRouter-Client, ZDR, Kosten, Wiederholung
    candidates.py           # signature, describe
    runner.py               # prepare, run_analysis
    cli.py                  # run | compare | list | backtest
  config/
    analysis.yaml
  sql/
    analysis.sql
  tests/
    analysis_fixture.py     # Datenbank mit einer bekannten Gewohnheit
    fake_openrouter.py      # OpenRouter-Attrappe
    test_analysis_config.py, test_store.py, test_pseudonym.py, test_timeline.py,
    test_interventions.py, test_features.py, test_vocabulary.py, test_backtest.py,
    test_llm.py, test_runner.py, test_analysis_cli.py
```

Zusätzlich zu den Dateien der Spezifikation (Abschnitt 13): `models.py`, `timeline.py`, `interventions.py`, `context.py`, `prompts.py`, `runner.py`. Sie trennen Verantwortlichkeiten, die die Spezifikation unter `features.py`, `llm.py` und `cli.py` zusammenfasst.

**Bewusste Abweichungen von der Spezifikation:**
1. Spalten `trigger_json`, `conditions_json`, `action_json` statt `trigger`, `conditions`, `action`, weil `TRIGGER` ein SQL-Schlüsselwort ist.
2. Zusätzliche Spalte `candidates.note` (Grund für `nicht_pruefbar`, `zu_wenig_belege`, `abgelehnt`) und `analysis_runs.error`.
3. Räume stehen in der Datenbank als `area_id` (z. B. `office` für das Bastelzimmer). Die Klartextnamen für das Modell kommen aus `room_names` in `analysis.yaml`.

---

### Task 1: Gerüst, Konfiguration, Modelle, Schema und Store

**Files:**
- Create: `ai/analysis/__init__.py`, `ai/analysis/models.py`, `ai/analysis/config.py`, `ai/analysis/store.py`
- Create: `ai/config/analysis.yaml`, `ai/sql/analysis.sql`
- Create: `ai/tests/test_analysis_config.py`, `ai/tests/test_store.py`
- Modify: `ai/tests/conftest.py` (Fixtures `acfg`, `store`)

**Interfaces:**
- Consumes: `collector.db.Database` (Attribut `conn`), `collector.models.parse_utc`, `to_utc_iso`.
- Produces:
  - `analysis.models`: Konstanten `STATUS_OPEN = "offen"`, `STATUS_PASSED = "bestanden"`, `STATUS_FEW = "zu_wenig_belege"`, `STATUS_REJECTED = "abgelehnt"`, `STATUS_UNTESTABLE = "nicht_pruefbar"`; `@dataclass(frozen=True) Intervention(ts: datetime, room: str, entity_id: str, kind: str, value: float | str, user_id: str | None = None)` (`kind` ist `"setpoint"` oder `"preset"`); `@dataclass Candidate(room: str, trigger: dict, action: dict, conditions: list[dict] = [], hypothesis: str = "", status: str = "offen", note: str = "", id: int | None = None, run_id: int | None = None)`.
  - `analysis.config`: `AnalysisConfigError`, `Exclusion(room, start, end)`, `BacktestParams`, `ClusterParams`, `AnalysisConfig` (Felder siehe Code), `load_analysis_config(path) -> AnalysisConfig`.
  - `analysis.store.Store(db)`: `commit()`, `rollback()`, `start_run(mode, model, params, now) -> int` (committet), `finish_run(run_id, status, now, *, error=None, prompt_hash=None, prompt_text=None, response_text=None, tokens_in=None, tokens_out=None, cost_usd=None)` (committet), `add_candidate(run_id, candidate) -> int` (committet nicht), `set_candidate_status(candidate_id, status, note=None)` (committet nicht), `save_results(candidate_id, metrics: dict[str, float])` (committet nicht), `get_candidate(candidate_id) -> Candidate`, `list_candidates(run_id=None, status=None) -> list[Candidate]`, `results(candidate_id) -> dict[str, float]`, `get_run(run_id) -> sqlite3.Row | None`, `list_runs() -> list[sqlite3.Row]`, `save_pseudonyms(mapping: dict[str, tuple[str, str]])` (committet nicht), `load_pseudonyms() -> dict[str, str]`.
  - Fixtures `acfg` (lädt `ai/config/analysis.yaml`) und `store` (`Store(db)`).

- [ ] **Step 1: Konfigurationsdatei anlegen**

`ai/config/analysis.yaml`:

```yaml
# Regeln der Analyse (Teilprojekt 2). Enthält keine Zugangsdaten:
# Der Schlüssel kommt aus der Umgebungsvariable OPENROUTER_API_KEY.
model: "anthropic/claude-sonnet-4.5"      # OpenRouter-Modell-ID; vor dem ersten Lauf gegen die Modellliste prüfen
openrouter_url: "https://openrouter.ai/api/v1/chat/completions"
provider:                                  # Datenschutz: nur Anbieter ohne Datenspeicherung (zdr darf nicht false werden)
  zdr: true
  data_collection: "deny"
price_per_mtok: {input: 3.0, output: 15.0}   # USD je Million Token, nur für die Kostenschätzung vor dem Aufruf
max_output_tokens: 8000
max_cost_usd: 2.0                          # Kostenlimit je Lauf
temperature: 0.2
request_timeout_s: 180
retries: 3                                 # Wiederholungen bei Netzfehlern
retry_backoff_s: 2.0
max_candidates: 20
timezone: "Europe/Berlin"
min_interventions: 20                      # darunter endet der Lauf ohne Aufruf
merge_interventions_s: 60

# area_id (wie in room_map) -> Klartextname für das Modell
room_names:
  a05f1cd02cf34142af915331271a6986: "Bad"
  office: "Bastelzimmer"
  flur: "Flur"
  12f2b49ac8b049e98d4a10a1fe8766fb: "Küche"
  ee532a5799b5440691fdcc87e3354f9b: "Schlafzimmer"
  25752d3793e84768941596a2c9579f36: "Wohnzimmer"

# Zeiträume, die nicht gewertet werden (keine echte Regelung)
exclusions:
  # Schlafzimmer: Homematic-Thermostate am 2026-03-16 deaktiviert, neue Thermostate erst später.
  # Das Ende ist das Datum des Thermostattauschs und beim Einrichten anzupassen.
  - {room: ee532a5799b5440691fdcc87e3354f9b, from: "2026-03-16T00:00:00+00:00", to: "2026-09-15T00:00:00+00:00"}

# Entitäten, aus denen der Kontext zu einem Zeitpunkt gelesen wird
context:
  flat_state: input_select.flat_occupation_state
  in_bed: input_boolean.someone_in_bed
  office_day: binary_sensor.office_day_template
  outside_temperature: sensor.environment_sensor_outside_temperature

clusters:
  min_size: 3
  window_min: 90                           # Breite des Zeitfensters (±45 Minuten)
  weekday_groups:
    werktage: [0, 1, 2, 3, 4]
    wochenende: [5, 6]

backtest:
  match_window_min: 30
  contrary_window_min: 60
  setpoint_tolerance: 0.5
  min_faelle: 5
  min_wochen: 3
  min_abdeckung: 0.3
  max_fehlausloesungen: 0.4
```

- [ ] **Step 2: Schema anlegen**

`ai/sql/analysis.sql`:

```sql
CREATE TABLE IF NOT EXISTS analysis_runs(
  id            INTEGER PRIMARY KEY,
  mode          TEXT NOT NULL,                 -- weekly|clusters
  model         TEXT NOT NULL,
  started_at    TEXT NOT NULL,                 -- ISO-8601 UTC
  finished_at   TEXT,
  params        TEXT NOT NULL DEFAULT '{}',    -- JSON
  status        TEXT NOT NULL,                 -- laeuft|abgeschlossen|fehlgeschlagen
  error         TEXT,
  prompt_hash   TEXT,
  prompt_text   TEXT,                          -- pseudonymisiert
  response_text TEXT,
  tokens_in     INTEGER,
  tokens_out    INTEGER,
  cost_usd      REAL
);

CREATE TABLE IF NOT EXISTS candidates(
  id              INTEGER PRIMARY KEY,
  run_id          INTEGER NOT NULL REFERENCES analysis_runs(id),
  room            TEXT NOT NULL,               -- area_id
  trigger_json    TEXT NOT NULL,
  conditions_json TEXT NOT NULL DEFAULT '[]',
  action_json     TEXT NOT NULL,
  hypothesis      TEXT,
  status          TEXT NOT NULL,               -- offen|bestanden|zu_wenig_belege|abgelehnt|nicht_pruefbar
  note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_id);

CREATE TABLE IF NOT EXISTS backtest_results(
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  metric       TEXT NOT NULL,
  value        REAL NOT NULL,
  PRIMARY KEY(candidate_id, metric)
);

CREATE TABLE IF NOT EXISTS pseudonyms(
  alias     TEXT PRIMARY KEY,                  -- person_1, person_2, ...
  entity_id TEXT NOT NULL,
  kind      TEXT NOT NULL
);
```

- [ ] **Step 3: Tests für Konfiguration schreiben**

`ai/tests/test_analysis_config.py`:

```python
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
```

- [ ] **Step 4: Tests für den Store schreiben**

`ai/tests/test_store.py`:

```python
from datetime import datetime, timezone

from analysis.models import STATUS_OPEN, STATUS_PASSED, Candidate

NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def candidate(**overrides) -> Candidate:
    base = dict(
        room="r_living",
        trigger={"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"},
        action={"type": "setpoint", "value": 21.0},
        conditions=[{"type": "outside_temp", "op": "below", "value": 8}],
        hypothesis="Feierabend",
    )
    base.update(overrides)
    return Candidate(**base)


def test_run_lifecycle(store):
    run_id = store.start_run("weekly", "m", {"von": "a"}, NOW)
    assert store.get_run(run_id)["status"] == "laeuft"
    store.finish_run(run_id, "abgeschlossen", NOW, prompt_hash="h", prompt_text="p",
                     response_text="r", tokens_in=10, tokens_out=5, cost_usd=0.01)
    row = store.get_run(run_id)
    assert (row["status"], row["tokens_in"], row["cost_usd"]) == ("abgeschlossen", 10, 0.01)
    assert row["finished_at"] == "2026-01-01T10:00:00.000000+00:00"
    assert [r["id"] for r in store.list_runs()] == [run_id]


def test_candidate_roundtrip_and_status_update(store):
    run_id = store.start_run("clusters", "m", {}, NOW)
    cid = store.add_candidate(run_id, candidate())
    store.commit()
    loaded = store.get_candidate(cid)
    assert loaded.id == cid and loaded.run_id == run_id and loaded.status == STATUS_OPEN
    assert loaded.trigger["time"] == "17:30" and loaded.conditions[0]["value"] == 8
    store.set_candidate_status(cid, STATUS_PASSED, "ok")
    store.commit()
    assert store.get_candidate(cid).status == STATUS_PASSED
    assert store.get_candidate(cid).note == "ok"


def test_results_are_replaced_not_appended(store):
    run_id = store.start_run("weekly", "m", {}, NOW)
    cid = store.add_candidate(run_id, candidate())
    store.save_results(cid, {"abdeckung": 0.5, "faelle": 3.0})
    store.save_results(cid, {"abdeckung": 0.9})
    store.commit()
    assert store.results(cid) == {"abdeckung": 0.9}


def test_list_candidates_filters(store):
    first = store.start_run("weekly", "m", {}, NOW)
    second = store.start_run("clusters", "m", {}, NOW)
    store.add_candidate(first, candidate())
    store.add_candidate(second, candidate(status=STATUS_PASSED))
    store.commit()
    assert len(store.list_candidates()) == 2
    assert len(store.list_candidates(run_id=first)) == 1
    assert [c.status for c in store.list_candidates(status=STATUS_PASSED)] == [STATUS_PASSED]


def test_rollback_discards_uncommitted_candidates_but_keeps_run(store):
    run_id = store.start_run("weekly", "m", {}, NOW)
    store.add_candidate(run_id, candidate())
    store.rollback()
    assert store.list_candidates() == []
    assert store.get_run(run_id) is not None


def test_pseudonyms_roundtrip(store):
    store.save_pseudonyms({"person_1": ("person.a", "person"), "person_2": ("person.b", "person")})
    store.commit()
    assert store.load_pseudonyms() == {"person_1": "person.a", "person_2": "person.b"}
```

- [ ] **Step 5: Fixtures ergänzen**

Ersetze `ai/tests/conftest.py` durch (bestehender Inhalt bleibt, drei Zeilen kommen dazu):

```python
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from analysis.config import load_analysis_config
from analysis.store import Store
from collector.config import load_config
from collector.db import Database
from tests.fake_hass import FakeHass

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(
        ROOT / "config" / "collector.yaml",
        env={"HA_URL": "ws://ha.test/api/websocket", "DB_PATH": ":memory:"},
    )


@pytest.fixture
def acfg():
    return load_analysis_config(ROOT / "config" / "analysis.yaml")


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return Store(db)


@pytest_asyncio.fixture
async def fake_hass():
    fake = FakeHass()
    app = web.Application()
    app.router.add_get("/api/websocket", fake.handler)
    server = TestServer(app)
    await server.start_server()
    fake.url = f"ws://127.0.0.1:{server.port}/api/websocket"
    try:
        yield fake
    finally:
        await server.close()
```

- [ ] **Step 6: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_analysis_config.py tests/test_store.py -q`
Expected: FAIL beziehungsweise Collection-Fehler mit `ModuleNotFoundError: No module named 'analysis'`.

- [ ] **Step 7: Modelle und Konfiguration implementieren**

Leere Datei `ai/analysis/__init__.py` anlegen.

`ai/analysis/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

STATUS_OPEN = "offen"
STATUS_PASSED = "bestanden"
STATUS_FEW = "zu_wenig_belege"
STATUS_REJECTED = "abgelehnt"
STATUS_UNTESTABLE = "nicht_pruefbar"


@dataclass(frozen=True)
class Intervention:
    """Ein manueller Eingriff in einen Raum (Sollwert oder Preset)."""

    ts: datetime                 # UTC
    room: str                    # area_id
    entity_id: str
    kind: str                    # "setpoint" | "preset"
    value: float | str
    user_id: str | None = None


@dataclass
class Candidate:
    room: str                    # area_id
    trigger: dict[str, Any]
    action: dict[str, Any]
    conditions: list[dict[str, Any]] = field(default_factory=list)
    hypothesis: str = ""
    status: str = STATUS_OPEN
    note: str = ""
    id: int | None = None
    run_id: int | None = None
```

`ai/analysis/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from collector.models import parse_utc

TOP_KEYS = {
    "model", "openrouter_url", "provider", "price_per_mtok", "max_output_tokens", "max_cost_usd",
    "temperature", "request_timeout_s", "retries", "retry_backoff_s", "max_candidates", "timezone",
    "min_interventions", "merge_interventions_s", "room_names", "exclusions", "context", "clusters",
    "backtest",
}
CONTEXT_KEYS = {"flat_state", "in_bed", "office_day", "outside_temperature"}


class AnalysisConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Exclusion:
    room: str
    start: datetime
    end: datetime | None


@dataclass(frozen=True)
class BacktestParams:
    match_window_min: int = 30
    contrary_window_min: int = 60
    setpoint_tolerance: float = 0.5
    min_faelle: int = 5
    min_wochen: int = 3
    min_abdeckung: float = 0.3
    max_fehlausloesungen: float = 0.4


@dataclass(frozen=True)
class ClusterParams:
    min_size: int = 3
    window_min: int = 90
    weekday_groups: dict[str, tuple[int, ...]] = field(
        default_factory=lambda: {"werktage": (0, 1, 2, 3, 4), "wochenende": (5, 6)})


@dataclass(frozen=True)
class AnalysisConfig:
    model: str
    openrouter_url: str
    provider: dict[str, Any]
    price_input: float
    price_output: float
    max_output_tokens: int
    max_cost_usd: float
    temperature: float
    request_timeout_s: float
    retries: int
    retry_backoff_s: float
    max_candidates: int
    timezone: str
    min_interventions: int
    merge_interventions_s: int
    room_names: dict[str, str]
    exclusions: tuple[Exclusion, ...]
    context: dict[str, str]
    clusters: ClusterParams
    backtest: BacktestParams


def _only(where: str, data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise AnalysisConfigError(f"{where}: unbekannte Schlüssel {sorted(unknown)}")


def _clusters(raw: dict[str, Any]) -> ClusterParams:
    _only("clusters", raw, {"min_size", "window_min", "weekday_groups"})
    defaults = ClusterParams()
    groups = defaults.weekday_groups
    if "weekday_groups" in raw:
        groups = {}
        for name, days in (raw["weekday_groups"] or {}).items():
            parsed = tuple(int(day) for day in days)
            if not parsed or any(day < 0 or day > 6 for day in parsed):
                raise AnalysisConfigError(f"clusters.weekday_groups.{name}: Wochentage müssen 0 bis 6 sein")
            groups[name] = parsed
    return ClusterParams(min_size=int(raw.get("min_size", defaults.min_size)),
                         window_min=int(raw.get("window_min", defaults.window_min)),
                         weekday_groups=groups)


def load_analysis_config(path: str | Path) -> AnalysisConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    _only("analysis.yaml", raw, TOP_KEYS)
    model = raw.get("model")
    if not model:
        raise AnalysisConfigError("model fehlt")
    provider = dict(raw.get("provider") or {})
    if provider.get("zdr") is not True:
        raise AnalysisConfigError("provider.zdr muss true sein: Zero Data Retention ist Pflicht")
    timezone = str(raw.get("timezone", "Europe/Berlin"))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise AnalysisConfigError(f"timezone: unbekannte Zeitzone {timezone}") from exc
    price = raw.get("price_per_mtok") or {}
    try:
        price_input, price_output = float(price["input"]), float(price["output"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalysisConfigError("price_per_mtok braucht input und output (Zahlen)") from exc
    context = dict(raw.get("context") or {})
    missing = CONTEXT_KEYS - set(context)
    if missing:
        raise AnalysisConfigError(f"context: Einträge fehlen: {sorted(missing)}")
    _only("context", context, CONTEXT_KEYS)
    exclusions = []
    for item in raw.get("exclusions") or []:
        _only("exclusions", item, {"room", "from", "to"})
        if "room" not in item or "from" not in item:
            raise AnalysisConfigError("exclusions: room und from sind Pflicht")
        end = parse_utc(str(item["to"])) if item.get("to") else None
        exclusions.append(Exclusion(str(item["room"]), parse_utc(str(item["from"])), end))
    backtest_raw = dict(raw.get("backtest") or {})
    _only("backtest", backtest_raw, {f.name for f in fields(BacktestParams)})
    return AnalysisConfig(
        model=str(model),
        openrouter_url=str(raw.get("openrouter_url", "https://openrouter.ai/api/v1/chat/completions")),
        provider=provider,
        price_input=price_input,
        price_output=price_output,
        max_output_tokens=int(raw.get("max_output_tokens", 8000)),
        max_cost_usd=float(raw.get("max_cost_usd", 2.0)),
        temperature=float(raw.get("temperature", 0.2)),
        request_timeout_s=float(raw.get("request_timeout_s", 180)),
        retries=int(raw.get("retries", 3)),
        retry_backoff_s=float(raw.get("retry_backoff_s", 2.0)),
        max_candidates=int(raw.get("max_candidates", 20)),
        timezone=timezone,
        min_interventions=int(raw.get("min_interventions", 20)),
        merge_interventions_s=int(raw.get("merge_interventions_s", 60)),
        room_names={str(k): str(v) for k, v in (raw.get("room_names") or {}).items()},
        exclusions=tuple(exclusions),
        context={key: str(value) for key, value in context.items()},
        clusters=_clusters(dict(raw.get("clusters") or {})),
        backtest=BacktestParams(**backtest_raw),
    )
```

- [ ] **Step 8: Store implementieren**

`ai/analysis/store.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from collector.db import Database
from collector.models import to_utc_iso

from .models import Candidate

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "analysis.sql"


def _candidate(row) -> Candidate:
    return Candidate(
        room=row["room"],
        trigger=json.loads(row["trigger_json"]),
        conditions=json.loads(row["conditions_json"]),
        action=json.loads(row["action_json"]),
        hypothesis=row["hypothesis"] or "",
        status=row["status"],
        note=row["note"] or "",
        id=row["id"],
        run_id=row["run_id"],
    )


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    """Tabellen der Analyse in derselben Datenbank wie die Datenbasis.

    `start_run` und `finish_run` committen. `add_candidate`, `set_candidate_status`,
    `save_results` und `save_pseudonyms` committen nicht: Der Aufrufer bündelt sie
    in einer Transaktion und ruft `commit()` oder `rollback()`.
    """

    def __init__(self, db: Database):
        self.conn = db.conn
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # --- Läufe -------------------------------------------------------------

    def start_run(self, mode: str, model: str, params: dict, now: datetime) -> int:
        cursor = self.conn.execute(
            "INSERT INTO analysis_runs(mode, model, started_at, params, status) VALUES (?, ?, ?, ?, 'laeuft')",
            (mode, model, to_utc_iso(now), _dump(params)),
        )
        self.conn.commit()
        return cursor.lastrowid

    def finish_run(self, run_id: int, status: str, now: datetime, *, error: str | None = None,
                   prompt_hash: str | None = None, prompt_text: str | None = None,
                   response_text: str | None = None, tokens_in: int | None = None,
                   tokens_out: int | None = None, cost_usd: float | None = None) -> None:
        self.conn.execute(
            """
            UPDATE analysis_runs SET status = ?, finished_at = ?, error = ?, prompt_hash = ?,
              prompt_text = ?, response_text = ?, tokens_in = ?, tokens_out = ?, cost_usd = ?
            WHERE id = ?
            """,
            (status, to_utc_iso(now), error, prompt_hash, prompt_text, response_text,
             tokens_in, tokens_out, cost_usd, run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: int):
        return self.conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    def list_runs(self):
        return self.conn.execute("SELECT * FROM analysis_runs ORDER BY id").fetchall()

    # --- Kandidaten --------------------------------------------------------

    def add_candidate(self, run_id: int, candidate: Candidate) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO candidates(run_id, room, trigger_json, conditions_json, action_json,
                                   hypothesis, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, candidate.room, _dump(candidate.trigger), _dump(candidate.conditions),
             _dump(candidate.action), candidate.hypothesis, candidate.status, candidate.note),
        )
        return cursor.lastrowid

    def set_candidate_status(self, candidate_id: int, status: str, note: str | None = None) -> None:
        self.conn.execute("UPDATE candidates SET status = ?, note = COALESCE(?, note) WHERE id = ?",
                          (status, note, candidate_id))

    def get_candidate(self, candidate_id: int) -> Candidate:
        row = self.conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(f"Kandidat {candidate_id} existiert nicht")
        return _candidate(row)

    def list_candidates(self, run_id: int | None = None, status: str | None = None) -> list[Candidate]:
        sql, params = "SELECT * FROM candidates WHERE 1 = 1", []
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        return [_candidate(row) for row in self.conn.execute(sql + " ORDER BY id", params)]

    # --- Ergebnisse und Pseudonyme -------------------------------------------

    def save_results(self, candidate_id: int, metrics: dict[str, float]) -> None:
        self.conn.execute("DELETE FROM backtest_results WHERE candidate_id = ?", (candidate_id,))
        self.conn.executemany(
            "INSERT INTO backtest_results(candidate_id, metric, value) VALUES (?, ?, ?)",
            [(candidate_id, name, float(value)) for name, value in metrics.items()],
        )

    def results(self, candidate_id: int) -> dict[str, float]:
        rows = self.conn.execute(
            "SELECT metric, value FROM backtest_results WHERE candidate_id = ?", (candidate_id,))
        return {row["metric"]: row["value"] for row in rows}

    def save_pseudonyms(self, mapping: dict[str, tuple[str, str]]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO pseudonyms(alias, entity_id, kind) VALUES (?, ?, ?)",
            [(alias, entity_id, kind) for alias, (entity_id, kind) in mapping.items()],
        )

    def load_pseudonyms(self) -> dict[str, str]:
        return {row["alias"]: row["entity_id"] for row in self.conn.execute("SELECT * FROM pseudonyms")}
```

- [ ] **Step 9: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_analysis_config.py tests/test_store.py -v`
Expected: PASS (16 Tests).

- [ ] **Step 10: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle bisherigen Tests PASS.

```bash
git add ai/analysis ai/config/analysis.yaml ai/sql/analysis.sql ai/tests
git commit -m "feat(ai): Analyse-Gerüst mit Konfiguration, Modellen, Schema und Store"
```

---

### Task 2: Pseudonymisierung

**Files:**
- Create: `ai/analysis/pseudonym.py`
- Test: `ai/tests/test_pseudonym.py`

**Interfaces:**
- Consumes: `collector.db.Database` (Tabellen `entities`, `users`).
- Produces: `@dataclass(frozen=True) Pseudonyms(persons: tuple[str, ...], display_names: dict[str, str], user_to_person: dict[str, str])` mit `aliases -> dict[str, str]` (Property, Alias → Entity-ID), `alias_for_person(entity_id) -> str | None`, `person_for_alias(alias) -> str | None`, `alias_for_user(user_id) -> str | None`, `display_name(entity_id) -> str` (nur lokal), `leaks(text) -> list[str]`, `to_rows() -> dict[str, tuple[str, str]]` (für `Store.save_pseudonyms`); `build_pseudonyms(db) -> Pseudonyms`.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_pseudonym.py`:

```python
from datetime import datetime, timezone

from analysis.pseudonym import build_pseudonyms
from collector.models import EntityRule

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fill(db):
    db.upsert_entity(EntityRule("person.svenja", "person", "shared"), None, NOW)
    db.upsert_entity(EntityRule("person.fabian", "person", "shared"), None, NOW)
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating"), "buero", NOW)
    db.upsert_user("45171d305dd5422ca9d0ecf755abbda7", "person.fabian", "Fabian Muster")
    db.upsert_user("0f1e2d3c4b5a69788796a5b4c3d2e1f0", "person.svenja", "Svenja")


def test_aliases_follow_sorted_entity_ids(db):
    fill(db)
    pseudo = build_pseudonyms(db)
    assert pseudo.persons == ("person.fabian", "person.svenja")
    assert pseudo.aliases == {"person_1": "person.fabian", "person_2": "person.svenja"}
    assert pseudo.alias_for_person("person.svenja") == "person_2"
    assert pseudo.person_for_alias("person_1") == "person.fabian"
    assert pseudo.person_for_alias("person_9") is None
    assert pseudo.alias_for_person("sensor.x") is None


def test_users_map_to_person_aliases(db):
    fill(db)
    pseudo = build_pseudonyms(db)
    assert pseudo.alias_for_user("45171d305dd5422ca9d0ecf755abbda7") == "person_1"
    assert pseudo.alias_for_user("unbekannt") is None


def test_display_name_is_local_only_and_falls_back_to_object_id(db):
    fill(db)
    db.upsert_entity(EntityRule("person.gast", "person", "shared"), None, NOW)
    pseudo = build_pseudonyms(db)
    assert pseudo.display_name("person.fabian") == "Fabian Muster"
    assert pseudo.display_name("person.gast") == "Gast"


def test_leaks_finds_names_ids_and_user_ids(db):
    fill(db)
    pseudo = build_pseudonyms(db)
    assert pseudo.leaks("person_1 kommt heim, Wohnzimmer") == []
    found = pseudo.leaks("FABIAN ist da, person.svenja auch, user 45171d305dd5422ca9d0ecf755abbda7")
    assert "fabian" in found and "svenja" in found
    assert "person.svenja" in found
    assert "45171d305dd5422ca9d0ecf755abbda7" in found


def test_to_rows_for_the_store(db):
    fill(db)
    assert build_pseudonyms(db).to_rows() == {
        "person_1": ("person.fabian", "person"),
        "person_2": ("person.svenja", "person"),
    }


def test_database_without_persons_gives_empty_mapping(db):
    pseudo = build_pseudonyms(db)
    assert pseudo.persons == () and pseudo.aliases == {} and pseudo.leaks("egal") == []
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_pseudonym.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.pseudonym'`.

- [ ] **Step 3: Implementieren**

`ai/analysis/pseudonym.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from collector.db import Database


@dataclass(frozen=True)
class Pseudonyms:
    """Zuordnung Person -> Alias. Klarnamen verlassen den Rechner nie."""

    persons: tuple[str, ...]                  # Entity-IDs; Reihenfolge = person_1, person_2, ...
    display_names: dict[str, str]             # entity_id -> Anzeigename (nur lokal)
    user_to_person: dict[str, str]            # user_id -> person-Entity-ID

    @property
    def aliases(self) -> dict[str, str]:
        return {f"person_{number}": entity_id for number, entity_id in enumerate(self.persons, start=1)}

    def alias_for_person(self, entity_id: str) -> str | None:
        for alias, entity in self.aliases.items():
            if entity == entity_id:
                return alias
        return None

    def person_for_alias(self, alias: str) -> str | None:
        return self.aliases.get(alias)

    def alias_for_user(self, user_id: str) -> str | None:
        person = self.user_to_person.get(user_id)
        return self.alias_for_person(person) if person else None

    def display_name(self, entity_id: str) -> str:
        return self.display_names.get(entity_id) or entity_id.split(".", 1)[-1].title()

    def _sensitive_terms(self) -> set[str]:
        terms: set[str] = set(self.user_to_person)
        for entity_id in self.persons:
            terms.add(entity_id)
            terms.add(entity_id.split(".", 1)[-1])
            name = self.display_names.get(entity_id)
            if name:
                terms.add(name)
                terms.update(word for word in name.split() if len(word) >= 3)
        return {term for term in terms if len(term) >= 3}

    def leaks(self, text: str) -> list[str]:
        lowered = text.lower()
        return sorted({term.lower() for term in self._sensitive_terms() if term.lower() in lowered})

    def to_rows(self) -> dict[str, tuple[str, str]]:
        return {alias: (entity_id, "person") for alias, entity_id in self.aliases.items()}


def build_pseudonyms(db: Database) -> Pseudonyms:
    persons = tuple(sorted(
        row["entity_id"] for row in db.conn.execute("SELECT entity_id FROM entities WHERE kind = 'person'")))
    display_names: dict[str, str] = {}
    user_to_person: dict[str, str] = {}
    for row in db.conn.execute("SELECT user_id, person_entity, name FROM users"):
        if row["person_entity"]:
            user_to_person[row["user_id"]] = row["person_entity"]
            if row["name"]:
                display_names[row["person_entity"]] = row["name"]
    return Pseudonyms(persons, display_names, user_to_person)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_pseudonym.py -v`
Expected: PASS (6 Tests).

- [ ] **Step 5: Commit**

```bash
git add ai/analysis/pseudonym.py ai/tests/test_pseudonym.py
git commit -m "feat(ai): Pseudonymisierung der Personen für die Analyse"
```

---

### Task 3: Zeitreihen und Eingriffe

**Files:**
- Create: `ai/analysis/timeline.py`, `ai/analysis/interventions.py`
- Test: `ai/tests/test_timeline.py`, `ai/tests/test_interventions.py`

**Interfaces:**
- Consumes: `Database` (View `manual_actions`, Tabelle `state_changes`), `parse_utc`, `AnalysisConfig` (`exclusions`, `merge_interventions_s`), `Intervention` aus Aufgabe 1.
- Produces:
  - `analysis.timeline.Timeline(points: list[tuple[datetime, str | None]] | None = None)` mit `__bool__`, `value_at(ts) -> str | None` (Zustand, der zum Zeitpunkt galt; `None` davor), `numeric_at(ts) -> float | None`, `transitions() -> list[tuple[datetime, str | None, str | None]]` (Zeitpunkt, alter, neuer Zustand; nur wo sich der Zustand ändert), `states() -> set[str]`; `load_timeline(db, entity_id) -> Timeline` (leer, wenn keine Zeilen).
  - `analysis.interventions.excluded(cfg, room, ts) -> bool`; `load_interventions(db, cfg, *, start=None, end=None) -> list[Intervention]` (sortiert nach Zeit). Ein Eingriff mit geändertem `temperature` ist `kind = "setpoint"` (Wert als `float`), sonst mit geändertem `preset_mode` `kind = "preset"`; Zeilen nur mit `hvac_mode`-Änderung, ohne Raum, im Ausschlusszeitraum oder außerhalb von `start`/`end` entfallen; Zeilen derselben Entität innerhalb von `merge_interventions_s` Sekunden werden zu einem Eingriff mit dem Zeitpunkt der ersten und dem Wert der letzten Zeile.

- [ ] **Step 1: Tests für die Zeitreihe schreiben**

`ai/tests/test_timeline.py`:

```python
from datetime import datetime, timedelta, timezone

from analysis.timeline import Timeline, load_timeline
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def timeline(*items) -> Timeline:
    return Timeline([(T0 + timedelta(minutes=minutes), state) for minutes, state in items])


def test_value_at_returns_last_state_not_after_t():
    tl = timeline((0, "off"), (10, "on"), (20, "off"))
    assert tl.value_at(T0 - timedelta(minutes=1)) is None
    assert tl.value_at(T0) == "off"
    assert tl.value_at(T0 + timedelta(minutes=10)) == "on"
    assert tl.value_at(T0 + timedelta(minutes=19)) == "on"
    assert tl.value_at(T0 + timedelta(hours=5)) == "off"


def test_transitions_only_where_state_changes():
    tl = timeline((0, "off"), (5, "off"), (10, "on"), (20, "off"))
    assert tl.transitions() == [
        (T0 + timedelta(minutes=10), "off", "on"),
        (T0 + timedelta(minutes=20), "on", "off"),
    ]


def test_numeric_at_ignores_non_numbers():
    tl = timeline((0, "18.5"), (10, "unavailable"))
    assert tl.numeric_at(T0) == 18.5
    assert tl.numeric_at(T0 + timedelta(minutes=10)) is None
    assert tl.numeric_at(T0 - timedelta(minutes=1)) is None


def test_empty_timeline_is_falsy_and_safe():
    tl = Timeline()
    assert not tl
    assert tl.value_at(T0) is None and tl.numeric_at(T0) is None
    assert tl.transitions() == [] and tl.states() == set()


def test_unsorted_points_are_sorted():
    tl = timeline((10, "on"), (0, "off"))
    assert tl.value_at(T0 + timedelta(minutes=5)) == "off"


def test_load_timeline_reads_state_changes(db):
    db.upsert_entity(EntityRule("input_boolean.x", "flat", "shared"), None, T0)
    db.insert_changes([Change(T0, "input_boolean.x", "off"),
                       Change(T0 + timedelta(minutes=5), "input_boolean.x", "on")], "live")
    tl = load_timeline(db, "input_boolean.x")
    assert tl.states() == {"off", "on"}
    assert tl.value_at(T0 + timedelta(minutes=6)) == "on"
    assert not load_timeline(db, "input_boolean.gibt_es_nicht")
```

- [ ] **Step 2: Tests für die Eingriffe schreiben**

`ai/tests/test_interventions.py`:

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from analysis.config import Exclusion
from analysis.interventions import excluded, load_interventions
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
CLIMATE = EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode"))


def put(db, seconds, state="heat", temperature=17, preset="eco", user=None, parent=None, ctx=None):
    db.insert_changes([Change(T0 + timedelta(seconds=seconds), "climate.bt", state,
                              {"temperature": temperature, "preset_mode": preset},
                              ctx or f"c{seconds}", user, parent)], "live")


def prepared(db, room="r_living"):
    db.upsert_entity(CLIMATE, room, T0)
    put(db, 0)                                       # Ausgangszustand


def test_setpoint_change_by_user_is_a_setpoint_intervention(db, acfg):
    prepared(db)
    put(db, 60, temperature=21, preset="comfort", user="u1")   # beides ändert sich: Sollwert gewinnt
    (item,) = load_interventions(db, acfg)
    assert (item.kind, item.value, item.room, item.entity_id, item.user_id) == (
        "setpoint", 21.0, "r_living", "climate.bt", "u1")
    assert item.ts == T0 + timedelta(seconds=60)


def test_preset_only_change_is_a_preset_intervention(db, acfg):
    prepared(db)
    put(db, 60, temperature=17, preset="comfort", user="u1")
    (item,) = load_interventions(db, acfg)
    assert (item.kind, item.value) == ("preset", "comfort")


def test_hvac_mode_only_change_is_skipped(db, acfg):
    prepared(db)
    put(db, 60, state="off", user="u1")
    assert load_interventions(db, acfg) == []


def test_automation_and_unknown_are_not_interventions(db, acfg):
    prepared(db)
    put(db, 60, temperature=21, parent="p1", user="u1")
    put(db, 120, temperature=22)
    assert load_interventions(db, acfg) == []


def test_rows_within_merge_window_become_one_intervention(db, acfg):
    prepared(db)
    put(db, 100, temperature=18, user="u1")
    put(db, 130, temperature=19, user="u1")
    put(db, 170, temperature=20, user="u1")          # 40 s nach der vorigen Zeile: gleiche Kette
    put(db, 400, temperature=22, user="u1")          # neuer Eingriff
    items = load_interventions(db, acfg)
    assert [(i.ts, i.value) for i in items] == [
        (T0 + timedelta(seconds=100), 20.0),
        (T0 + timedelta(seconds=400), 22.0),
    ]


def test_entity_without_room_is_skipped(db, acfg):
    prepared(db, room=None)
    put(db, 60, temperature=21, user="u1")
    assert load_interventions(db, acfg) == []


def test_period_filter_is_half_open(db, acfg):
    prepared(db)
    put(db, 100, temperature=18, user="u1")
    put(db, 500, temperature=21, user="u1")
    start, end = T0 + timedelta(seconds=100), T0 + timedelta(seconds=500)
    items = load_interventions(db, acfg, start=start, end=end)
    assert [i.value for i in items] == [18.0]


def test_exclusion_drops_interventions_of_that_room_only(db, acfg):
    prepared(db)
    put(db, 60, temperature=21, user="u1")
    cfg = replace(acfg, exclusions=(Exclusion("r_living", T0, T0 + timedelta(hours=1)),))
    assert load_interventions(db, cfg) == []
    other = replace(acfg, exclusions=(Exclusion("r_anders", T0, None),))
    assert len(load_interventions(db, other)) == 1


def test_excluded_handles_open_end(acfg):
    cfg = replace(acfg, exclusions=(Exclusion("r", T0, None),))
    assert excluded(cfg, "r", T0 + timedelta(days=900))
    assert not excluded(cfg, "r", T0 - timedelta(seconds=1))
    assert not excluded(cfg, "andere", T0)
```

- [ ] **Step 3: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_timeline.py tests/test_interventions.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.timeline'`.

- [ ] **Step 4: Zeitreihe implementieren**

`ai/analysis/timeline.py`:

```python
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

from collector.db import Database
from collector.models import parse_utc


class Timeline:
    """Zustandsverlauf einer Entität: welcher Zustand galt zum Zeitpunkt t?"""

    def __init__(self, points: list[tuple[datetime, str | None]] | None = None):
        self._points = sorted(points or [], key=lambda point: point[0])
        self._times = [point[0] for point in self._points]

    def __bool__(self) -> bool:
        return bool(self._points)

    def value_at(self, ts: datetime) -> str | None:
        index = bisect_right(self._times, ts) - 1
        return None if index < 0 else self._points[index][1]

    def numeric_at(self, ts: datetime) -> float | None:
        value = self.value_at(ts)
        try:
            return None if value is None else float(value)
        except ValueError:
            return None

    def transitions(self) -> list[tuple[datetime, str | None, str | None]]:
        return [(current[0], previous[1], current[1])
                for previous, current in zip(self._points, self._points[1:])
                if previous[1] != current[1]]

    def states(self) -> set[str]:
        return {state for _, state in self._points if state is not None}


def load_timeline(db: Database, entity_id: str) -> Timeline:
    rows = db.conn.execute(
        "SELECT ts_utc, state FROM state_changes WHERE entity_id = ? ORDER BY ts_utc, id", (entity_id,))
    return Timeline([(parse_utc(row["ts_utc"]), row["state"]) for row in rows])
```

- [ ] **Step 5: Eingriffe implementieren**

`ai/analysis/interventions.py`:

```python
from __future__ import annotations

import json
from datetime import datetime

from collector.db import Database
from collector.models import parse_utc

from .config import AnalysisConfig
from .models import Intervention


def excluded(cfg: AnalysisConfig, room: str, ts: datetime) -> bool:
    return any(item.room == room and item.start <= ts and (item.end is None or ts < item.end)
               for item in cfg.exclusions)


def _kind_and_value(changed: set[str], new: dict) -> tuple[str, float | str] | None:
    if "temperature" in changed and new.get("temperature") is not None:
        try:
            return "setpoint", float(new["temperature"])
        except (TypeError, ValueError):
            return None
    if "preset_mode" in changed and new.get("preset_mode") is not None:
        return "preset", str(new["preset_mode"])
    return None


def load_interventions(db: Database, cfg: AnalysisConfig, *, start: datetime | None = None,
                       end: datetime | None = None) -> list[Intervention]:
    rows = db.conn.execute(
        "SELECT ts_utc, room, entity_id, changed_attrs, new, actor_user_id FROM manual_actions "
        "WHERE actor_class = 'manual' ORDER BY entity_id, ts_utc")
    merged: list[Intervention] = []
    last_index: dict[str, int] = {}
    last_row_ts: dict[str, datetime] = {}
    for row in rows:
        if row["room"] is None:
            continue
        ts = parse_utc(row["ts_utc"])
        if (start is not None and ts < start) or (end is not None and ts >= end):
            continue
        if excluded(cfg, row["room"], ts):
            continue
        found = _kind_and_value(set(row["changed_attrs"].split(",")), json.loads(row["new"]))
        if found is None:
            continue
        entity_id = row["entity_id"]
        previous = last_row_ts.get(entity_id)
        if previous is not None and (ts - previous).total_seconds() <= cfg.merge_interventions_s:
            first = merged[last_index[entity_id]]
            merged[last_index[entity_id]] = Intervention(
                first.ts, first.room, first.entity_id, found[0], found[1], row["actor_user_id"])
        else:
            merged.append(Intervention(ts, row["room"], entity_id, found[0], found[1], row["actor_user_id"]))
            last_index[entity_id] = len(merged) - 1
        last_row_ts[entity_id] = ts
    return sorted(merged, key=lambda item: item.ts)
```

- [ ] **Step 6: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_timeline.py tests/test_interventions.py -v`
Expected: PASS (15 Tests).

- [ ] **Step 7: Commit**

```bash
git add ai/analysis/timeline.py ai/analysis/interventions.py ai/tests/test_timeline.py ai/tests/test_interventions.py
git commit -m "feat(ai): Zeitreihen und Eingriffe für die Analyse"
```

---

### Task 4: Kontext und Verdichtung

**Files:**
- Create: `ai/analysis/context.py`, `ai/analysis/features.py`, `ai/tests/analysis_fixture.py`
- Test: `ai/tests/test_features.py`

**Interfaces:**
- Consumes: `Timeline`, `load_timeline`, `Intervention`, `AnalysisConfig`, `Pseudonyms`, `Database`.
- Produces:
  - `analysis.context.ContextProvider` mit Konstruktor `ContextProvider(pseudo, *, persons=None, flat_state=None, in_bed=None, office_day=None, outside_temp=None, windows=None, presence=None)` (Timelines beziehungsweise `dict[str, Timeline]` und `dict[str, list[Timeline]]` je Raum), `ContextProvider.empty(pseudo=None)`, `ContextProvider.from_db(db, cfg, pseudo)`, Attribute `flat_state`, `in_bed`, `office_day`, `outside_temp` (jeweils `Timeline`), Methoden `person_timeline(entity_id) -> Timeline`, `windows(room) -> list[Timeline]`, `presence(room) -> list[Timeline]`, `persons_home(ts) -> dict[str, bool | None]` (Alias → zu Hause), `snapshot(room, ts) -> dict` mit den Schlüsseln `persons_home`, `flat_state`, `in_bed`, `office_day`, `windows_closed`, `room_occupied`, `outside_temp` (fehlende Daten sind `None`).
  - `analysis.features`: `WEEKDAY_NAMES`, `room_name(cfg, area_id) -> str`, `data_period(db) -> tuple[datetime, datetime] | None`, `known_presets(db) -> set[str]`, `build_weekly(interventions, ctx, cfg, pseudo, start, end) -> dict`, `build_clusters(interventions, ctx, cfg, pseudo, start, end) -> dict` (Schlüssel `zeitraum` und `cluster`).
  - Test-Hilfe `tests.analysis_fixture`: `ROOM = "r_living"`, `BERLIN`, `local_utc(day, "HH:MM") -> datetime`, `build_habit_db(db, *, weeks=6, with_noise=True)`.

- [ ] **Step 1: Test-Datenbank mit bekannter Gewohnheit schreiben**

`ai/tests/analysis_fixture.py`:

```python
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
```

- [ ] **Step 2: Tests für Kontext und Verdichtung schreiben**

`ai/tests/test_features.py`:

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from analysis.context import ContextProvider
from analysis.features import (build_clusters, build_weekly, data_period, known_presets, room_name)
from analysis.interventions import load_interventions
from analysis.models import Intervention
from analysis.pseudonym import build_pseudonyms
from analysis.timeline import Timeline
from tests.analysis_fixture import ROOM, build_habit_db

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def habit(db, acfg):
    build_habit_db(db)
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())
    pseudo = build_pseudonyms(db)
    ctx = ContextProvider.from_db(db, cfg, pseudo)
    start, end = data_period(db)
    return cfg, pseudo, ctx, load_interventions(db, cfg), start, end


def test_data_period_and_known_presets(db):
    assert data_period(db) is None
    build_habit_db(db)
    start, end = data_period(db)
    assert start < end
    assert known_presets(db) == {"eco", "comfort", "none"}


def test_room_name_falls_back_to_area_id(acfg):
    assert room_name(acfg, "office") == "Bastelzimmer"
    assert room_name(acfg, "unbekannt") == "unbekannt"


def test_weekly_cells_use_local_time_across_dst(habit):
    cfg, pseudo, ctx, interventions, start, end = habit
    data = build_weekly(interventions, ctx, cfg, pseudo, start, end)
    assert data["eingriffe_gesamt"] == 32
    cells = {(c["wochentag"], c["stunde"]): c for c in data["raeume"]["Wohnzimmer"]}
    monday = cells[("Montag", 17)]
    assert monday["eingriffe"] == 6                      # alle sechs Montage in derselben lokalen Stunde
    assert monday["haeufigste_aktion"] == {"type": "setpoint", "value": 21.0}
    assert monday["anteil_haeufigste"] == 1.0
    assert monday["personen_zuhause_anteil"] == {"person_2": 1.0}
    assert cells[("Samstag", 12)]["eingriffe"] == 2
    assert cells[("Samstag", 12)]["haeufigste_aktion"]["value"] == 19.0


def test_weekly_summarises_weekday_context(habit):
    cfg, pseudo, ctx, interventions, start, end = habit
    data = build_weekly(interventions, ctx, cfg, pseudo, start, end)
    share = data["wochentage"]["Montag"]["personen_zuhause_anteil"]["person_2"]
    assert 0.1 < share < 0.4                              # abends 5 von 24 Stunden zu Hause
    assert "wohnungszustand_anteile" not in data["wochentage"]["Montag"]   # Entität fehlt in der Fixture
    assert data["zeitraum"]["von"] == "2026-03-16"
    assert set(data["wochentage"]) == {"Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
                                       "Samstag", "Sonntag"}


def test_clusters_find_the_weekday_habit_with_context(habit):
    cfg, pseudo, ctx, interventions, start, end = habit
    data = build_clusters(interventions, ctx, cfg, pseudo, start, end)
    (cluster,) = data["cluster"]                          # die zwei Samstags-Eingriffe sind zu wenige
    assert cluster["raum"] == "Wohnzimmer"
    assert cluster["aktion"] == {"type": "setpoint", "value": 21.0}
    assert cluster["wochentage"] == "werktage"
    assert (cluster["anzahl"], cluster["wochen"]) == (30, 6)
    assert cluster["uhrzeit"] == {"von": "17:32", "median": "17:32", "bis": "17:32"}
    assert cluster["kontext"]["personen_zuhause_anteil"] == {"person_2": 1.0}
    assert cluster["kontext"]["aussentemperatur"] == {}   # kein Außensensor in der Fixture


def test_clusters_split_when_times_are_far_apart(acfg):
    cfg = replace(acfg, room_names={"r": "Raum"})
    items = [Intervention(T0 + timedelta(hours=h, minutes=m), "r", "climate.x", "setpoint", 21.0)
             for h, m in ((-2, 0), (-2, 10), (-2, 20), (10, 0), (10, 5), (10, 9))]
    ctx = ContextProvider.empty()
    data = build_clusters(items, ctx, cfg, ctx.pseudo, T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert [c["anzahl"] for c in data["cluster"]] == [3, 3]


def test_snapshot_tolerates_missing_context():
    ctx = ContextProvider.empty()
    assert ctx.snapshot("r", T0) == {
        "persons_home": {}, "flat_state": None, "in_bed": None, "office_day": None,
        "windows_closed": None, "room_occupied": None, "outside_temp": None}
    assert not ctx.windows("r") and not ctx.person_timeline("person.x")


def test_snapshot_reads_windows_presence_and_temperature(db, acfg):
    ctx = ContextProvider(
        build_pseudonyms(db),
        windows={"r": [Timeline([(T0, "off")]), Timeline([(T0, "on")])]},
        presence={"r": [Timeline([(T0, "on")])]},
        outside_temp=Timeline([(T0, "7.5")]),
        flat_state=Timeline([(T0, "home")]),
    )
    later = T0 + timedelta(minutes=1)
    snap = ctx.snapshot("r", later)
    assert snap["windows_closed"] is False               # ein Fenster offen
    assert snap["room_occupied"] is True
    assert snap["outside_temp"] == 7.5 and snap["flat_state"] == "home"
    assert ctx.snapshot("anderer_raum", later)["windows_closed"] is None
```

- [ ] **Step 3: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_features.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.context'`.

- [ ] **Step 4: Kontext implementieren**

`ai/analysis/context.py`:

```python
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from collector.db import Database

from .config import AnalysisConfig
from .pseudonym import Pseudonyms
from .timeline import Timeline, load_timeline

UNKNOWN = (None, "unavailable", "unknown")


def _flag(timeline: Timeline, ts: datetime, on: str) -> bool | None:
    value = timeline.value_at(ts)
    return None if value in UNKNOWN else value == on


class ContextProvider:
    """Liefert den Kontext (Anwesenheit, Wohnungszustand, Fenster, Wetter) zu einem Zeitpunkt.

    Fehlende Entitäten sind kein Fehler: Die zugehörigen Werte sind `None`.
    """

    def __init__(self, pseudo: Pseudonyms, *, persons: dict[str, Timeline] | None = None,
                 flat_state: Timeline | None = None, in_bed: Timeline | None = None,
                 office_day: Timeline | None = None, outside_temp: Timeline | None = None,
                 windows: dict[str, list[Timeline]] | None = None,
                 presence: dict[str, list[Timeline]] | None = None):
        self.pseudo = pseudo
        self._persons = persons or {}
        self.flat_state = flat_state or Timeline()
        self.in_bed = in_bed or Timeline()
        self.office_day = office_day or Timeline()
        self.outside_temp = outside_temp or Timeline()
        self._windows = windows or {}
        self._presence = presence or {}

    @classmethod
    def empty(cls, pseudo: Pseudonyms | None = None) -> "ContextProvider":
        return cls(pseudo or Pseudonyms((), {}, {}))

    @classmethod
    def from_db(cls, db: Database, cfg: AnalysisConfig, pseudo: Pseudonyms) -> "ContextProvider":
        windows: dict[str, list[Timeline]] = defaultdict(list)
        for row in db.conn.execute(
                "SELECT entity_id, room FROM entities WHERE kind = 'contact' AND room IS NOT NULL"):
            windows[row["room"]].append(load_timeline(db, row["entity_id"]))
        presence: dict[str, list[Timeline]] = defaultdict(list)
        for row in db.conn.execute(
                "SELECT entity_id, room FROM entities WHERE kind = 'presence' AND room IS NOT NULL "
                "AND entity_id LIKE 'binary_sensor.%'"):
            presence[row["room"]].append(load_timeline(db, row["entity_id"]))
        return cls(
            pseudo,
            persons={entity_id: load_timeline(db, entity_id) for entity_id in pseudo.persons},
            flat_state=load_timeline(db, cfg.context["flat_state"]),
            in_bed=load_timeline(db, cfg.context["in_bed"]),
            office_day=load_timeline(db, cfg.context["office_day"]),
            outside_temp=load_timeline(db, cfg.context["outside_temperature"]),
            windows=dict(windows),
            presence=dict(presence),
        )

    def person_timeline(self, entity_id: str) -> Timeline:
        return self._persons.get(entity_id, Timeline())

    def windows(self, room: str) -> list[Timeline]:
        return self._windows.get(room, [])

    def presence(self, room: str) -> list[Timeline]:
        return self._presence.get(room, [])

    def persons_home(self, ts: datetime) -> dict[str, bool | None]:
        result: dict[str, bool | None] = {}
        for alias, entity_id in self.pseudo.aliases.items():
            timeline = self.person_timeline(entity_id)
            result[alias] = _flag(timeline, ts, "home") if timeline else None
        return {alias: value for alias, value in result.items() if value is not None}

    def snapshot(self, room: str, ts: datetime) -> dict:
        window_states = [tl.value_at(ts) for tl in self.windows(room) if tl]
        if any(state == "on" for state in window_states):
            windows_closed: bool | None = False
        elif window_states and all(state == "off" for state in window_states):
            windows_closed = True
        else:
            windows_closed = None
        presence_states = [tl.value_at(ts) for tl in self.presence(room) if tl]
        if any(state == "on" for state in presence_states):
            room_occupied: bool | None = True
        elif presence_states and all(state == "off" for state in presence_states):
            room_occupied = False
        else:
            room_occupied = None
        flat = self.flat_state.value_at(ts)
        return {
            "persons_home": self.persons_home(ts),
            "flat_state": None if flat in UNKNOWN else flat,
            "in_bed": _flag(self.in_bed, ts, "on"),
            "office_day": _flag(self.office_day, ts, "on"),
            "windows_closed": windows_closed,
            "room_occupied": room_occupied,
            "outside_temp": self.outside_temp.numeric_at(ts),
        }
```

- [ ] **Step 5: Verdichtung implementieren**

`ai/analysis/features.py`:

```python
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from collector.db import Database
from collector.models import parse_utc

from .config import AnalysisConfig
from .context import UNKNOWN, ContextProvider
from .models import Intervention
from .pseudonym import Pseudonyms

WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def room_name(cfg: AnalysisConfig, area_id: str) -> str:
    return cfg.room_names.get(area_id, area_id)


def data_period(db: Database) -> tuple[datetime, datetime] | None:
    row = db.conn.execute("SELECT MIN(ts_utc) AS first, MAX(ts_utc) AS last FROM state_changes").fetchone()
    if row["first"] is None:
        return None
    return parse_utc(row["first"]), parse_utc(row["last"]) + timedelta(seconds=1)


def known_presets(db: Database) -> set[str]:
    rows = db.conn.execute(
        "SELECT DISTINCT json_extract(sc.attrs, '$.preset_mode') AS preset FROM state_changes sc "
        "JOIN entities e ON e.entity_id = sc.entity_id WHERE e.kind = 'climate'")
    return {row["preset"] for row in rows if row["preset"]}


def _value_key(item: Intervention) -> float | str:
    return item.value if item.kind == "preset" else round(float(item.value) * 2) / 2


def _period(cfg: AnalysisConfig, start: datetime, end: datetime) -> dict:
    tz = ZoneInfo(cfg.timezone)
    return {"von": start.astimezone(tz).date().isoformat(), "bis": end.astimezone(tz).date().isoformat()}


def _share(values: list[bool]) -> float:
    return round(sum(values) / len(values), 2)


def build_weekly(interventions: list[Intervention], ctx: ContextProvider, cfg: AnalysisConfig,
                 pseudo: Pseudonyms, start: datetime, end: datetime) -> dict:
    """Raster je Raum, Wochentag und Stunde (lokale Zeit); nur Zellen mit Eingriffen."""
    tz = ZoneInfo(cfg.timezone)
    cells: dict[tuple[str, int, int], list[Intervention]] = defaultdict(list)
    for item in interventions:
        local = item.ts.astimezone(tz)
        cells[(item.room, local.weekday(), local.hour)].append(item)
    rooms: dict[str, list[dict]] = defaultdict(list)
    for (room, weekday, hour), items in sorted(cells.items()):
        actions = Counter((item.kind, _value_key(item)) for item in items)
        (kind, value), count = actions.most_common(1)[0]
        home: dict[str, list[bool]] = defaultdict(list)
        for item in items:
            for alias, is_home in ctx.snapshot(room, item.ts)["persons_home"].items():
                home[alias].append(is_home)
        rooms[room_name(cfg, room)].append({
            "wochentag": WEEKDAY_NAMES[weekday],
            "stunde": hour,
            "eingriffe": len(items),
            "haeufigste_aktion": {"type": kind, "value": value},
            "anteil_haeufigste": round(count / len(items), 2),
            "personen_zuhause_anteil": {alias: _share(values) for alias, values in sorted(home.items())},
        })
    flat: dict[int, Counter] = defaultdict(Counter)
    home_hours: dict[int, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    moment = start.replace(minute=0, second=0, microsecond=0)
    while moment < end:
        weekday = moment.astimezone(tz).weekday()
        state = ctx.flat_state.value_at(moment)
        if state not in UNKNOWN:
            flat[weekday][state] += 1
        for alias, is_home in ctx.persons_home(moment).items():
            home_hours[weekday][alias].append(is_home)
        moment += timedelta(hours=1)
    weekdays: dict[str, dict] = {}
    for number, name in enumerate(WEEKDAY_NAMES):
        entry: dict = {}
        if flat[number]:
            total = sum(flat[number].values())
            entry["wohnungszustand_anteile"] = {state: round(count / total, 2)
                                                for state, count in sorted(flat[number].items())}
        if home_hours[number]:
            entry["personen_zuhause_anteil"] = {alias: _share(values)
                                                for alias, values in sorted(home_hours[number].items())}
        weekdays[name] = entry
    return {
        "zeitraum": _period(cfg, start, end),
        "eingriffe_gesamt": len(interventions),
        "raeume": dict(rooms),
        "wochentage": weekdays,
    }


def _context_shares(snapshots: list[dict]) -> dict:
    shares: dict = {}
    home: dict[str, list[bool]] = defaultdict(list)
    for snap in snapshots:
        for alias, is_home in snap["persons_home"].items():
            home[alias].append(is_home)
    if home:
        shares["personen_zuhause_anteil"] = {alias: _share(values) for alias, values in sorted(home.items())}
    states = [snap["flat_state"] for snap in snapshots if snap["flat_state"] is not None]
    if states:
        counts = Counter(states)
        shares["wohnungszustand_anteile"] = {state: round(count / len(states), 2)
                                             for state, count in sorted(counts.items())}
    for key, label in (("in_bed", "im_bett_anteil"), ("office_day", "buerotag_anteil"),
                       ("windows_closed", "fenster_zu_anteil"), ("room_occupied", "raum_belegt_anteil")):
        values = [snap[key] for snap in snapshots if snap[key] is not None]
        if values:
            shares[label] = _share(values)
    temperatures = [snap["outside_temp"] for snap in snapshots if snap["outside_temp"] is not None]
    buckets = Counter("unter_8" if t < 8 else "8_bis_15" if t <= 15 else "ueber_15" for t in temperatures)
    shares["aussentemperatur"] = {name: round(count / len(temperatures), 2)
                                  for name, count in sorted(buckets.items())}
    return shares


def _clock(minutes: float) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def build_clusters(interventions: list[Intervention], ctx: ContextProvider, cfg: AnalysisConfig,
                   pseudo: Pseudonyms, start: datetime, end: datetime) -> dict:
    """Bündelt Eingriffe nach Raum, Aktion, Wochentagsgruppe und Uhrzeit und hängt den Kontext an."""
    tz = ZoneInfo(cfg.timezone)
    groups: dict[tuple, list[tuple[int, Intervention]]] = defaultdict(list)
    for item in interventions:
        local = item.ts.astimezone(tz)
        group = next((name for name, days in cfg.clusters.weekday_groups.items()
                      if local.weekday() in days), None)
        if group is None:
            continue
        groups[(item.room, item.kind, _value_key(item), group)].append((local.hour * 60 + local.minute, item))
    clusters: list[dict] = []
    for (room, kind, value, group), members in sorted(groups.items(), key=lambda entry: str(entry[0])):
        members.sort(key=lambda entry: entry[0])
        index = 0
        while index < len(members):
            first_minute = members[index][0]
            stop = index
            while stop < len(members) and members[stop][0] - first_minute <= cfg.clusters.window_min:
                stop += 1
            chosen = members[index:stop]
            index = stop
            if len(chosen) < cfg.clusters.min_size:
                continue
            minutes = [minute for minute, _ in chosen]
            weeks = {item.ts.astimezone(tz).isocalendar()[:2] for _, item in chosen}
            clusters.append({
                "raum": room_name(cfg, room),
                "aktion": {"type": kind, "value": value},
                "wochentage": group,
                "uhrzeit": {"von": _clock(min(minutes)), "median": _clock(median(minutes)),
                            "bis": _clock(max(minutes))},
                "anzahl": len(chosen),
                "wochen": len(weeks),
                "kontext": _context_shares([ctx.snapshot(room, item.ts) for _, item in chosen]),
            })
    return {"zeitraum": _period(cfg, start, end), "cluster": clusters}
```

- [ ] **Step 6: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_features.py -v`
Expected: PASS (8 Tests).

- [ ] **Step 7: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle bisherigen Tests PASS.

```bash
git add ai/analysis/context.py ai/analysis/features.py ai/tests/analysis_fixture.py ai/tests/test_features.py
git commit -m "feat(ai): Kontext und Verdichtung (Wochenraster und Cluster)"
```

---

### Task 5: Vokabular und Prüfung der Kandidaten

**Files:**
- Create: `ai/analysis/vocabulary.py`
- Test: `ai/tests/test_vocabulary.py`

**Interfaces:**
- Consumes: `Candidate` und Status-Konstanten (Aufgabe 1), `Pseudonyms` (Aufgabe 2).
- Produces:
  - `EVENT_NAMES: tuple[str, ...]` (alle zulässigen Ereignisse), `vocabulary_text() -> str` (deutsche Beschreibung der erlaubten Bausteine für den Prompt).
  - `@dataclass(frozen=True) ParseContext(rooms: dict[str, str], pseudo: Pseudonyms, presets: frozenset[str], flat_states: frozenset[str], min_setpoint: float = 5.0, max_setpoint: float = 30.0)`; `rooms` bildet Klartextnamen auf `area_id` ab.
  - `parse_candidate(raw, ctx) -> Candidate` wirft nie. Gültig: Status `offen`, Raum als `area_id`, Personen als Entity-ID (`person.xxx`), Sollwert als `float`. Ungültig oder unbekannt: Status `nicht_pruefbar` mit Begründung in `note`.
  - `parse_candidates(raws: list, ctx) -> list[Candidate]`.
- Kanonisches Format eines Kandidaten nach dem Parsen:
  - `trigger`: `{"type": "time", "weekdays": [int, ...], "time": "HH:MM"}` oder `{"type": "event", "event": <EVENT_NAMES>, "person": <entity_id>}` (nur bei `person_arrives`/`person_leaves`) beziehungsweise `"value": <Zustand>` (nur bei `flat_state`).
  - `conditions`: Liste aus `{"type": "weekdays", "weekdays": [...]}`, `{"type": "time_window", "from": "HH:MM", "to": "HH:MM"}`, `{"type": "outside_temp", "op": "below" | "above", "value": float}`, `{"type": "person_home", "person": <entity_id>, "home": bool}`.
  - `action`: `{"type": "preset", "value": str}` oder `{"type": "setpoint", "value": float}`.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_vocabulary.py`:

```python
import pytest

from analysis.models import STATUS_OPEN, STATUS_UNTESTABLE
from analysis.pseudonym import Pseudonyms
from analysis.vocabulary import EVENT_NAMES, ParseContext, parse_candidate, parse_candidates, vocabulary_text

PSEUDO = Pseudonyms(("person.fabian", "person.svenja"), {}, {})
CTX = ParseContext(
    rooms={"Wohnzimmer": "r_living", "Bad": "r_bath"},
    pseudo=PSEUDO,
    presets=frozenset({"eco", "comfort"}),
    flat_states=frozenset({"home", "away"}),
)


def raw(**overrides):
    base = {
        "room": "Wohnzimmer",
        "trigger": {"type": "time", "weekdays": [4, 0, 1, 2, 3, 3], "time": "17:30"},
        "conditions": [],
        "action": {"type": "setpoint", "value": 21},
        "hypothesis": "Feierabend",
    }
    base.update(overrides)
    return base


def test_valid_time_candidate_is_normalised():
    candidate = parse_candidate(raw(), CTX)
    assert candidate.status == STATUS_OPEN and candidate.note == ""
    assert candidate.room == "r_living"
    assert candidate.trigger == {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"}
    assert candidate.action == {"type": "setpoint", "value": 21.0}
    assert isinstance(candidate.action["value"], float)
    assert candidate.hypothesis == "Feierabend"


def test_person_alias_becomes_entity_id():
    trigger = {"type": "event", "event": "person_arrives", "person": "person_2"}
    candidate = parse_candidate(raw(trigger=trigger), CTX)
    assert candidate.status == STATUS_OPEN
    assert candidate.trigger == {"type": "event", "event": "person_arrives", "person": "person.svenja"}


@pytest.mark.parametrize("event", ["bed_in", "bed_out", "window_closed", "office_day_start"])
def test_plain_events_are_valid(event):
    candidate = parse_candidate(raw(trigger={"type": "event", "event": event}), CTX)
    assert candidate.status == STATUS_OPEN
    assert candidate.trigger == {"type": "event", "event": event}


def test_flat_state_event_needs_a_known_state():
    good = parse_candidate(raw(trigger={"type": "event", "event": "flat_state", "value": "away"}), CTX)
    assert good.status == STATUS_OPEN and good.trigger["value"] == "away"
    bad = parse_candidate(raw(trigger={"type": "event", "event": "flat_state", "value": "party"}), CTX)
    assert bad.status == STATUS_UNTESTABLE


def test_all_condition_types_are_accepted():
    conditions = [
        {"type": "weekdays", "weekdays": [5, 6]},
        {"type": "time_window", "from": "16:00", "to": "20:00"},
        {"type": "outside_temp", "op": "below", "value": 8},
        {"type": "person_home", "person": "person_1", "home": True},
    ]
    candidate = parse_candidate(raw(conditions=conditions), CTX)
    assert candidate.status == STATUS_OPEN
    assert candidate.conditions == [
        {"type": "weekdays", "weekdays": [5, 6]},
        {"type": "time_window", "from": "16:00", "to": "20:00"},
        {"type": "outside_temp", "op": "below", "value": 8.0},
        {"type": "person_home", "person": "person.fabian", "home": True},
    ]


def test_preset_action_must_exist_in_the_data():
    assert parse_candidate(raw(action={"type": "preset", "value": "comfort"}), CTX).status == STATUS_OPEN
    assert parse_candidate(raw(action={"type": "preset", "value": "party"}), CTX).status == STATUS_UNTESTABLE


@pytest.mark.parametrize("overrides", [
    {"room": "Keller"},
    {"room": None},
    {"room": ["Wohnzimmer"]},
    {"trigger": {"type": "magic"}},
    {"trigger": None},
    {"trigger": {"type": "time", "weekdays": [0], "time": "25:99"}},
    {"trigger": {"type": "time", "weekdays": [9], "time": "08:00"}},
    {"trigger": {"type": "time", "weekdays": [], "time": "08:00"}},
    {"trigger": {"type": "time", "weekdays": [True], "time": "08:00"}},
    {"trigger": {"type": "event", "event": "door_opens"}},
    {"trigger": {"type": "event", "event": "person_arrives", "person": "person_9"}},
    {"trigger": {"type": "event", "event": "person_arrives"}},
    {"action": {"type": "setpoint", "value": 99}},
    {"action": {"type": "setpoint", "value": "warm"}},
    {"action": {"type": "setpoint", "value": True}},
    {"action": {"type": "dim", "value": 5}},
    {"action": {"type": "preset", "value": ["comfort"]}},
    {"trigger": {"type": "event", "event": "flat_state", "value": ["away"]}},
    {"action": None},
    {"conditions": "immer"},
    {"conditions": [{"type": "outside_temp", "op": "equals", "value": 8}]},
    {"conditions": [{"type": "outside_temp", "op": "below", "value": "kalt"}]},
    {"conditions": [{"type": "time_window", "from": "20:00", "to": "16:00"}]},
    {"conditions": [{"type": "person_home", "person": "person_1"}]},
    {"conditions": [{"type": "lunar_phase"}]},
])
def test_parse_candidate_marks_garbage_as_untestable(overrides):
    candidate = parse_candidate(raw(**overrides), CTX)
    assert candidate.status == STATUS_UNTESTABLE
    assert candidate.note


@pytest.mark.parametrize("value", [None, 5, "text", [], [1, 2]])
def test_non_object_entries_never_raise(value):
    candidate = parse_candidate(value, CTX)
    assert candidate.status == STATUS_UNTESTABLE and candidate.note


def test_hypothesis_is_truncated_and_missing_hypothesis_is_empty():
    assert len(parse_candidate(raw(hypothesis="x" * 2000), CTX).hypothesis) == 500
    without = raw()
    del without["hypothesis"]
    assert parse_candidate(without, CTX).hypothesis == ""


def test_parse_candidates_keeps_order_and_mixes_status():
    result = parse_candidates([raw(), raw(room="Keller")], CTX)
    assert [c.status for c in result] == [STATUS_OPEN, STATUS_UNTESTABLE]


def test_vocabulary_text_names_every_building_block():
    text = vocabulary_text()
    for name in (*EVENT_NAMES, "time_window", "outside_temp", "person_home", "weekdays", "preset", "setpoint"):
        assert name in text
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_vocabulary.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.vocabulary'`.

- [ ] **Step 3: Implementieren**

`ai/analysis/vocabulary.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import STATUS_OPEN, STATUS_UNTESTABLE, Candidate
from .pseudonym import Pseudonyms

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PERSON_EVENTS = ("person_arrives", "person_leaves")
PLAIN_EVENTS = ("bed_in", "bed_out", "window_closed", "office_day_start")
EVENT_NAMES = (*PERSON_EVENTS, "flat_state", *PLAIN_EVENTS)


@dataclass(frozen=True)
class ParseContext:
    rooms: dict[str, str]                 # Klartextname -> area_id
    pseudo: Pseudonyms
    presets: frozenset[str]
    flat_states: frozenset[str]
    min_setpoint: float = 5.0
    max_setpoint: float = 30.0


def vocabulary_text() -> str:
    return """Erlaubte Bausteine. Verwende ausschließlich diese, nichts anderes.

Auslöser ("trigger"), genau einer:
- {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"}
    Wochentage (0 = Montag bis 6 = Sonntag) und Uhrzeit in lokaler Zeit.
- {"type": "event", "event": "person_arrives", "person": "person_2"}
    Eine Person kommt nach Hause.
- {"type": "event", "event": "person_leaves", "person": "person_2"}
    Eine Person verlässt die Wohnung.
- {"type": "event", "event": "flat_state", "value": "<Wohnungszustand aus den Daten>"}
    Der Wohnungszustand wechselt zu diesem Wert.
- {"type": "event", "event": "bed_in"} und {"type": "event", "event": "bed_out"}
    Jemand legt sich ins Bett beziehungsweise steht auf.
- {"type": "event", "event": "window_closed"}
    Alle Fenster des Raums sind geschlossen.
- {"type": "event", "event": "office_day_start"}
    Ein Büro-Tag beginnt.

Bedingungen ("conditions", optional, Liste):
- {"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]}
- {"type": "time_window", "from": "16:00", "to": "20:00"}   (from muss vor to liegen)
- {"type": "outside_temp", "op": "below", "value": 8}   ("below" oder "above", Grad Celsius)
- {"type": "person_home", "person": "person_1", "home": true}

Aktion ("action"), genau eine:
- {"type": "preset", "value": "<Preset aus den Daten>"}
- {"type": "setpoint", "value": 21.0}   (Sollwert in Grad Celsius, 5 bis 30)"""


def _mapping(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{what} fehlt oder ist kein Objekt")
    return value


def _time(value: Any) -> str:
    if not isinstance(value, str) or not TIME_RE.match(value):
        raise ValueError(f"Uhrzeit {value!r} ist ungültig (erwartet HH:MM)")
    return value


def _weekdays(value: Any) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError("weekdays muss eine nicht leere Liste sein")
    days = []
    for day in value:
        if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
            raise ValueError(f"Wochentag {day!r} ist ungültig (0 bis 6)")
        days.append(day)
    return sorted(set(days))


def _number(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} {value!r} ist keine Zahl")
    if not low <= value <= high:
        raise ValueError(f"{what} {value} liegt außerhalb von {low} bis {high}")
    return float(value)


def _person(alias: Any, ctx: ParseContext) -> str:
    entity_id = ctx.pseudo.person_for_alias(alias) if isinstance(alias, str) else None
    if entity_id is None:
        raise ValueError(f"unbekannte Person {alias!r}")
    return entity_id


def _trigger(raw: Any, ctx: ParseContext) -> dict:
    raw = _mapping(raw, "Auslöser")
    kind = raw.get("type")
    if kind == "time":
        return {"type": "time", "weekdays": _weekdays(raw.get("weekdays")), "time": _time(raw.get("time"))}
    if kind == "event":
        event = raw.get("event")
        if event in PERSON_EVENTS:
            return {"type": "event", "event": event, "person": _person(raw.get("person"), ctx)}
        if event == "flat_state":
            value = raw.get("value")
            if not isinstance(value, str) or value not in ctx.flat_states:
                raise ValueError(f"unbekannter Wohnungszustand {value!r}")
            return {"type": "event", "event": event, "value": value}
        if event in PLAIN_EVENTS:
            return {"type": "event", "event": event}
        raise ValueError(f"unbekanntes Ereignis {event!r}")
    raise ValueError(f"unbekannter Auslöser {kind!r}")


def _condition(raw: Any, ctx: ParseContext) -> dict:
    raw = _mapping(raw, "Bedingung")
    kind = raw.get("type")
    if kind == "weekdays":
        return {"type": "weekdays", "weekdays": _weekdays(raw.get("weekdays"))}
    if kind == "time_window":
        start, end = _time(raw.get("from")), _time(raw.get("to"))
        if start >= end:
            raise ValueError("time_window: from muss vor to liegen")
        return {"type": "time_window", "from": start, "to": end}
    if kind == "outside_temp":
        if raw.get("op") not in ("below", "above"):
            raise ValueError(f"outside_temp: op {raw.get('op')!r} ist ungültig")
        return {"type": "outside_temp", "op": raw["op"], "value": _number(raw.get("value"), -40, 50, "Temperatur")}
    if kind == "person_home":
        if not isinstance(raw.get("home"), bool):
            raise ValueError("person_home: home muss true oder false sein")
        return {"type": "person_home", "person": _person(raw.get("person"), ctx), "home": raw["home"]}
    raise ValueError(f"unbekannte Bedingung {kind!r}")


def _action(raw: Any, ctx: ParseContext) -> dict:
    raw = _mapping(raw, "Aktion")
    kind = raw.get("type")
    if kind == "preset":
        if not isinstance(raw.get("value"), str) or raw["value"] not in ctx.presets:
            raise ValueError(f"unbekanntes Preset {raw.get('value')!r}")
        return {"type": "preset", "value": raw["value"]}
    if kind == "setpoint":
        return {"type": "setpoint", "value": _number(raw.get("value"), ctx.min_setpoint, ctx.max_setpoint, "Sollwert")}
    raise ValueError(f"unbekannte Aktion {kind!r}")


def parse_candidate(raw: Any, ctx: ParseContext) -> Candidate:
    """Prüft einen Kandidaten des Modells gegen das Vokabular. Wirft nie."""
    if not isinstance(raw, dict):
        return Candidate(room="", trigger={}, action={}, status=STATUS_UNTESTABLE,
                         note="Eintrag ist kein Objekt")
    hypothesis = str(raw.get("hypothesis") or "")[:500]
    name = raw.get("room")
    try:
        if not isinstance(name, str) or name not in ctx.rooms:
            raise ValueError(f"unbekannter Raum {name!r}")
        conditions_raw = raw.get("conditions") or []
        if not isinstance(conditions_raw, list):
            raise ValueError("conditions muss eine Liste sein")
        return Candidate(
            room=ctx.rooms[name],
            trigger=_trigger(raw.get("trigger"), ctx),
            conditions=[_condition(item, ctx) for item in conditions_raw],
            action=_action(raw.get("action"), ctx),
            hypothesis=hypothesis,
            status=STATUS_OPEN,
        )
    except ValueError as exc:
        trigger = raw.get("trigger") if isinstance(raw.get("trigger"), dict) else {}
        action = raw.get("action") if isinstance(raw.get("action"), dict) else {}
        return Candidate(room=name if isinstance(name, str) else "", trigger=trigger, action=action,
                         hypothesis=hypothesis, status=STATUS_UNTESTABLE, note=str(exc))


def parse_candidates(raws: list, ctx: ParseContext) -> list[Candidate]:
    return [parse_candidate(item, ctx) for item in raws]
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_vocabulary.py -v`
Expected: PASS (42 Tests).

- [ ] **Step 5: Commit**

```bash
git add ai/analysis/vocabulary.py ai/tests/test_vocabulary.py
git commit -m "feat(ai): geschlossenes Vokabular und Prüfung der Kandidaten"
```

---

### Task 6: Backtest

**Files:**
- Create: `ai/analysis/backtest.py`
- Test: `ai/tests/test_backtest.py`

**Interfaces:**
- Consumes: `AnalysisConfig` (`backtest`, `timezone`, `exclusions`), `ContextProvider` (`person_timeline`, `flat_state`, `in_bed`, `office_day`, `outside_temp`, `windows(room)`), `Timeline`, `Intervention`, `Candidate`, Status-Konstanten, `excluded` (Aufgabe 3).
- Produces: `NotTestable(Exception)`; `@dataclass BacktestResult(status: str, note: str, metrics: dict[str, float])`; `Backtester(cfg, interventions, ctx, start, end)` mit `evaluate(candidate) -> BacktestResult`. Metriken: `ausloesungen`, `faelle`, `wochen`, `eingriffe_gesamt`, `abdeckung`, `fehlausloesungen` (bei `nicht_pruefbar` leer). Interventionen müssen bereits auf Zeitraum und Ausschlüsse gefiltert sein (so liefert sie `load_interventions`); die Auslöser werden hier auf `[start, end)` und die Ausschlüsse begrenzt.
- Definitionen: Ein Auslöser hat einen **Fall**, wenn im Raum innerhalb von `match_window_min` Minuten danach ein Eingriff gleicher Aktion folgt (Preset gleich beziehungsweise Sollwert innerhalb von `setpoint_tolerance`). Eine **Fehlauslösung** ist ein Auslöser ohne Fall oder mit einem Eingriff gleicher Art (Sollwert beziehungsweise Preset) mit anderem Wert innerhalb von `contrary_window_min` Minuten danach. `abdeckung` = vorweggenommene Eingriffe / alle Eingriffe mit passender Aktion im Raum; `fehlausloesungen` = Fehlauslösungen / Auslösungen; `wochen` = verschiedene ISO-Kalenderwochen der Fälle (lokale Zeit).

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_backtest.py`:

```python
from dataclasses import replace
from datetime import date, timedelta

import pytest

from analysis.backtest import Backtester
from analysis.config import Exclusion
from analysis.context import ContextProvider
from analysis.models import (STATUS_FEW, STATUS_PASSED, STATUS_REJECTED, STATUS_UNTESTABLE,
                             Candidate, Intervention)
from analysis.pseudonym import Pseudonyms
from analysis.timeline import Timeline
from tests.analysis_fixture import local_utc

BASE = date(2026, 1, 5)                      # ein Montag, Winterzeit
START = local_utc(BASE, "00:00")
END = local_utc(BASE + timedelta(days=42), "00:00")


def day(n):
    return BASE + timedelta(days=n)


def iv(n, hhmm, value=21.0, kind="setpoint", room="r"):
    return Intervention(local_utc(day(n), hhmm), room, "climate.x", kind, value)


def rule(trigger, *, action=None, conditions=None, room="r"):
    return Candidate(room=room, trigger=trigger, conditions=conditions or [],
                     action=action or {"type": "setpoint", "value": 21.0})


def time_rule(hhmm="17:30", weekdays=(0, 1, 2, 3, 4)):
    return {"type": "time", "weekdays": list(weekdays), "time": hhmm}


def backtester(cfg, interventions, ctx=None, start=START, end=END):
    return Backtester(cfg, interventions, ctx or ContextProvider.empty(), start, end)


def weekday_interventions(hhmm="17:32", value=21.0):
    return [iv(n, hhmm, value) for n in range(42) if day(n).weekday() < 5]


def with_persons(timelines):
    return ContextProvider(Pseudonyms(tuple(timelines), {}, {}), persons=timelines)


def test_time_trigger_with_matching_habit_passes(acfg):
    result = backtester(acfg, weekday_interventions()).evaluate(rule(time_rule()))
    assert result.status == STATUS_PASSED
    assert result.metrics == {"ausloesungen": 30.0, "faelle": 30.0, "wochen": 6.0,
                              "eingriffe_gesamt": 30.0, "abdeckung": 1.0, "fehlausloesungen": 0.0}


def test_time_trigger_follows_local_time_across_dst(acfg):
    start, end = local_utc(date(2026, 3, 24), "00:00"), local_utc(date(2026, 4, 3), "00:00")
    items = [Intervention(local_utc(date(2026, 3, 25) + timedelta(days=k), "07:05"),
                          "r", "climate.x", "setpoint", 21.0) for k in range(9)]   # 25.03. bis 02.04.
    result = backtester(acfg, items, start=start, end=end).evaluate(
        rule(time_rule("07:00", range(7)), action={"type": "setpoint", "value": 21.0}))
    assert result.metrics["faelle"] == 9.0               # Sommerzeit am 29.03. verschiebt nichts
    assert result.metrics["abdeckung"] == 1.0
    assert result.metrics["fehlausloesungen"] == pytest.approx(0.1)   # 24.03. ohne Eingriff


def test_trigger_without_any_intervention_is_a_false_trigger(acfg):
    result = backtester(acfg, weekday_interventions()).evaluate(rule(time_rule("09:00")))
    assert result.status == STATUS_FEW
    assert result.metrics["faelle"] == 0.0 and result.metrics["fehlausloesungen"] == 1.0


def test_contrary_intervention_within_an_hour_counts_as_false_trigger(acfg):
    items = [iv(n, "17:32", 21.0) for n in range(10)] + [iv(n, "18:00", 24.0) for n in range(10)]
    ten_days = local_utc(day(10), "00:00")              # genau die zehn Tage mit Eingriffen
    result = backtester(acfg, items, end=ten_days).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["faelle"] == 10.0
    assert result.metrics["fehlausloesungen"] == 1.0


def test_low_coverage_is_rejected(acfg):
    items = [iv(7 * week, "17:32") for week in range(6)]                  # nur 6 Fälle, je eine Woche
    items += [iv(n, "10:00") for n in range(42) if day(n).weekday() < 5]  # 30 Eingriffe zu anderer Zeit
    result = backtester(acfg, items).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["faelle"] == 6.0 and result.metrics["wochen"] == 6.0
    assert result.metrics["abdeckung"] == pytest.approx(6 / 36)
    assert result.status == STATUS_REJECTED


def test_too_few_cases_or_weeks_means_not_enough_evidence(acfg):
    items = [iv(n, "17:32") for n in range(4)]
    result = backtester(acfg, items).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.status == STATUS_FEW and "4" in result.note
    two_weeks = [iv(n, "17:32") for n in range(10)]
    result = backtester(acfg, two_weeks).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["faelle"] == 10.0 and result.metrics["wochen"] == 2.0
    assert result.status == STATUS_FEW


def test_preset_action_matches_exact_value_only(acfg):
    items = [iv(n, "17:32", "comfort", kind="preset") for n in range(42) if day(n).weekday() < 5]
    good = backtester(acfg, items).evaluate(rule(time_rule(), action={"type": "preset", "value": "comfort"}))
    assert good.status == STATUS_PASSED
    other = backtester(acfg, items).evaluate(rule(time_rule(), action={"type": "preset", "value": "eco"}))
    assert other.metrics["faelle"] == 0.0


def test_setpoint_tolerance_is_respected(acfg):
    items = weekday_interventions(value=21.4)
    assert backtester(acfg, items).evaluate(rule(time_rule())).metrics["faelle"] == 30.0
    far = weekday_interventions(value=22.0)
    assert backtester(acfg, far).evaluate(rule(time_rule())).metrics["faelle"] == 0.0


def arrival_context(days=21):
    points = []
    for n in range(days):
        points += [(local_utc(day(n), "07:00"), "not_home"), (local_utc(day(n), "17:20"), "home")]
    return with_persons({"person.svenja": Timeline(points)})


def test_person_arrives_trigger_passes_for_the_known_habit(acfg):
    items = [iv(n, "17:35") for n in range(21)]
    trigger = {"type": "event", "event": "person_arrives", "person": "person.svenja"}
    result = backtester(acfg, items, arrival_context()).evaluate(rule(trigger))
    assert result.status == STATUS_PASSED
    assert result.metrics["ausloesungen"] == 21.0 and result.metrics["wochen"] == 3.0


def test_person_leaves_uses_the_departure_instant(acfg):
    points = []
    for n in range(21):
        points += [(local_utc(day(n), "07:00"), "home"), (local_utc(day(n), "08:10"), "not_home")]
    ctx = with_persons({"person.svenja": Timeline(points)})
    items = [iv(n, "08:20", 17.0) for n in range(21)]
    trigger = {"type": "event", "event": "person_leaves", "person": "person.svenja"}
    result = backtester(acfg, items, ctx).evaluate(rule(trigger, action={"type": "setpoint", "value": 17.0}))
    assert result.metrics["faelle"] == 21.0


def test_conditions_filter_the_triggers(acfg):
    items = [iv(n, "17:35") for n in range(21)]
    trigger = {"type": "event", "event": "person_arrives", "person": "person.svenja"}
    weekdays = [{"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]}]
    result = backtester(acfg, items, arrival_context()).evaluate(rule(trigger, conditions=weekdays))
    assert result.metrics["ausloesungen"] == 15.0
    window = [{"type": "time_window", "from": "18:00", "to": "23:00"}]
    result = backtester(acfg, items, arrival_context()).evaluate(rule(trigger, conditions=window))
    assert result.metrics["ausloesungen"] == 0.0


def test_outside_temperature_condition(acfg):
    ctx = ContextProvider.empty()
    ctx.outside_temp = Timeline([(local_utc(day(0), "00:00"), "5.0"), (local_utc(day(7), "00:00"), "12.0")])
    below = [{"type": "outside_temp", "op": "below", "value": 8}]
    above = [{"type": "outside_temp", "op": "above", "value": 8}]
    cold = backtester(acfg, [], ctx).evaluate(rule(time_rule(weekdays=range(7)), conditions=below))
    warm = backtester(acfg, [], ctx).evaluate(rule(time_rule(weekdays=range(7)), conditions=above))
    assert cold.metrics["ausloesungen"] == 7.0 and warm.metrics["ausloesungen"] == 35.0
    unknown = backtester(acfg, []).evaluate(rule(time_rule(weekdays=range(7)), conditions=below))
    assert unknown.metrics["ausloesungen"] == 0.0         # ohne Messwert gilt die Bedingung nicht


def test_person_home_condition(acfg):
    ctx = arrival_context()
    three_weeks = local_utc(day(21), "00:00")           # so lange gibt es Daten für die Person
    home = [{"type": "person_home", "person": "person.svenja", "home": True}]
    result = backtester(acfg, [], ctx, end=three_weeks).evaluate(rule(time_rule("18:00", range(7)), conditions=home))
    assert result.metrics["ausloesungen"] == 21.0        # um 18:00 ist sie zu Hause
    away = [{"type": "person_home", "person": "person.svenja", "home": False}]
    result = backtester(acfg, [], ctx, end=three_weeks).evaluate(rule(time_rule("12:00", range(7)), conditions=away))
    assert result.metrics["ausloesungen"] == 21.0
    result = backtester(acfg, [], ctx, end=three_weeks).evaluate(rule(time_rule("12:00", range(7)), conditions=home))
    assert result.metrics["ausloesungen"] == 0.0         # mittags ist sie nicht zu Hause


def test_flat_state_bed_and_office_events(acfg):
    ctx = ContextProvider.empty()
    ctx.flat_state = Timeline([(local_utc(day(0), "08:00"), "home"), (local_utc(day(1), "08:00"), "away"),
                               (local_utc(day(2), "08:00"), "home"), (local_utc(day(3), "08:00"), "away")])
    ctx.in_bed = Timeline([(local_utc(day(0), "22:00"), "off"), (local_utc(day(0), "23:00"), "on"),
                           (local_utc(day(1), "07:00"), "off")])
    ctx.office_day = Timeline([(local_utc(day(0), "06:00"), "off"), (local_utc(day(2), "06:00"), "on")])
    flat = backtester(acfg, [], ctx).evaluate(rule({"type": "event", "event": "flat_state", "value": "away"}))
    assert flat.metrics["ausloesungen"] == 2.0
    for event, count in (("bed_in", 1.0), ("bed_out", 1.0), ("office_day_start", 1.0)):
        assert backtester(acfg, [], ctx).evaluate(rule({"type": "event", "event": event})).metrics[
            "ausloesungen"] == count


def test_window_closed_trigger_waits_for_the_last_window(acfg):
    windows = {"r": [
        Timeline([(local_utc(day(n), "17:00"), "on") for n in range(21)]
                 + [(local_utc(day(n), "17:10"), "off") for n in range(21)]),
        Timeline([(local_utc(day(n), "17:05"), "on") for n in range(21)]
                 + [(local_utc(day(n), "17:20"), "off") for n in range(21)]),
    ]}
    ctx = ContextProvider(Pseudonyms((), {}, {}), windows=windows)
    items = [iv(n, "17:25", 19.0) for n in range(21)]
    result = backtester(acfg, items, ctx).evaluate(
        rule({"type": "event", "event": "window_closed"}, action={"type": "setpoint", "value": 19.0}))
    assert result.metrics["ausloesungen"] == 21.0         # 17:20, nicht 17:10
    assert result.metrics["faelle"] == 21.0


def test_missing_window_sensors_make_candidate_untestable(acfg):
    result = backtester(acfg, weekday_interventions()).evaluate(rule({"type": "event", "event": "window_closed"}))
    assert result.status == STATUS_UNTESTABLE and result.metrics == {} and result.note


@pytest.mark.parametrize("trigger", [
    {"type": "event", "event": "person_arrives", "person": "person.unbekannt"},
    {"type": "event", "event": "flat_state", "value": "away"},
    {"type": "event", "event": "bed_in"},
    {"type": "event", "event": "bed_out"},
    {"type": "event", "event": "office_day_start"},
])
def test_missing_context_data_makes_event_candidates_untestable(acfg, trigger):
    result = backtester(acfg, weekday_interventions()).evaluate(rule(trigger))
    assert result.status == STATUS_UNTESTABLE and result.note


def test_exclusion_removes_triggers_of_that_room_only(acfg):
    cfg = replace(acfg, exclusions=(Exclusion("r", local_utc(day(21), "00:00"), None),))
    result = backtester(cfg, []).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["ausloesungen"] == 21.0
    other = replace(acfg, exclusions=(Exclusion("andere", START, None),))
    assert backtester(other, []).evaluate(rule(time_rule(weekdays=range(7)))).metrics["ausloesungen"] == 42.0


def test_interventions_of_other_rooms_and_kinds_are_ignored(acfg):
    items = weekday_interventions()
    other_room = [Intervention(i.ts, "anderer", i.entity_id, i.kind, i.value) for i in items]
    assert backtester(acfg, other_room).evaluate(rule(time_rule())).metrics["faelle"] == 0.0
    other_kind = [Intervention(i.ts, i.room, i.entity_id, "preset", "comfort") for i in items]
    assert backtester(acfg, other_kind).evaluate(rule(time_rule())).metrics["faelle"] == 0.0


def test_backtest_is_reproducible(acfg):
    items = weekday_interventions()
    first = backtester(acfg, items).evaluate(rule(time_rule()))
    second = backtester(acfg, items).evaluate(rule(time_rule()))
    assert first == second
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_backtest.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.backtest'`.

- [ ] **Step 3: Implementieren**

`ai/analysis/backtest.py`:

```python
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import AnalysisConfig
from .context import UNKNOWN, ContextProvider
from .interventions import excluded
from .models import (STATUS_FEW, STATUS_PASSED, STATUS_REJECTED, STATUS_UNTESTABLE, Candidate,
                     Intervention)


class NotTestable(Exception):
    pass


@dataclass
class BacktestResult:
    status: str
    note: str
    metrics: dict[str, float]


class Backtester:
    """Rechnet Kandidaten gegen die Historie nach. Kein LLM beteiligt."""

    def __init__(self, cfg: AnalysisConfig, interventions: list[Intervention], ctx: ContextProvider,
                 start: datetime, end: datetime):
        self.cfg = cfg
        self.ctx = ctx
        self.start = start
        self.end = end
        self.tz = ZoneInfo(cfg.timezone)
        self._pool: dict[tuple[str, str], list[Intervention]] = defaultdict(list)
        for item in sorted(interventions, key=lambda entry: entry.ts):
            self._pool[(item.room, item.kind)].append(item)

    def evaluate(self, candidate: Candidate) -> BacktestResult:
        try:
            instants = self._trigger_instants(candidate)
        except NotTestable as exc:
            return BacktestResult(STATUS_UNTESTABLE, str(exc), {})
        instants = [instant for instant in instants
                    if self.start <= instant < self.end
                    and not excluded(self.cfg, candidate.room, instant)
                    and self._conditions_hold(candidate, instant)]
        return self._measure(candidate, instants)

    # --- Auslöser ---------------------------------------------------------------

    def _trigger_instants(self, candidate: Candidate) -> list[datetime]:
        trigger = candidate.trigger
        if trigger["type"] == "time":
            return self._time_instants(trigger)
        return self._event_instants(trigger, candidate.room)

    def _time_instants(self, trigger: dict) -> list[datetime]:
        hour, minute = (int(part) for part in trigger["time"].split(":"))
        current = self.start.astimezone(self.tz).date()
        last = self.end.astimezone(self.tz).date()
        instants = []
        while current <= last:
            if current.weekday() in trigger["weekdays"]:
                local = datetime(current.year, current.month, current.day, hour, minute, tzinfo=self.tz)
                instants.append(local.astimezone(timezone.utc))
            current += timedelta(days=1)
        return instants

    def _event_instants(self, trigger: dict, room: str) -> list[datetime]:
        event = trigger["event"]
        if event in ("person_arrives", "person_leaves"):
            timeline = self.ctx.person_timeline(trigger["person"])
            if not timeline:
                raise NotTestable("keine Daten für die Person")
            result = []
            for ts, old, new in timeline.transitions():
                if old in UNKNOWN or new in UNKNOWN:
                    continue
                if (event == "person_arrives" and new == "home") or (event == "person_leaves" and old == "home"):
                    result.append(ts)
            return result
        if event == "flat_state":
            if not self.ctx.flat_state:
                raise NotTestable("kein Wohnungszustand aufgezeichnet")
            return [ts for ts, old, new in self.ctx.flat_state.transitions()
                    if new == trigger["value"] and old not in UNKNOWN]
        if event in ("bed_in", "bed_out"):
            if not self.ctx.in_bed:
                raise NotTestable("kein Bett-Sensor aufgezeichnet")
            old_state, new_state = ("off", "on") if event == "bed_in" else ("on", "off")
            return [ts for ts, old, new in self.ctx.in_bed.transitions() if old == old_state and new == new_state]
        if event == "office_day_start":
            if not self.ctx.office_day:
                raise NotTestable("kein Büro-Tag-Sensor aufgezeichnet")
            return [ts for ts, old, new in self.ctx.office_day.transitions() if old == "off" and new == "on"]
        if event == "window_closed":
            windows = [timeline for timeline in self.ctx.windows(room) if timeline]
            if not windows:
                raise NotTestable("keine Fensterkontakte im Raum")
            result = set()
            for timeline in windows:
                for ts, old, new in timeline.transitions():
                    if old == "on" and new == "off" and all(other.value_at(ts) == "off" for other in windows):
                        result.add(ts)
            return sorted(result)
        raise NotTestable(f"unbekanntes Ereignis {event}")

    # --- Bedingungen ------------------------------------------------------------

    def _conditions_hold(self, candidate: Candidate, instant: datetime) -> bool:
        local = instant.astimezone(self.tz)
        for condition in candidate.conditions:
            kind = condition["type"]
            if kind == "weekdays":
                if local.weekday() not in condition["weekdays"]:
                    return False
            elif kind == "time_window":
                if not condition["from"] <= local.strftime("%H:%M") <= condition["to"]:
                    return False
            elif kind == "outside_temp":
                value = self.ctx.outside_temp.numeric_at(instant)
                if value is None:
                    return False
                if condition["op"] == "below" and not value < condition["value"]:
                    return False
                if condition["op"] == "above" and not value > condition["value"]:
                    return False
            elif kind == "person_home":
                state = self.ctx.person_timeline(condition["person"]).value_at(instant)
                if state in UNKNOWN or (state == "home") != condition["home"]:
                    return False
        return True

    # --- Messung ----------------------------------------------------------------

    def _matches(self, item: Intervention, action: dict) -> bool:
        if action["type"] == "preset":
            return item.value == action["value"]
        return abs(float(item.value) - float(action["value"])) <= self.cfg.backtest.setpoint_tolerance

    def _measure(self, candidate: Candidate, instants: list[datetime]) -> BacktestResult:
        params = self.cfg.backtest
        action = candidate.action
        pool = self._pool.get((candidate.room, action["type"]), [])
        times = [item.ts for item in pool]
        fits = [self._matches(item, action) for item in pool]
        total = sum(fits)
        match_window = timedelta(minutes=params.match_window_min)
        contrary_window = timedelta(minutes=params.contrary_window_min)
        reach = max(match_window, contrary_window)
        covered: set[int] = set()
        weeks: set[tuple[int, int]] = set()
        cases = false_triggers = 0
        for instant in instants:
            low = bisect_right(times, instant)
            high = bisect_right(times, instant + reach)
            matched = [index for index in range(low, high) if fits[index] and times[index] <= instant + match_window]
            contrary = any(not fits[index] and times[index] <= instant + contrary_window
                           for index in range(low, high))
            if matched:
                cases += 1
                covered.update(matched)
                iso = instant.astimezone(self.tz).isocalendar()
                weeks.add((iso[0], iso[1]))
            if not matched or contrary:
                false_triggers += 1
        coverage = len(covered) / total if total else 0.0
        false_rate = false_triggers / len(instants) if instants else 0.0
        metrics = {"ausloesungen": float(len(instants)), "faelle": float(cases), "wochen": float(len(weeks)),
                   "eingriffe_gesamt": float(total), "abdeckung": coverage, "fehlausloesungen": false_rate}
        if cases < params.min_faelle or len(weeks) < params.min_wochen:
            return BacktestResult(
                STATUS_FEW,
                f"{cases} Fälle in {len(weeks)} Wochen (nötig: {params.min_faelle} Fälle in {params.min_wochen} Wochen)",
                metrics)
        if coverage >= params.min_abdeckung and false_rate <= params.max_fehlausloesungen:
            return BacktestResult(STATUS_PASSED, "", metrics)
        return BacktestResult(
            STATUS_REJECTED,
            f"Abdeckung {coverage:.2f} (Soll ab {params.min_abdeckung}), "
            f"Fehlauslösungen {false_rate:.2f} (Soll bis {params.max_fehlausloesungen})",
            metrics)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_backtest.py -v`
Expected: PASS (24 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle bisherigen Tests PASS.

```bash
git add ai/analysis/backtest.py ai/tests/test_backtest.py
git commit -m "feat(ai): Backtest mit Abdeckung, Fehlauslösungen und Belegen"
```

---

### Task 7: Prompts und LLM-Client

**Files:**
- Create: `ai/analysis/prompts.py`, `ai/analysis/llm.py`, `ai/tests/fake_openrouter.py`
- Test: `ai/tests/test_llm.py`
- Modify: `ai/tests/conftest.py` (Fixture `fake_llm` ergänzen)

**Interfaces:**
- Consumes: `AnalysisConfig` (`model`, `openrouter_url`, `provider`, `temperature`, `max_output_tokens`, `request_timeout_s`, `retries`, `retry_backoff_s`, `price_input`, `price_output`, `max_cost_usd`, `max_candidates`).
- Produces:
  - `analysis.prompts.build_messages(mode, data, vocabulary, max_candidates, *, presets, flat_states, rooms) -> list[dict]` (zwei Nachrichten: `system`, `user`); `mode` ist `"weekly"` oder `"clusters"`, sonst `ValueError`.
  - `analysis.llm`: Ausnahmen `LlmError`, `ZdrUnavailable(LlmError)`, `InvalidResponse(LlmError)`, `CostLimitExceeded(LlmError)`; `@dataclass LlmResult(text, tokens_in, tokens_out, cost_usd)`; `estimate_cost(messages, cfg) -> float`; `async complete(cfg, messages, api_key, *, sleep=asyncio.sleep) -> LlmResult`; `parse_candidates_json(text, max_candidates) -> list[dict]`; `async request_candidates(cfg, messages, api_key, *, sleep=asyncio.sleep) -> tuple[list[dict], LlmResult]` (der Rückgabewert `LlmResult` summiert Token und Kosten aller Versuche, `text` ist die letzte Antwort).
  - Test-Attrappe `FakeOpenRouter` mit `url`, `responses: list[dict]` (je Aufruf ein Eintrag mit `content`, optional `status`, `message`, `tokens_in`, `tokens_out`, `cost`; `cost: None` lässt das Feld weg), `requests: list[dict]` (`payload`, `auth`); Fixture `fake_llm`.

- [ ] **Step 1: Attrappe und Fixture schreiben**

`ai/tests/fake_openrouter.py`:

```python
from __future__ import annotations

from aiohttp import web


class FakeOpenRouter:
    """Kleine Attrappe der OpenRouter-Chat-Schnittstelle für Tests."""

    def __init__(self):
        self.url = ""
        self.responses: list[dict] = []
        self.requests: list[dict] = []

    async def handler(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.requests.append({"payload": payload, "auth": request.headers.get("Authorization")})
        item = self.responses.pop(0) if self.responses else {"content": '{"candidates": []}'}
        status = item.get("status", 200)
        if status != 200:
            return web.json_response({"error": {"message": item.get("message", "Fehler")}}, status=status)
        usage = {"prompt_tokens": item.get("tokens_in", 100), "completion_tokens": item.get("tokens_out", 50)}
        if item.get("cost", 0.01) is not None:
            usage["cost"] = item.get("cost", 0.01)
        return web.json_response({
            "choices": [{"message": {"role": "assistant", "content": item["content"]}}],
            "usage": usage,
        })
```

An `ai/tests/conftest.py` anhängen (Import oben bei den anderen, Fixture unten):

```python
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402


@pytest_asyncio.fixture
async def fake_llm():
    fake = FakeOpenRouter()
    app = web.Application()
    app.router.add_post("/api/v1/chat/completions", fake.handler)
    server = TestServer(app)
    await server.start_server()
    fake.url = f"http://127.0.0.1:{server.port}/api/v1/chat/completions"
    try:
        yield fake
    finally:
        await server.close()
```

- [ ] **Step 2: Tests schreiben**

`ai/tests/test_llm.py`:

```python
import json
from dataclasses import replace

import pytest

from analysis.llm import (CostLimitExceeded, InvalidResponse, LlmError, ZdrUnavailable, complete,
                          estimate_cost, parse_candidates_json, request_candidates)
from analysis.prompts import build_messages

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
VALID = json.dumps({"candidates": [{"room": "Wohnzimmer"}]})


async def no_sleep(_seconds):
    return None


def config(acfg, fake, **overrides):
    return replace(acfg, openrouter_url=fake.url, retry_backoff_s=0.0, **overrides)


# --- Prompts -------------------------------------------------------------------

def test_build_messages_contains_data_vocabulary_and_limits():
    messages = build_messages("weekly", {"a": 1}, "VOKABULAR", 20, presets=["comfort", "eco"],
                              flat_states=["home"], rooms=["Wohnzimmer"])
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "VOKABULAR" in messages[0]["content"] and "höchstens 20" in messages[0]["content"]
    assert "JSON" in messages[0]["content"]
    user = messages[1]["content"]
    assert '{"a":1}' in user
    assert "comfort" in user and "eco" in user and "home" in user and "Wohnzimmer" in user


def test_modes_have_different_instructions_and_unknown_mode_raises():
    weekly = build_messages("weekly", {}, "V", 5, presets=[], flat_states=[], rooms=[])
    clusters = build_messages("clusters", {}, "V", 5, presets=[], flat_states=[], rooms=[])
    assert weekly[1]["content"].splitlines()[0] != clusters[1]["content"].splitlines()[0]
    assert weekly[0]["content"] == clusters[0]["content"]
    with pytest.raises(ValueError):
        build_messages("anders", {}, "V", 5, presets=[], flat_states=[], rooms=[])


# --- Antwort lesen -----------------------------------------------------------------

def test_parse_candidates_json_strips_markdown_fences():
    fenced = "```json\n" + VALID + "\n```"
    assert parse_candidates_json(fenced, 20) == [{"room": "Wohnzimmer"}]


def test_parse_candidates_json_accepts_bare_list_and_truncates():
    text = json.dumps([{"n": i} for i in range(5)])
    assert parse_candidates_json(text, 3) == [{"n": 0}, {"n": 1}, {"n": 2}]


@pytest.mark.parametrize("text", ["", "kein json", "{}", '{"candidates": "x"}', '{"candidates": [1]}', "42"])
def test_parse_candidates_json_rejects_bad_shapes(text):
    with pytest.raises(InvalidResponse):
        parse_candidates_json(text, 20)


# --- Aufruf ----------------------------------------------------------------------------

async def test_complete_sends_zdr_provider_key_and_settings(acfg, fake_llm):
    fake_llm.responses = [{"content": VALID}]
    result = await complete(config(acfg, fake_llm), MESSAGES, "geheim", sleep=no_sleep)
    assert (result.text, result.tokens_in, result.tokens_out, result.cost_usd) == (VALID, 100, 50, 0.01)
    (request,) = fake_llm.requests
    payload = request["payload"]
    assert request["auth"] == "Bearer geheim"
    assert payload["provider"] == {"zdr": True, "data_collection": "deny"}
    assert payload["model"] == acfg.model and payload["temperature"] == acfg.temperature
    assert payload["max_tokens"] == acfg.max_output_tokens and payload["messages"] == MESSAGES


async def test_cost_falls_back_to_estimate_from_tokens(acfg, fake_llm):
    fake_llm.responses = [{"content": VALID, "tokens_in": 1000, "tokens_out": 500, "cost": None}]
    result = await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert result.cost_usd == pytest.approx(1000 * 3.0 / 1e6 + 500 * 15.0 / 1e6)


async def test_missing_zdr_provider_raises_zdr_unavailable(acfg, fake_llm):
    fake_llm.responses = [{"status": 404, "message": "No endpoints found matching your data policy"}]
    with pytest.raises(ZdrUnavailable, match="Zero Data Retention"):
        await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 1


async def test_server_errors_are_retried(acfg, fake_llm):
    fake_llm.responses = [{"status": 503}, {"status": 429}, {"content": VALID}]
    result = await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert result.text == VALID and len(fake_llm.requests) == 3


async def test_gives_up_after_the_configured_retries(acfg, fake_llm):
    fake_llm.responses = [{"status": 503}] * 10
    with pytest.raises(LlmError, match="fehlgeschlagen"):
        await complete(config(acfg, fake_llm, retries=2), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 3


async def test_client_errors_are_not_retried(acfg, fake_llm):
    fake_llm.responses = [{"status": 401, "message": "bad key"}]
    with pytest.raises(LlmError, match="401"):
        await complete(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 1


async def test_unreachable_server_raises_llm_error(acfg):
    cfg = replace(acfg, openrouter_url="http://127.0.0.1:9/x", retries=1, retry_backoff_s=0.0)
    with pytest.raises(LlmError, match="fehlgeschlagen"):
        await complete(cfg, MESSAGES, "k", sleep=no_sleep)


# --- Kandidaten anfragen -----------------------------------------------------------------

async def test_invalid_answer_is_retried_once_with_the_error(acfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": VALID}]
    items, total = await request_candidates(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert items == [{"room": "Wohnzimmer"}]
    assert len(fake_llm.requests) == 2
    second = fake_llm.requests[1]["payload"]["messages"]
    assert second[:2] == MESSAGES
    assert second[2] == {"role": "assistant", "content": "kein json"}
    assert "ungültig" in second[3]["content"] and "JSON" in second[3]["content"]
    assert (total.tokens_in, total.tokens_out) == (200, 100)
    assert total.cost_usd == pytest.approx(0.02)


async def test_second_invalid_answer_raises(acfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": "immer noch nicht"}]
    with pytest.raises(InvalidResponse):
        await request_candidates(config(acfg, fake_llm), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 2


async def test_cost_limit_blocks_the_call(acfg, fake_llm):
    with pytest.raises(CostLimitExceeded):
        await request_candidates(config(acfg, fake_llm, max_cost_usd=0.0001), MESSAGES, "k", sleep=no_sleep)
    assert fake_llm.requests == []


async def test_cost_limit_counts_what_the_first_attempt_already_cost(acfg, fake_llm):
    fake_llm.responses = [{"content": "kein json", "cost": 1.9}]
    with pytest.raises(CostLimitExceeded):
        await request_candidates(config(acfg, fake_llm, max_cost_usd=2.0), MESSAGES, "k", sleep=no_sleep)
    assert len(fake_llm.requests) == 1


def test_estimate_cost_uses_prices_and_output_limit(acfg):
    cost = estimate_cost([{"role": "user", "content": "x" * 3000}], acfg)
    assert cost == pytest.approx(1000 * 3.0 / 1e6 + acfg.max_output_tokens * 15.0 / 1e6)
```

- [ ] **Step 3: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_llm.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.llm'`.

- [ ] **Step 4: Prompts implementieren**

`ai/analysis/prompts.py`:

```python
from __future__ import annotations

import json

SYSTEM_PROMPT = """Du analysierst das Heizverhalten einer Wohnung und schlägst Regeln vor, die ein System später deterministisch ausführen kann.

Grundlage sind verdichtete Daten über manuelle Eingriffe der Bewohner in die Raumtemperatur (Sollwert oder Preset) samt Kontext. Personen sind pseudonymisiert (person_1, person_2, ...), Räume stehen im Klartext. Alle Zeiten sind lokale Zeiten, Wochentage zählen von 0 (Montag) bis 6 (Sonntag).

Deine Aufgabe: Finde wiederkehrende Muster und formuliere sie als Regelkandidaten. Ein Kandidat sagt: Wenn der Auslöser eintritt (und die Bedingungen gelten), sollte im Raum die Aktion ausgeführt werden.

Regeln für dich:
- Schlage nur Muster vor, die in den Daten belegt sind. Erfinde keine Zahlen und keine Gewohnheiten.
- Verwende ausschließlich die erlaubten Bausteine unten, mit genau diesen Schlüsseln und Werten.
- Verwende nur Räume, Presets und Wohnungszustände aus den Angaben der Nachricht.
- Gib höchstens {max_candidates} Kandidaten zurück, die aussagekräftigsten zuerst.
- Die Hypothese ist ein kurzer deutscher Satz (höchstens 200 Zeichen), warum das Muster auftritt. Sie darf keine Zahlen behaupten.
- Das System prüft jeden Kandidaten anschließend gegen die Daten. Trefferquoten und Zählungen gehören nicht in deine Antwort.

{vocabulary}

Antwortformat: ausschließlich ein JSON-Objekt, ohne Text davor oder danach:
{"candidates": [{"room": "<Raum>", "trigger": {...}, "conditions": [...], "action": {...}, "hypothesis": "<Satz>"}]}"""

MODE_INTROS = {
    "weekly": (
        "Modus: Wochenraster. Die Daten zeigen je Raum, Wochentag und Stunde, wie oft manuell eingegriffen "
        "wurde, welche Aktion am häufigsten war und welche Personen dabei zu Hause waren; dazu je Wochentag "
        "den durchschnittlichen Wohnungszustand und die Anwesenheit. Suche selbst nach Mustern."
    ),
    "clusters": (
        "Modus: Cluster. Die Daten zeigen gebündelte Eingriffe (gleicher Raum, gleiche Aktion, ähnliche "
        "Uhrzeit, gleiche Wochentagsgruppe) mit dem Kontext zum Zeitpunkt der Eingriffe als Anteile. "
        "Formuliere zu den Clustern die Ursache als Auslöser und Bedingungen."
    ),
}


def build_messages(mode: str, data: dict, vocabulary: str, max_candidates: int, *,
                   presets: list[str], flat_states: list[str], rooms: list[str]) -> list[dict]:
    if mode not in MODE_INTROS:
        raise ValueError(f"unbekannter Modus {mode!r}")
    system = SYSTEM_PROMPT.replace("{max_candidates}", str(max_candidates)).replace("{vocabulary}", vocabulary)
    user = "\n".join([
        MODE_INTROS[mode],
        f"Bekannte Räume: {', '.join(rooms) or '-'}",
        f"Bekannte Presets: {', '.join(presets) or '-'}",
        f"Bekannte Wohnungszustände: {', '.join(flat_states) or '-'}",
        "Daten (JSON):",
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    ])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
```

- [ ] **Step 5: LLM-Client implementieren**

`ai/analysis/llm.py`:

```python
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import aiohttp

from .config import AnalysisConfig


class LlmError(Exception):
    pass


class ZdrUnavailable(LlmError):
    pass


class InvalidResponse(LlmError):
    pass


class CostLimitExceeded(LlmError):
    pass


class _Retry(Exception):
    pass


@dataclass
class LlmResult:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float


def estimate_cost(messages: list[dict], cfg: AnalysisConfig) -> float:
    """Obere Schätzung: drei Zeichen je Eingabetoken, Ausgabe bis zum Limit."""
    characters = sum(len(message["content"]) for message in messages)
    return ((characters / 3) * cfg.price_input + cfg.max_output_tokens * cfg.price_output) / 1_000_000


def _check_cost(messages: list[dict], cfg: AnalysisConfig, spent: float) -> None:
    total = spent + estimate_cost(messages, cfg)
    if total > cfg.max_cost_usd:
        raise CostLimitExceeded(
            f"Geschätzte Kosten {total:.2f} USD übersteigen das Limit von {cfg.max_cost_usd:.2f} USD je Lauf")


def _result(body: str, cfg: AnalysisConfig) -> LlmResult:
    try:
        data = json.loads(body)
        text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"unerwartete Antwort von OpenRouter: {body[:200]}") from exc
    usage = data.get("usage") or {}
    tokens_in, tokens_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    if usage.get("cost") is not None:
        cost = float(usage["cost"])
    else:
        cost = (tokens_in * cfg.price_input + tokens_out * cfg.price_output) / 1_000_000
    return LlmResult(text or "", tokens_in, tokens_out, cost)


async def complete(cfg: AnalysisConfig, messages: list[dict], api_key: str, *,
                   sleep=asyncio.sleep) -> LlmResult:
    """Ein Aufruf mit Zero Data Retention. Netzfehler und 429/5xx werden wiederholt."""
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_output_tokens,
        "provider": cfg.provider,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=cfg.request_timeout_s)
    last: Exception | None = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(cfg.retries + 1):
            try:
                async with session.post(cfg.openrouter_url, json=payload, headers=headers) as response:
                    status, body = response.status, await response.text()
                if status == 404:
                    raise ZdrUnavailable(
                        f"Kein OpenRouter-Anbieter erfüllt Zero Data Retention für {cfg.model} "
                        f"(HTTP 404: {body[:200]})")
                if status in (429, 500, 502, 503, 504):
                    raise _Retry(f"HTTP {status}")
                if status >= 400:
                    raise LlmError(f"OpenRouter antwortet mit HTTP {status}: {body[:300]}")
                return _result(body, cfg)
            except (aiohttp.ClientError, asyncio.TimeoutError, _Retry) as exc:
                last = exc
                if attempt < cfg.retries:
                    await sleep(cfg.retry_backoff_s * 2 ** attempt)
    raise LlmError(f"Anfrage an OpenRouter fehlgeschlagen: {last}")


def parse_candidates_json(text: str, max_candidates: int) -> list[dict]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise InvalidResponse(f"kein gültiges JSON: {exc}") from exc
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("candidates"), list):
        items = data["candidates"]
    else:
        raise InvalidResponse('Schlüssel "candidates" (Liste) fehlt')
    if any(not isinstance(item, dict) for item in items):
        raise InvalidResponse("Die Einträge von candidates müssen Objekte sein")
    return items[:max_candidates]


async def request_candidates(cfg: AnalysisConfig, messages: list[dict], api_key: str, *,
                             sleep=asyncio.sleep) -> tuple[list[dict], LlmResult]:
    """Fragt Kandidaten an; bei ungültiger Antwort genau ein Wiederholungsversuch mit der Fehlermeldung."""
    total = LlmResult("", 0, 0, 0.0)
    current = list(messages)
    for attempt in range(2):
        _check_cost(current, cfg, total.cost_usd)
        result = await complete(cfg, current, api_key, sleep=sleep)
        total = LlmResult(result.text, total.tokens_in + result.tokens_in,
                          total.tokens_out + result.tokens_out, total.cost_usd + result.cost_usd)
        try:
            return parse_candidates_json(result.text, cfg.max_candidates), total
        except InvalidResponse as exc:
            if attempt == 1:
                raise
            current = [*messages,
                       {"role": "assistant", "content": result.text},
                       {"role": "user", "content": f"Die Antwort war ungültig ({exc}). "
                                                   "Antworte ausschließlich mit gültigem JSON im vorgegebenen Format."}]
    raise InvalidResponse("unerreichbar")   # pragma: no cover
```

- [ ] **Step 6: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_llm.py -v`
Expected: PASS (22 Tests).

- [ ] **Step 7: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle bisherigen Tests PASS.

```bash
git add ai/analysis/prompts.py ai/analysis/llm.py ai/tests/fake_openrouter.py ai/tests/conftest.py ai/tests/test_llm.py
git commit -m "feat(ai): Prompts und OpenRouter-Client mit ZDR, Kostenlimit und Wiederholung"
```

---

### Task 8: Lauf (Vorbereitung, Aufruf, Backtest, Speichern)

**Files:**
- Create: `ai/analysis/runner.py`
- Test: `ai/tests/test_runner.py`

**Interfaces:**
- Consumes: alle Module aus den Aufgaben 1 bis 7: `Store`, `build_pseudonyms`, `ContextProvider`, `load_interventions`, `build_weekly`, `build_clusters`, `data_period`, `known_presets`, `room_name`, `ParseContext`, `parse_candidates`, `vocabulary_text`, `Backtester`, `build_messages`, `request_candidates`, `Candidate`.
- Produces:
  - `AnalysisError(Exception)`, `TooFewInterventions(AnalysisError)` (Konstruktor `(found, needed)`), `NothingToAnalyze(AnalysisError)`, `PrivacyLeak(AnalysisError)`; `MODES = ("weekly", "clusters")`.
  - `@dataclass Environment(start, end, pseudo, ctx, interventions)`; `load_environment(db, cfg, start=None, end=None) -> Environment` (wirft `NothingToAnalyze`, wenn die Datenbank leer ist).
  - `@dataclass Prepared(mode, env, data, messages, parse_ctx)`; `prepare(db, cfg, mode, *, start=None, end=None) -> Prepared` (wirft `TooFewInterventions`, `NothingToAnalyze` bei leeren Clustern, `PrivacyLeak`, wenn der Text einen Klarnamen enthält).
  - `@dataclass RunSummary(run_id, mode, candidates, by_status: dict[str, int], cost_usd, tokens_in, tokens_out)`.
  - `async run_analysis(db, cfg, mode, *, api_key, start=None, end=None, now=utcnow, completer=request_candidates) -> RunSummary`.
  - `backtest_candidate(db, cfg, candidate_id, *, start=None, end=None) -> BacktestResult` (rechnet neu, schreibt Status, Notiz und Kennzahlen und committet).

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_runner.py`:

```python
import json
from dataclasses import replace

import pytest

from analysis.backtest import Backtester
from analysis.models import STATUS_PASSED, STATUS_UNTESTABLE
from analysis.llm import InvalidResponse
from analysis.runner import (NothingToAnalyze, TooFewInterventions, backtest_candidate, prepare,
                             run_analysis)
from analysis.store import Store
from tests.analysis_fixture import ROOM, build_habit_db

HABIT_TIME = {"room": "Wohnzimmer", "hypothesis": "Feierabend",
              "trigger": {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"},
              "action": {"type": "setpoint", "value": 21}}
HABIT_EVENT = {"room": "Wohnzimmer", "hypothesis": "person_2 kommt heim",
               "trigger": {"type": "event", "event": "person_arrives", "person": "person_2"},
               "action": {"type": "setpoint", "value": 21}}
NOT_ENOUGH = {"room": "Wohnzimmer", "hypothesis": "Morgens",
              "trigger": {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "09:00"},
              "action": {"type": "setpoint", "value": 21}}
UNKNOWN_TRIGGER = {"room": "Wohnzimmer", "trigger": {"type": "magic"}, "action": {"type": "setpoint", "value": 21}}
UNKNOWN_ROOM = {"room": "Keller", "trigger": HABIT_TIME["trigger"], "action": HABIT_TIME["action"]}


def answer(*items, **extra):
    return {"content": json.dumps({"candidates": list(items)}, ensure_ascii=False), **extra}


@pytest.fixture
def cfg(db, acfg, fake_llm):
    build_habit_db(db)
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(),
                   openrouter_url=fake_llm.url, retry_backoff_s=0.0)


async def run(db, cfg, mode="weekly", **kwargs):
    return await run_analysis(db, cfg, mode, api_key="geheim", **kwargs)


async def test_known_habit_is_found_across_dst_change(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME, HABIT_EVENT, NOT_ENOUGH, UNKNOWN_TRIGGER, UNKNOWN_ROOM)]
    summary = await run(db, cfg)
    assert summary.candidates == 5
    assert summary.by_status == {"bestanden": 2, "zu_wenig_belege": 1, "nicht_pruefbar": 2}
    store = Store(db)
    by_hypothesis = {c.hypothesis: c for c in store.list_candidates(run_id=summary.run_id)}
    time_rule = by_hypothesis["Feierabend"]
    assert time_rule.status == STATUS_PASSED and time_rule.room == ROOM
    metrics = store.results(time_rule.id)
    assert metrics["faelle"] == 30.0 and metrics["wochen"] == 6.0
    assert metrics["abdeckung"] == 1.0 and metrics["fehlausloesungen"] == 0.0
    event_rule = by_hypothesis["person_2 kommt heim"]
    assert event_rule.status == STATUS_PASSED and event_rule.trigger["person"] == "person.svenja"
    assert by_hypothesis["Morgens"].status == "zu_wenig_belege"
    untestable = [c for c in store.list_candidates() if c.status == STATUS_UNTESTABLE]
    assert len(untestable) == 2 and all(c.note for c in untestable)


async def test_both_modes_give_the_same_backtest_for_the_same_answer(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH), answer(HABIT_TIME, NOT_ENOUGH)]
    first = await run(db, cfg, "weekly")
    second = await run(db, cfg, "clusters")
    store = Store(db)
    pairs = [(c.status, store.results(c.id)) for c in store.list_candidates(run_id=first.run_id)]
    again = [(c.status, store.results(c.id)) for c in store.list_candidates(run_id=second.run_id)]
    assert pairs == again and len(pairs) == 2
    modes = [row["mode"] for row in store.list_runs()]
    assert modes == ["weekly", "clusters"]


async def test_prompt_contains_no_real_names(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME)]
    await run(db, cfg, "clusters")
    sent = "\n".join(m["content"] for m in fake_llm.requests[0]["payload"]["messages"]).lower()
    for forbidden in ("fabian", "svenja", "u-fabian", "u-svenja", "person.fabian", "person.svenja"):
        assert forbidden not in sent
    assert "person_2" in sent and "wohnzimmer" in sent
    stored = Store(db).get_run(1)["prompt_text"].lower()
    assert "svenja" not in stored and "fabian" not in stored


async def test_run_records_tokens_cost_prompt_and_pseudonyms(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME, tokens_in=1234, tokens_out=321, cost=0.5)]
    summary = await run(db, cfg)
    store = Store(db)
    row = store.get_run(summary.run_id)
    assert (row["status"], row["tokens_in"], row["tokens_out"], row["cost_usd"]) == ("abgeschlossen", 1234, 321, 0.5)
    assert row["model"] == cfg.model and len(row["prompt_hash"]) == 16
    assert "Wohnzimmer" in row["prompt_text"] and "Feierabend" in row["response_text"]
    assert json.loads(row["params"])["eingriffe"] == 32
    assert store.load_pseudonyms() == {"person_1": "person.fabian", "person_2": "person.svenja"}
    assert (summary.tokens_in, summary.cost_usd) == (1234, 0.5)


def test_prepare_makes_no_request_and_returns_messages(db, cfg, fake_llm):
    prepared = prepare(db, cfg, "weekly")
    assert fake_llm.requests == []
    assert [m["role"] for m in prepared.messages] == ["system", "user"]
    assert prepared.parse_ctx.rooms == {"Wohnzimmer": ROOM}
    assert prepared.parse_ctx.presets == frozenset({"eco", "comfort", "none"})
    assert len(prepared.env.interventions) == 32


async def test_too_few_interventions_stops_before_the_call(db, acfg, fake_llm):
    build_habit_db(db, weeks=1, with_noise=False)
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(), openrouter_url=fake_llm.url)
    with pytest.raises(TooFewInterventions, match="5"):
        await run(db, cfg)
    assert fake_llm.requests == [] and Store(db).list_runs() == []


def test_empty_database_and_empty_clusters(db, acfg):
    with pytest.raises(NothingToAnalyze):
        prepare(db, acfg, "weekly")
    build_habit_db(db)
    strict = replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(),
                     clusters=replace(acfg.clusters, min_size=999))
    with pytest.raises(NothingToAnalyze, match="Cluster"):
        prepare(db, strict, "clusters")


def test_unknown_mode_is_rejected(db, cfg):
    with pytest.raises(ValueError):
        prepare(db, cfg, "wild")


async def test_failed_run_leaves_no_candidates(db, cfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": "immer noch nicht"}]
    with pytest.raises(InvalidResponse):
        await run(db, cfg)
    store = Store(db)
    assert store.list_candidates() == []
    (row,) = store.list_runs()
    assert row["status"] == "fehlgeschlagen" and "JSON" in row["error"]
    assert row["prompt_text"]


async def test_run_is_atomic_when_the_backtest_crashes(db, cfg, fake_llm, monkeypatch):
    fake_llm.responses = [answer(HABIT_TIME, HABIT_EVENT)]
    original = Backtester.evaluate
    calls = {"count": 0}

    def flaky(self, candidate):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("kaputt")
        return original(self, candidate)

    monkeypatch.setattr(Backtester, "evaluate", flaky)
    with pytest.raises(RuntimeError):
        await run(db, cfg)
    store = Store(db)
    assert store.list_candidates() == []
    assert store.list_runs()[0]["status"] == "fehlgeschlagen"


async def test_backtest_candidate_recomputes_and_updates(db, cfg, fake_llm):
    fake_llm.responses = [answer(NOT_ENOUGH)]
    summary = await run(db, cfg)
    store = Store(db)
    (candidate,) = store.list_candidates(run_id=summary.run_id)
    store.set_candidate_status(candidate.id, STATUS_UNTESTABLE, "manuell verstellt")
    store.save_results(candidate.id, {})
    store.commit()
    result = backtest_candidate(db, cfg, candidate.id)
    assert result.status == "zu_wenig_belege"
    assert store.get_candidate(candidate.id).status == "zu_wenig_belege"
    assert store.results(candidate.id)["ausloesungen"] == 30.0
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_runner.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.runner'`.

- [ ] **Step 3: Implementieren**

`ai/analysis/runner.py`:

```python
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from collector.db import Database
from collector.models import to_utc_iso, utcnow

from .backtest import Backtester, BacktestResult
from .config import AnalysisConfig
from .context import ContextProvider
from .features import build_clusters, build_weekly, data_period, known_presets, room_name
from .interventions import load_interventions
from .llm import request_candidates
from .models import STATUS_OPEN, Intervention
from .prompts import build_messages
from .pseudonym import Pseudonyms, build_pseudonyms
from .store import Store
from .vocabulary import ParseContext, parse_candidates, vocabulary_text

MODES = ("weekly", "clusters")


class AnalysisError(Exception):
    pass


class TooFewInterventions(AnalysisError):
    def __init__(self, found: int, needed: int):
        super().__init__(f"Nur {found} manuelle Eingriffe im Zeitraum, mindestens {needed} nötig.")


class NothingToAnalyze(AnalysisError):
    pass


class PrivacyLeak(AnalysisError):
    pass


@dataclass
class Environment:
    start: datetime
    end: datetime
    pseudo: Pseudonyms
    ctx: ContextProvider
    interventions: list[Intervention]


@dataclass
class Prepared:
    mode: str
    env: Environment
    data: dict
    messages: list[dict]
    parse_ctx: ParseContext


@dataclass
class RunSummary:
    run_id: int
    mode: str
    candidates: int
    by_status: dict[str, int]
    cost_usd: float
    tokens_in: int
    tokens_out: int


def load_environment(db: Database, cfg: AnalysisConfig, start: datetime | None = None,
                     end: datetime | None = None) -> Environment:
    period = data_period(db)
    if period is None:
        raise NothingToAnalyze("Die Datenbank enthält keine Zustandsänderungen.")
    start, end = start or period[0], end or period[1]
    pseudo = build_pseudonyms(db)
    return Environment(start, end, pseudo, ContextProvider.from_db(db, cfg, pseudo),
                       load_interventions(db, cfg, start=start, end=end))


def prepare(db: Database, cfg: AnalysisConfig, mode: str, *, start: datetime | None = None,
            end: datetime | None = None) -> Prepared:
    if mode not in MODES:
        raise ValueError(f"unbekannter Modus {mode!r} (erlaubt: {', '.join(MODES)})")
    env = load_environment(db, cfg, start, end)
    if len(env.interventions) < cfg.min_interventions:
        raise TooFewInterventions(len(env.interventions), cfg.min_interventions)
    if mode == "weekly":
        data = build_weekly(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
    else:
        data = build_clusters(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
        if not data["cluster"]:
            raise NothingToAnalyze(
                f"Keine Cluster mit mindestens {cfg.clusters.min_size} Eingriffen gefunden.")
    rooms = {room_name(cfg, area): area for area in {item.room for item in env.interventions}}
    presets = known_presets(db) | {str(item.value) for item in env.interventions if item.kind == "preset"}
    flat_states = env.ctx.flat_state.states()
    parse_ctx = ParseContext(rooms=rooms, pseudo=env.pseudo, presets=frozenset(presets),
                             flat_states=frozenset(flat_states))
    messages = build_messages(mode, data, vocabulary_text(), cfg.max_candidates, presets=sorted(presets),
                              flat_states=sorted(flat_states), rooms=sorted(rooms))
    leaks = env.pseudo.leaks("\n".join(message["content"] for message in messages))
    if leaks:
        raise PrivacyLeak(f"Der zu sendende Text enthält schützenswerte Begriffe: {', '.join(leaks)}")
    return Prepared(mode, env, data, messages, parse_ctx)


async def run_analysis(db: Database, cfg: AnalysisConfig, mode: str, *, api_key: str,
                       start: datetime | None = None, end: datetime | None = None,
                       now=utcnow, completer=request_candidates) -> RunSummary:
    prepared = prepare(db, cfg, mode, start=start, end=end)
    env = prepared.env
    store = Store(db)
    store.save_pseudonyms(env.pseudo.to_rows())
    store.commit()
    prompt_text = "\n\n".join(f"[{message['role']}]\n{message['content']}" for message in prepared.messages)
    prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
    params = {"von": to_utc_iso(env.start), "bis": to_utc_iso(env.end),
              "eingriffe": len(env.interventions), "prompt_zeichen": len(prompt_text)}
    run_id = store.start_run(mode, cfg.model, params, now())
    try:
        raws, result = await completer(cfg, prepared.messages, api_key)
        candidates = parse_candidates(raws, prepared.parse_ctx)
        tester = Backtester(cfg, env.interventions, env.ctx, env.start, env.end)
        counts: Counter[str] = Counter()
        for candidate in candidates:
            outcome = tester.evaluate(candidate) if candidate.status == STATUS_OPEN else None
            if outcome is not None:
                candidate.status, candidate.note = outcome.status, outcome.note
            candidate_id = store.add_candidate(run_id, candidate)
            if outcome is not None and outcome.metrics:
                store.save_results(candidate_id, outcome.metrics)
            counts[candidate.status] += 1
        store.finish_run(run_id, "abgeschlossen", now(), prompt_hash=prompt_hash, prompt_text=prompt_text,
                         response_text=result.text, tokens_in=result.tokens_in,
                         tokens_out=result.tokens_out, cost_usd=result.cost_usd)
    except Exception as exc:
        store.rollback()
        store.finish_run(run_id, "fehlgeschlagen", now(), error=str(exc)[:500], prompt_hash=prompt_hash,
                         prompt_text=prompt_text)
        raise
    return RunSummary(run_id, mode, len(candidates), dict(counts), result.cost_usd,
                      result.tokens_in, result.tokens_out)


def backtest_candidate(db: Database, cfg: AnalysisConfig, candidate_id: int, *,
                       start: datetime | None = None, end: datetime | None = None) -> BacktestResult:
    store = Store(db)
    candidate = store.get_candidate(candidate_id)
    env = load_environment(db, cfg, start, end)
    outcome = Backtester(cfg, env.interventions, env.ctx, env.start, env.end).evaluate(candidate)
    store.set_candidate_status(candidate_id, outcome.status, outcome.note)
    store.save_results(candidate_id, outcome.metrics)
    store.commit()
    return outcome
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_runner.py -v`
Expected: PASS (11 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle bisherigen Tests PASS.

```bash
git add ai/analysis/runner.py ai/tests/test_runner.py
git commit -m "feat(ai): Analyse-Lauf mit Vorbereitung, Backtest und atomarer Speicherung"
```

---

### Task 9: Kandidaten anzeigen und vergleichen, Kommandozeile

**Files:**
- Create: `ai/analysis/candidates.py`, `ai/analysis/cli.py`, `ai/analysis/__main__.py`
- Test: `ai/tests/test_analysis_cli.py`

**Interfaces:**
- Consumes: `runner` (`prepare`, `run_analysis`, `backtest_candidate`, `MODES`, `AnalysisError`), `Store`, `build_pseudonyms`, `load_analysis_config`, `AnalysisConfigError`, `LlmError`, `estimate_cost`, `room_name`, `Candidate`.
- Produces:
  - `analysis.candidates`: `weekday_label(days: list[int]) -> str`; `describe(candidate, cfg, pseudo) -> tuple[str, str, str]` (Raum, Auslöser samt Bedingungen, Aktion; lesbarer deutscher Text mit Klarnamen, nur für die lokale Anzeige; bei nicht darstellbaren Kandidaten das JSON); `signature(candidate) -> tuple | None` (Vergleichsschlüssel: Raum, Auslöser — Zeit auf 30-Minuten-Raster, Ereignis mit Person oder Wert —, Aktionsart und Wert, Sollwert auf 0,5 gerundet; `None` bei `nicht_pruefbar`).
  - `analysis.cli.main(argv=None) -> int` mit globalen Optionen `--config` (Standard `ANALYSIS_CONFIG` oder `/app/config/analysis.yaml`) und `--db` (Standard `DB_PATH` oder `/data/home_ai.sqlite`) und den Unterbefehlen `run --mode weekly|clusters [--from TS] [--to TS] [--dry-run]`, `compare [RUN_ID ...]`, `list [--run RUN_ID] [--status STATUS]`, `backtest CANDIDATE_ID [--from TS] [--to TS]`. Rückgabe 0 = Erfolg, 1 = nichts zu vergleichen, 2 = Konfigurations-, Verbindungs- oder Datenfehler (Meldung auf stderr als `Fehler: …`).
- Ausgabeformate (die Tests prüfen sie):
  - `run`: erste Zeile `Lauf {id} ({mode}) abgeschlossen: {n} Kandidaten`, danach je eine Zeile `  bestanden: x`, `  zu_wenig_belege: x`, `  abgelehnt: x`, `  nicht_pruefbar: x`, dann `Kosten: {cost:.4f} USD ({tokens_in} Token ein, {tokens_out} aus)`.
  - `run --dry-run`: je Nachricht eine Zeile `--- {rolle} ---` und der Text, am Ende `--- {n} Eingriffe, geschätzte Kosten höchstens {x:.2f} USD, kein Aufruf ---`.
  - `list`: je Kandidat `{id:>4}  {Raum}  {Auslöser}  ->  {Aktion}  [{status}]` plus `  abdeckung=… fehlausloesungen=… faelle=… wochen=…`, wenn Kennzahlen vorliegen.
  - `compare`: Tabellenkopf `Lauf  Modus     Modell … Kandidaten  bestanden  zu_wenig_belege  abgelehnt  nicht_pruefbar  Kosten`, eine Zeile je Lauf; bei genau zwei Läufen zusätzlich `Überlappung Lauf {a} / Lauf {b}: gemeinsam {n}, nur Lauf {a}: {x}, nur Lauf {b}: {y}`. Ohne Lauf-IDs werden die jeweils letzten abgeschlossenen Läufe je Modus verglichen.
  - `backtest`: `Kandidat {id}: {status}` und danach die Kennzahlen als `  {name}: {wert}`.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_analysis_cli.py`:

```python
import asyncio
import json

import pytest
import yaml

from analysis.candidates import describe, signature, weekday_label
from analysis.cli import main
from analysis.models import STATUS_UNTESTABLE, Candidate
from analysis.pseudonym import Pseudonyms
from analysis.store import Store
from collector.db import Database
from tests.analysis_fixture import ROOM, build_habit_db
from tests.conftest import ROOT
from tests.test_runner import HABIT_EVENT, HABIT_TIME, NOT_ENOUGH, UNKNOWN_ROOM, answer


@pytest.fixture
def files(tmp_path, fake_llm):
    database = Database(tmp_path / "home_ai.sqlite")
    build_habit_db(database)
    database.close()
    raw = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))
    raw.update(openrouter_url=fake_llm.url, retry_backoff_s=0.0, exclusions=[], room_names={ROOM: "Wohnzimmer"})
    config = tmp_path / "analysis.yaml"
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return str(config), str(tmp_path / "home_ai.sqlite")


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "geheim")


async def cli(files, *args) -> int:
    config, database = files
    return await asyncio.to_thread(main, ["--config", config, "--db", database, *args])


def stored(files) -> Store:
    return Store(Database(files[1]))


async def test_run_prints_summary_and_stores_candidates(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, UNKNOWN_ROOM, tokens_in=10, tokens_out=5, cost=0.0123)]
    assert await cli(files, "run", "--mode", "weekly") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Lauf 1 (weekly) abgeschlossen: 2 Kandidaten"
    assert "  bestanden: 1" in out and "  nicht_pruefbar: 1" in out and "  abgelehnt: 0" in out
    assert "Kosten: 0.0123 USD (10 Token ein, 5 aus)" in out
    assert len(stored(files).list_candidates()) == 2


async def test_dry_run_prints_the_prompt_and_calls_nothing(files, fake_llm, capsys):
    assert await cli(files, "run", "--mode", "clusters", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "--- system ---" in out and "--- user ---" in out and "Wohnzimmer" in out
    assert "32 Eingriffe" in out and "kein Aufruf" in out
    assert fake_llm.requests == [] and stored(files).list_runs() == []
    for forbidden in ("fabian", "svenja"):
        assert forbidden not in out.lower()


async def test_run_without_api_key_exits_with_2(files, fake_llm, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert await cli(files, "run", "--mode", "weekly") == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert fake_llm.requests == []


async def test_compare_shows_both_modes_and_their_overlap(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH), answer(HABIT_TIME, HABIT_EVENT)]
    assert await cli(files, "run", "--mode", "weekly") == 0
    assert await cli(files, "run", "--mode", "clusters") == 0
    capsys.readouterr()
    assert await cli(files, "compare") == 0
    out = capsys.readouterr().out
    assert "weekly" in out and "clusters" in out and "bestanden" in out
    assert "Überlappung Lauf 1 / Lauf 2: gemeinsam 1, nur Lauf 1: 1, nur Lauf 2: 1" in out


async def test_compare_without_runs_exits_with_1(files, capsys):
    assert await cli(files, "compare") == 1
    assert "Keine" in capsys.readouterr().out


async def test_list_filters_by_status(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH, UNKNOWN_ROOM)]
    await cli(files, "run", "--mode", "weekly")
    capsys.readouterr()
    assert await cli(files, "list", "--status", "bestanden") == 0
    out = capsys.readouterr().out
    assert "Wohnzimmer" in out and "Mo–Fr 17:30" in out and "21 °C" in out and "[bestanden]" in out
    assert "abdeckung=1.00" in out and "faelle=30" in out
    assert "zu_wenig_belege" not in out and "nicht_pruefbar" not in out
    assert await cli(files, "list", "--run", "1") == 0
    assert capsys.readouterr().out.count("\n") == 3


async def test_backtest_command_recomputes_one_candidate(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH)]
    await cli(files, "run", "--mode", "weekly")
    capsys.readouterr()
    assert await cli(files, "backtest", "2") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Kandidat 2: zu_wenig_belege"
    assert "  ausloesungen: 30.0" in out


async def test_unknown_candidate_exits_with_2(files, capsys):
    assert await cli(files, "backtest", "99") == 2
    assert "99" in capsys.readouterr().err


async def test_too_few_interventions_exits_with_2(tmp_path, fake_llm, capsys):
    database = Database(tmp_path / "wenig.sqlite")
    build_habit_db(database, weeks=1, with_noise=False)
    database.close()
    config = tmp_path / "a.yaml"
    raw = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))
    raw.update(openrouter_url=fake_llm.url, exclusions=[], room_names={ROOM: "Wohnzimmer"})
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert await asyncio.to_thread(main, ["--config", str(config), "--db", str(tmp_path / "wenig.sqlite"),
                                          "run", "--mode", "weekly"]) == 2
    assert "mindestens" in capsys.readouterr().err
    assert fake_llm.requests == []


def test_missing_config_and_disabled_zdr_exit_with_2(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "gibt-es-nicht.yaml"), "--db", str(tmp_path / "x.sqlite"),
                 "list"]) == 2
    assert "gibt-es-nicht.yaml" in capsys.readouterr().err
    raw = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))
    raw["provider"]["zdr"] = False
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert main(["--config", str(bad), "--db", str(tmp_path / "x.sqlite"), "list"]) == 2
    assert "zdr" in capsys.readouterr().err


# --- Darstellung und Vergleichsschlüssel -------------------------------------------------

PSEUDO = Pseudonyms(("person.fabian", "person.svenja"), {"person.svenja": "Svenja"}, {})


def candidate(trigger, action=None, conditions=None, status="offen"):
    return Candidate(room=ROOM, trigger=trigger, action=action or {"type": "setpoint", "value": 21.0},
                     conditions=conditions or [], status=status)


def test_weekday_labels():
    assert weekday_label([0, 1, 2, 3, 4]) == "Mo–Fr"
    assert weekday_label([6, 5]) == "Sa–So"
    assert weekday_label(list(range(7))) == "täglich"
    assert weekday_label([0, 2]) == "Mo,Mi"


def test_describe_uses_names_conditions_and_actions(acfg):
    import dataclasses
    cfg = dataclasses.replace(acfg, room_names={ROOM: "Wohnzimmer"})
    trigger = {"type": "event", "event": "person_arrives", "person": "person.svenja"}
    conditions = [{"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]},
                  {"type": "outside_temp", "op": "below", "value": 8.0},
                  {"type": "person_home", "person": "person.fabian", "home": False}]
    room, when, action = describe(candidate(trigger, conditions=conditions), cfg, PSEUDO)
    assert room == "Wohnzimmer"
    assert when == ("Svenja kommt heim wenn Mo–Fr; Außentemperatur unter 8 °C; Fabian ist nicht zu Hause")
    assert action == "21 °C"
    assert describe(candidate({"type": "time", "weekdays": [0], "time": "07:00"},
                              action={"type": "preset", "value": "comfort"}), cfg, PSEUDO)[1:] == (
        "Mo 07:00", "Preset comfort")
    broken = describe(candidate({"type": "kaputt"}), cfg, PSEUDO)
    assert broken[1] == json.dumps({"type": "kaputt"})


def test_signature_ignores_conditions_and_rounds_time_and_setpoint():
    a = candidate({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"})
    b = candidate({"type": "time", "weekdays": [4, 3, 2, 1, 0], "time": "17:40"},
                  action={"type": "setpoint", "value": 21.2},
                  conditions=[{"type": "outside_temp", "op": "below", "value": 8.0}])
    assert signature(a) == signature(b)
    c = candidate({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "18:10"})
    assert signature(a) != signature(c)
    d = candidate({"type": "event", "event": "person_arrives", "person": "person.svenja"})
    assert signature(d) != signature(a)
    assert signature(candidate({}, status=STATUS_UNTESTABLE)) is None
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_analysis_cli.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'analysis.candidates'`.

- [ ] **Step 3: Darstellung und Vergleichsschlüssel implementieren**

`ai/analysis/candidates.py`:

```python
from __future__ import annotations

import json

from .config import AnalysisConfig
from .features import room_name
from .models import STATUS_UNTESTABLE, Candidate
from .pseudonym import Pseudonyms

SHORT_DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def weekday_label(days: list[int]) -> str:
    days = sorted(days)
    if days == list(range(7)):
        return "täglich"
    if days == [0, 1, 2, 3, 4]:
        return "Mo–Fr"
    if days == [5, 6]:
        return "Sa–So"
    return ",".join(SHORT_DAYS[day] for day in days)


def _trigger_text(trigger: dict, pseudo: Pseudonyms) -> str:
    if trigger["type"] == "time":
        return f"{weekday_label(trigger['weekdays'])} {trigger['time']}"
    event = trigger["event"]
    if event == "person_arrives":
        return f"{pseudo.display_name(trigger['person'])} kommt heim"
    if event == "person_leaves":
        return f"{pseudo.display_name(trigger['person'])} geht"
    if event == "flat_state":
        return f"Wohnungszustand wird {trigger['value']}"
    return {"bed_in": "jemand legt sich ins Bett", "bed_out": "jemand steht auf",
            "window_closed": "Fenster geschlossen", "office_day_start": "Büro-Tag beginnt"}[event]


def _condition_text(condition: dict, pseudo: Pseudonyms) -> str:
    kind = condition["type"]
    if kind == "weekdays":
        return weekday_label(condition["weekdays"])
    if kind == "time_window":
        return f"{condition['from']}–{condition['to']} Uhr"
    if kind == "outside_temp":
        word = "unter" if condition["op"] == "below" else "über"
        return f"Außentemperatur {word} {condition['value']:g} °C"
    state = "ist zu Hause" if condition["home"] else "ist nicht zu Hause"
    return f"{pseudo.display_name(condition['person'])} {state}"


def _action_text(action: dict) -> str:
    if action["type"] == "preset":
        return f"Preset {action['value']}"
    return f"{action['value']:g} °C"


def describe(candidate: Candidate, cfg: AnalysisConfig, pseudo: Pseudonyms) -> tuple[str, str, str]:
    """Lesbarer Text für die lokale Anzeige (mit Klarnamen, geht nie an das Modell)."""
    room = room_name(cfg, candidate.room)
    try:
        when = _trigger_text(candidate.trigger, pseudo)
        if candidate.conditions:
            when += " wenn " + "; ".join(_condition_text(item, pseudo) for item in candidate.conditions)
        return room, when, _action_text(candidate.action)
    except (KeyError, TypeError, ValueError):
        return (room, json.dumps(candidate.trigger, ensure_ascii=False),
                json.dumps(candidate.action, ensure_ascii=False))


def signature(candidate: Candidate) -> tuple | None:
    """Vergleichsschlüssel für `compare`: gleicher Raum, gleiche Aktion, im Kern gleicher Auslöser."""
    if candidate.status == STATUS_UNTESTABLE:
        return None
    try:
        trigger = candidate.trigger
        if trigger["type"] == "time":
            hour, minute = (int(part) for part in trigger["time"].split(":"))
            key: tuple = ("time", tuple(sorted(trigger["weekdays"])), (hour * 60 + minute) // 30)
        else:
            key = ("event", trigger["event"], trigger.get("person") or trigger.get("value") or "")
        action = candidate.action
        value = action["value"] if action["type"] == "preset" else round(float(action["value"]) * 2) / 2
        return (candidate.room, key, action["type"], value)
    except (KeyError, TypeError, ValueError):
        return None
```

- [ ] **Step 4: Kommandozeile implementieren**

`ai/analysis/cli.py`:

```python
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from collector.db import Database
from collector.models import parse_utc

from .candidates import describe, signature
from .config import AnalysisConfigError, load_analysis_config
from .llm import LlmError, estimate_cost
from .pseudonym import build_pseudonyms
from .runner import MODES, AnalysisError, backtest_candidate, prepare, run_analysis
from .store import Store

STATUSES = ("bestanden", "zu_wenig_belege", "abgelehnt", "nicht_pruefbar")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="analysis", description="KI-Analyse: Regelkandidaten für die Heizung")
    parser.add_argument("--config", default=os.environ.get("ANALYSIS_CONFIG", "/app/config/analysis.yaml"))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "/data/home_ai.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="einen Analyse-Lauf starten")
    run.add_argument("--mode", required=True, choices=MODES)
    run.add_argument("--from", dest="since")
    run.add_argument("--to", dest="until")
    run.add_argument("--dry-run", action="store_true", help="Text anzeigen, nichts senden")
    compare = sub.add_parser("compare", help="Läufe gegenüberstellen")
    compare.add_argument("run_ids", nargs="*", type=int)
    listing = sub.add_parser("list", help="Kandidaten anzeigen")
    listing.add_argument("--run", dest="run_id", type=int)
    listing.add_argument("--status", choices=STATUSES)
    backtest = sub.add_parser("backtest", help="Backtest eines Kandidaten erneut rechnen")
    backtest.add_argument("candidate_id", type=int)
    backtest.add_argument("--from", dest="since")
    backtest.add_argument("--to", dest="until")
    return parser


def _period(args) -> tuple:
    return (parse_utc(args.since) if args.since else None, parse_utc(args.until) if args.until else None)


def _cmd_run(cfg, db: Database, args) -> int:
    start, end = _period(args)
    if args.dry_run:
        prepared = prepare(db, cfg, args.mode, start=start, end=end)
        for message in prepared.messages:
            print(f"--- {message['role']} ---")
            print(message["content"])
        print(f"--- {len(prepared.env.interventions)} Eingriffe, geschätzte Kosten höchstens "
              f"{estimate_cost(prepared.messages, cfg):.2f} USD, kein Aufruf ---")
        return 0
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise AnalysisError("Umgebungsvariable OPENROUTER_API_KEY fehlt")
    summary = asyncio.run(run_analysis(db, cfg, args.mode, api_key=api_key, start=start, end=end))
    print(f"Lauf {summary.run_id} ({summary.mode}) abgeschlossen: {summary.candidates} Kandidaten")
    for status in STATUSES:
        print(f"  {status}: {summary.by_status.get(status, 0)}")
    print(f"Kosten: {summary.cost_usd:.4f} USD ({summary.tokens_in} Token ein, {summary.tokens_out} aus)")
    return 0


def _latest_per_mode(store: Store) -> list:
    latest: dict[str, object] = {}
    for row in store.list_runs():
        if row["status"] == "abgeschlossen":
            latest[row["mode"]] = row
    return [latest[mode] for mode in MODES if mode in latest]


def _cmd_compare(store: Store, args) -> int:
    if args.run_ids:
        rows = []
        for run_id in args.run_ids:
            row = store.get_run(run_id)
            if row is None:
                raise AnalysisError(f"Lauf {run_id} existiert nicht")
            rows.append(row)
    else:
        rows = _latest_per_mode(store)
    if not rows:
        print("Keine abgeschlossenen Läufe zum Vergleichen.")
        return 1
    print(f"{'Lauf':>4}  {'Modus':<9} {'Modell':<28} {'Kandidaten':>10} {'bestanden':>9} "
          f"{'zu_wenig_belege':>15} {'abgelehnt':>9} {'nicht_pruefbar':>14} {'Kosten':>8}")
    signatures: list[set] = []
    for row in rows:
        candidates = store.list_candidates(run_id=row["id"])
        counts = {status: sum(1 for item in candidates if item.status == status) for status in STATUSES}
        cost = row["cost_usd"] or 0.0
        print(f"{row['id']:>4}  {row['mode']:<9} {row['model']:<28} {len(candidates):>10} "
              f"{counts['bestanden']:>9} {counts['zu_wenig_belege']:>15} {counts['abgelehnt']:>9} "
              f"{counts['nicht_pruefbar']:>14} {cost:>8.4f}")
        signatures.append({key for key in map(signature, candidates) if key is not None})
    if len(rows) == 2:
        first, second = rows[0]["id"], rows[1]["id"]
        both = signatures[0] & signatures[1]
        print(f"Überlappung Lauf {first} / Lauf {second}: gemeinsam {len(both)}, "
              f"nur Lauf {first}: {len(signatures[0] - both)}, nur Lauf {second}: {len(signatures[1] - both)}")
    return 0


def _cmd_list(cfg, db: Database, store: Store, args) -> int:
    pseudo = build_pseudonyms(db)
    for item in store.list_candidates(run_id=args.run_id, status=args.status):
        room, when, action = describe(item, cfg, pseudo)
        line = f"{item.id:>4}  {room}  {when}  ->  {action}  [{item.status}]"
        metrics = store.results(item.id)
        if metrics:
            line += (f"  abdeckung={metrics['abdeckung']:.2f} fehlausloesungen={metrics['fehlausloesungen']:.2f} "
                     f"faelle={metrics['faelle']:.0f} wochen={metrics['wochen']:.0f}")
        print(line)
    return 0


def _cmd_backtest(cfg, db: Database, args) -> int:
    start, end = _period(args)
    outcome = backtest_candidate(db, cfg, args.candidate_id, start=start, end=end)
    print(f"Kandidat {args.candidate_id}: {outcome.status}")
    if outcome.note:
        print(f"  {outcome.note}")
    for name, value in outcome.metrics.items():
        print(f"  {name}: {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        cfg = load_analysis_config(args.config)
        db = Database(args.db)
        try:
            store = Store(db)
            if args.command == "run":
                return _cmd_run(cfg, db, args)
            if args.command == "compare":
                return _cmd_compare(store, args)
            if args.command == "list":
                return _cmd_list(cfg, db, store, args)
            return _cmd_backtest(cfg, db, args)
        finally:
            db.close()
    except (AnalysisConfigError, AnalysisError, LlmError, FileNotFoundError, KeyError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

`ai/analysis/__main__.py`:

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_analysis_cli.py -v`
Expected: PASS (13 Tests). Falls `test_list_filters_by_status` bei der Zeilenzahl abweicht (`count("\n") == 3`), prüfen, ob `list` für jeden der drei Kandidaten genau eine Zeile ausgibt.

- [ ] **Step 6: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS.

```bash
git add ai/analysis/candidates.py ai/analysis/cli.py ai/analysis/__main__.py ai/tests/test_analysis_cli.py
git commit -m "feat(ai): Kommandozeile der Analyse (run, compare, list, backtest)"
```

---

### Task 10: Container und Dokumentation

**Files:**
- Modify: `ai/Dockerfile` (Zeile `COPY analysis ./analysis`)
- Modify: `ai/README.md` (Abschnitt „Analyse (Teilprojekt 2)“)
- Modify: `docs/superpowers/specs/2026-10-13-home-ai-analysis-design.md` (Status und Abweichungen)

**Interfaces:**
- Consumes: alle Kommandos aus Aufgabe 9.
- Produces: dasselbe Image `home-ai-collector` kann `python -m analysis …` ausführen; Dokumentation der Bedienung.

- [ ] **Step 1: Dockerfile ergänzen**

In `ai/Dockerfile` nach `COPY collector ./collector` einfügen:

```dockerfile
COPY analysis ./analysis
```

Ergebnis:

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector ./collector
COPY analysis ./analysis
COPY sql ./sql
COPY config ./config
CMD ["python", "-m", "collector", "run"]
```

- [ ] **Step 2: README ergänzen**

An `ai/README.md` anhängen:

````markdown
## Analyse (Teilprojekt 2)

Erzeugt aus den manuellen Eingriffen Regelkandidaten (Zeit- oder Ereignisauslöser), lässt ein LLM über OpenRouter (mit Zero Data Retention) Hypothesen formulieren und prüft jeden Kandidaten gegen die Historie. Nichts im Haus ändert sich dadurch; die Kandidaten sind Vorschläge. Spezifikation: `docs/superpowers/specs/2026-10-13-home-ai-analysis-design.md`.

Zwei umschaltbare Verfahren: `weekly` (das Modell sucht Muster im Wochenraster) und `clusters` (ein deterministischer Schritt bündelt Eingriffe samt Kontext, das Modell formuliert die Ursache). Backtest und Speicherung sind identisch, so lassen sich beide vergleichen.

Alle Befehle laufen im Image des Collectors und lesen die Datenbank unter `/data`:

```bash
# Vorab: was würde gesendet? (kein Aufruf, kostenlos)
docker run --rm -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector \
  python -m analysis run --mode weekly --dry-run

# Lauf (OPENROUTER_API_KEY vorher in der Shell setzen: read -s OPENROUTER_API_KEY; export OPENROUTER_API_KEY)
docker run --rm -e OPENROUTER_API_KEY -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector \
  python -m analysis run --mode weekly

# Vergleichen und ansehen
docker run --rm -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector python -m analysis compare
docker run --rm -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector python -m analysis list --status bestanden
```

Die Regeln liegen in `config/analysis.yaml` (Modell, Kostenlimit, Schwellen des Backtests, Ausschlüsse, Raumnamen) und sind ins Image eingebaut; nach einer Änderung das Image neu bauen. Der Schlüssel steht nie in einer Datei.

Hinweise:

- Das Modell sieht nur verdichtete Muster. Personen sind `person_1`, `person_2`; Räume stehen im Klartext. `--dry-run` zeigt den vollständigen Text.
- Ein Lauf bricht ohne Aufruf ab, wenn weniger als `min_interventions` Eingriffe im Zeitraum liegen oder das geschätzte Kostenlimit überschritten würde.
- Kandidaten mit Status `nicht_pruefbar` nutzen etwas, das das System nicht nachrechnen kann (unbekannter Raum, fehlender Sensor); die Notiz nennt den Grund.
- Das Ende des Ausschlusses für das Schlafzimmer in `analysis.yaml` ist das Datum des Thermostattauschs und beim Einrichten anzupassen.
````

- [ ] **Step 3: Spezifikation um die Abweichungen ergänzen**

In `docs/superpowers/specs/2026-10-13-home-ai-analysis-design.md` den Status auf „Entwurf freigegeben“ setzen und am Ende von Abschnitt 8 anfügen:

```markdown
**Umsetzung, Abweichungen:** Die Spalten heißen `trigger_json`, `conditions_json` und `action_json` (`TRIGGER` ist ein SQL-Schlüsselwort). Zusätzlich gibt es `candidates.note` (Grund für `nicht_pruefbar`, `zu_wenig_belege`, `abgelehnt`) und `analysis_runs.error`. Räume stehen in der Datenbank als `area_id`; die Klartextnamen für das Modell kommen aus `room_names` in `analysis.yaml`.
```

- [ ] **Step 4: Image bauen und Gesamtlauf**

Run: `cd ai && docker build -t home-ai-collector .`
Expected: Bau ohne Fehler. Falls auf diesem Rechner kein Docker läuft, den Bau auf dem Docker-Host ausführen und hier abhaken.

Run: `cd ai && docker run --rm home-ai-collector python -m analysis --help`
Expected: Hilfetext mit den Unterbefehlen `run`, `compare`, `list`, `backtest`.

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/Dockerfile ai/README.md docs/superpowers/specs/2026-10-13-home-ai-analysis-design.md
git commit -m "feat(ai): Analyse im Container, README und Abweichungen in der Spezifikation"
```

---

### Task 11: Abnahme mit den echten Daten

Diese Aufgabe nutzt deinen OpenRouter-Schlüssel und deine echte Datenbank und läuft auf dem Docker-Host. Schritte mit **[du]** musst du selbst ausführen. Entscheidet ein Ergebnis über den weiteren Verlauf, steht das als **Halt** dabei.

**Files:**
- Modify: `ai/config/analysis.yaml` (Modell-ID prüfen, Ende des Schlafzimmer-Ausschlusses)

**Interfaces:**
- Consumes: den gesamten Code aus den Aufgaben 1 bis 10 und die Erfolgskriterien 1 bis 5 der Spezifikation (Kriterium 6 deckt `test_known_habit_is_found_across_dst_change` ab).
- Produces: je ein abgeschlossener Lauf in beiden Modi und ein `compare`.

- [ ] **Step 1: [du] Code holen und Image bauen**

```bash
cd /opt/heating_control && git pull && docker build -t home-ai-collector ai
```

Expected: Bau ohne Fehler.

- [ ] **Step 2: [du] Modell-ID und Ausschlusszeitraum prüfen**

1. In `ai/config/analysis.yaml` `model` gegen die Modellliste auf openrouter.ai prüfen und, wenn nötig, auf eine gültige ID setzen. Die Preise (`price_per_mtok`) für das gewählte Modell eintragen; sie steuern nur die Kostenschätzung.
2. Das Ende des Ausschlusses für das Schlafzimmer (`to`) auf das Datum des Thermostattauschs setzen.
3. Prüfen, dass `provider.zdr: true` bleibt. Die Konfiguration lässt das Abschalten gar nicht zu.
4. Image neu bauen (`docker build -t home-ai-collector ai`).

- [ ] **Step 3: Trockenlauf und Datenschutzprüfung**

Run:

```bash
docker run --rm -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector \
  python -m analysis run --mode clusters --dry-run
```

Expected: Der Text zeigt `person_1`/`person_2` und Räume im Klartext, am Ende `… Eingriffe, geschätzte Kosten höchstens … USD, kein Aufruf`. **Halt:** Steht irgendwo ein Name einer Person, eine Entity-ID oder eine Benutzer-ID, nichts senden und den Fund festhalten (der Lauf bricht in dem Fall ohnehin mit `PrivacyLeak` ab). Steht dort `Cluster … gefunden`, `Nur n manuelle Eingriffe` oder eine andere Meldung, den Text lesen und klären, bevor es weitergeht.

- [ ] **Step 4: [du] Beide Läufe starten**

```bash
read -s OPENROUTER_API_KEY; export OPENROUTER_API_KEY
docker run --rm -e OPENROUTER_API_KEY -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector \
  python -m analysis run --mode weekly
docker run --rm -e OPENROUTER_API_KEY -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector \
  python -m analysis run --mode clusters
```

Expected: je Lauf `Lauf n (…) abgeschlossen: k Kandidaten` und die Kosten. **Halt:** Meldet ein Lauf `Kein OpenRouter-Anbieter erfüllt Zero Data Retention`, ein anderes Modell wählen; auf ZDR wird nicht verzichtet. Meldet er `Geschätzte Kosten … übersteigen das Limit`, `max_output_tokens` oder `max_cost_usd` bewusst anpassen oder den Zeitraum mit `--from` einschränken.

- [ ] **Step 5: Vergleich und Ansicht**

```bash
docker run --rm -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector python -m analysis compare
docker run --rm -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector python -m analysis list --status bestanden
```

Expected: eine Tabelle mit beiden Modi samt Überlappung, darunter die bestandenen Kandidaten mit Kennzahlen. Die Qualität ist bewusst nicht Teil der Abnahme: Es genügt, dass beide Läufe sauber durchlaufen, die Kennzahlen plausibel aussehen (Abdeckung zwischen 0 und 1) und `nicht_pruefbar`-Kandidaten eine Notiz tragen. **Halt:** Sind alle Kandidaten `nicht_pruefbar`, die Notizen mit `list` lesen; meist fehlt ein Raumname in `room_names` oder das Modell nutzt Bausteine außerhalb des Vokabulars, dann das Vokabular im Prompt prüfen.

- [ ] **Step 6: Abschluss**

1. Änderungen an `analysis.yaml` committen (ohne Schlüssel):

```bash
git add ai/config/analysis.yaml
git commit -m "chore(ai): Modell und Ausschlusszeitraum der Analyse festgelegt"
```

2. Erfolgskriterien 1 bis 6 der Spezifikation einzeln mit Ergebnis abhaken und melden. Pushen nur nach Bestätigung.

---

## Selbstprüfung (nach dem Schreiben)

**Spezifikationsabdeckung:**

| Spezifikation | Aufgabe |
|---------------|---------|
| 2 Erfolgskriterium 1 (beide Modi laufen auf der echten Datenbank) | Aufgabe 11 und Tests in 8, 9 |
| 2 Erfolgskriterium 2 (kein Klarname) | Aufgabe 2 (`leaks`), 8 (`test_prompt_contains_no_real_names`, `PrivacyLeak`), 9 (Dry-Run-Test) |
| 2 Erfolgskriterium 3 (Backtest reproduzierbar) | Aufgabe 6 (`test_backtest_is_reproducible`) |
| 2 Erfolgskriterium 4 (`compare`) | Aufgabe 9 |
| 2 Erfolgskriterium 5 (ZDR erzwungen) | Aufgabe 1 (Konfiguration lässt Abschalten nicht zu), 7 (`provider` in jeder Anfrage, `ZdrUnavailable`) |
| 2 Erfolgskriterium 6 (bekannte Regel, volle Abdeckung) | Aufgabe 8 (`test_known_habit_is_found_across_dst_change`), 6 |
| 3 Entscheidungen | Aufgaben 1 bis 9 |
| 4 Architektur, Stufen 1 bis 6 | Aufgaben 3/4 (Verdichtung), 2 (Pseudonym), 7 (LLM), 5 (Vokabular), 6 (Backtest), 8 (Speichern) |
| 5 Verdichtung, Eingriff, Modi, Kontextquellen, Pseudonymisierung | Aufgaben 3, 4, 2 |
| 6 Kandidatenformat und Vokabular | Aufgabe 5 |
| 7 Backtest und Status | Aufgabe 6 |
| 8 Datenmodell | Aufgabe 1 |
| 9 Kommandos, Konfiguration | Aufgaben 1, 9 |
| 10 Fehlerbehandlung (ungültige Antwort, Netz, atomar, zu wenig Daten, ZDR, Kostenlimit, unbekannte Räume, Zeitzone) | Aufgaben 7, 8, 5, 4/6 |
| 11 Tests | Aufgaben 1 bis 9 (Rauchtest in 11) |
| 12 Risiken | Aufgabe 11 (Halt-Punkte), README-Hinweise in 10 |
| 13 Verzeichnisstruktur | Dateistruktur oben |

**Bewusste Abweichungen von der Spezifikation:** siehe Abschnitt „Dateistruktur“ (Spaltennamen, `note`/`error`, `area_id` mit Namenszuordnung, zusätzliche Dateien).

**Platzhalter-Prüfung:** Die Modell-ID und die Preise in `analysis.yaml` sind ausdrücklich vor dem ersten echten Lauf zu prüfen (Aufgabe 11, Schritt 2), weil sie von OpenRouter abhängen. Das Datum des Thermostattauschs ist ebenfalls beim Einrichten einzutragen. Sonst enthält der Plan keine offenen Stellen.

**Typkonsistenz:** `Intervention`, `Candidate` und die Status-Konstanten (Aufgabe 1) werden in Aufgaben 3, 5, 6, 8, 9 mit denselben Feldnamen genutzt. `Pseudonyms` (2) nutzen `ContextProvider` (4), `parse_candidate` (5), `describe` (9). `ContextProvider.snapshot`-Schlüssel (4) werden in `features._context_shares` (4) gelesen. Das kanonische Kandidatenformat (5) entspricht dem, was `Backtester` (6) und `describe`/`signature` (9) erwarten: Zeit-Auslöser mit `weekdays`/`time`, Ereignis mit `event` und `person` (Entity-ID) beziehungsweise `value`, Bedingungen mit den vier `type`-Werten, Aktion mit `type`/`value`. `Backtester(cfg, interventions, ctx, start, end)` (6) wird von `run_analysis` und `backtest_candidate` (8) so aufgerufen. `request_candidates` (7) liefert `(list[dict], LlmResult)` und `run_analysis` (8) nutzt genau diese Form. `load_environment`/`prepare` (8) werden von der Kommandozeile (9) genutzt.
