# Allgemeine Signale und automatische Voranalyse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Analyse liegt kein von Hand gewählter Kontext mehr zugrunde. Jedes aufgezeichnete Signal wird automatisch auf Zusammenhänge mit manuellen Eingriffen geprüft (`analysis scan`), das Modell bekommt im neuen Modus `scan` die stärksten Zusammenhänge samt Signalkatalog und formuliert daraus Regeln mit einem allgemeinen Vokabular (beliebiges Signal als Auslöser oder Bedingung, Versatz, Dauer des vorherigen Zustands). Der Backtest berechnet diese Regeln ohne Modell.

**Architecture:** Neue Module in `ai/analysis/`: `signals.py` (Signalbibliothek: Art erkennen, Kennungen `sig_NN`, Beschreibungen ohne Namen), `events.py` (Ereignisse je Signal), `scan.py` (Assoziationen mit Kontrollgruppe, exaktem Test und Benjamini-Hochberg-Korrektur), `generic.py` (allgemeines Vokabular). Bestehende Module werden erweitert: Backtest (Signal-Auslöser), Lauf (Modus `scan`), Kommandozeile (`signals`, `scan`). `weekly` und `clusters` bleiben unverändert, die alten Vokabularformen bleiben prüfbar. Der Collector wird nicht verändert.

**Tech Stack:** Python 3.12, asyncio, aiohttp, PyYAML, sqlite3, zoneinfo, math (`lgamma`), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-14-home-ai-signal-scan-design.md` (Basis: `docs/superpowers/specs/2026-10-13-home-ai-analysis-design.md`)

## Global Constraints

Diese Werte stammen aus der Spezifikation und gelten für jede Aufgabe:

- Ein **Signal** ist eine aufgezeichnete Entität (Label `KI_DB_Sync`) mit `kind` ungleich `climate`, `scene`, `button`. Art aus den Zuständen: `categorical` (höchstens 12 Zustände, jeder mindestens 8-mal, sonst `other`; mehr als 50 verschiedene Zustände ist Freitext), `numeric` (mindestens 95 % Zahlen, nicht konstant), `timestamp` (mindestens 95 % ISO-Zeitstempel), sonst `none`. Personen sind immer `categorical` mit den Werten `home` und `away` (jede andere Zone ist `away`).
- Kennungen `sig_NN` stehen dauerhaft in der Tabelle `signals` und werden nie neu vergeben. Beschreibungen für das Modell entstehen nie aus Entity-ID oder Namen (Reihenfolge: `signal_labels`, Person-Alias, Namen der `context`-Einträge, Art und Raum).
- Ereignisse: `to_state` (Wechsel aus einem bekannten Zustand; mit den Minuten im Zustand davor), `rises_above`/`falls_below` an den Terzilen (höchstens einmal je 60 Minuten), `alarm_time` (Zeitstempel T zählt, wenn der Sensor eine Minute vor T noch auf T stand; nur Ereignisse bis zum Ende der Daten). Dauerklassen aus `scan.duration_edges` (Standard 45 und 180 Minuten) und „alle“.
- Scan: Abschnitte von 30 Minuten von 120 Minuten davor bis 120 Minuten danach; Treffer = mindestens ein Eingriff im Abschnitt; Kontrolle = dieselbe lokale Uhrzeit an den 7 Tagen davor und danach ohne Ereignis derselben Art im Umkreis von 120 Minuten; nur Tage mit Eingriffen (±1 Tag); einseitiger exakter Test nach Fisher, Benjamini-Hochberg über alle Tests eines Laufs; aufgenommen bei `hits >= min_hits`, `q <= q_max` und `lift > 1`; benachbarte Abschnitte werden zusammengefasst; `lift` = Trefferanteil / max(Kontrollanteil, 1/(Kontrollereignisse+1)).
- Allgemeines Vokabular: Auslöser `time` (unverändert) oder `signal` (`to_state` mit optionalem `min_prev_min` einschließlich und `max_prev_min` ausschließlich, `rises_above`/`falls_below` mit Schwelle im beobachteten Bereich, `alarm_time`); `offset_min` von -120 bis 120, negativ nur bei `alarm_time`; Bedingungen `weekdays`, `time_window`, `signal_state` (`is`/`is_not`), `signal_range` (`below`/`above`), im Modus `scan` höchstens zwei. Unbekanntes ergibt `nicht_pruefbar`, nie einen Abbruch.
- Datenschutz: An das Modell gehen nur Kennungen, Beschreibungen, Werte, Raumnamen und verdichtete Zahlen. Kein Klarname, keine Entity-ID, keine Benutzer-ID. Die Prüfung `Pseudonyms.leaks` läuft über den gesamten gesendeten Text und bricht den Lauf bei einem Treffer ab.
- Der Collector (`ai/collector/`) wird nicht verändert. Alle bisherigen Tests bleiben grün.
- Befehle im Plan gelten für Git Bash unter Windows (`.venv/Scripts/python`); unter Linux `.venv/bin/python`. Pfade sind relativ zum Repository-Wurzelverzeichnis, `cd ai` steht jeweils im Befehl.
- **Anwendung der Blöcke:** Ein Block mit der Überschrift „`ai/...`:“ und `python`-Code ist der vollständige Inhalt einer neuen Datei. Ein Block „Patch für `ai/...`“ ist ein Unified-Diff gegen den Stand vor dieser Aufgabe: als `_tmp.patch` im Repository-Wurzelverzeichnis speichern, mit `git apply _tmp.patch` anwenden und die Datei wieder löschen. Beide sind geprüft: Der Plan wurde in einer frischen Kopie Schritt für Schritt nachgespielt (Tests scheitern vor der Umsetzung, danach grün).

## Review Focus

Fünf Eingaben beziehungsweise Fehlerfälle, die die Spezifikation voraussetzt, aber nicht ausdrücklich nennt. Jede Zeile hat einen Test in der genannten Aufgabe.

1. **Zufallsdaten dürfen nichts finden:** Eingriffe ohne Zusammenhang zu den Signalen ergeben keine Assoziation (Fehlalarme sind das Hauptrisiko vieler gleichzeitiger Tests). → Aufgabe 4 (`test_random_interventions_produce_no_significant_associations`).
2. **Wecker:** gelöschte oder verschobene Wecker zählen nicht, Weckzeiten in der Zukunft oder in der Vergangenheit der Einstellung ebenso nicht; die Weckzeit wird ohne Sonderbehandlung gefunden. → Aufgabe 3 (`test_deleted_or_moved_alarms_do_not_count`, `test_future_alarms_and_alarms_after_until_are_ignored`), Aufgabe 4 (`test_alarm_signal_is_found_without_special_handling`).
3. **Unsinn vom Modell:** unbekannte Signal-Kennung, falsche Art, Zustand oder Schwelle außerhalb des Bekannten, negativer Versatz bei Nicht-Weckzeit, mehr als zwei Bedingungen, Zonen- oder Namensreste in Beschreibungen. → Aufgabe 5 (`test_invalid_signal_triggers_raise`), Aufgabe 7 (`test_names_in_signal_labels_abort_the_run_before_sending`).
4. **Stabilität:** Kennungen ändern sich nicht, wenn Entitäten hinzukommen; gespeicherte Signal-Kandidaten lassen sich neu rechnen. → Aufgabe 1 (`test_assign_handles_are_stable_and_never_reused`), Aufgabe 2 (`test_handles_stay_stable_when_entities_are_added`), Aufgabe 7 (`test_stored_generic_candidates_can_be_backtested_again`).
5. **Alte Modi:** `weekly` und `clusters` verhalten sich wie vorher (kein allgemeines Vokabular, keine Begrenzung der Bedingungen) und alle bisherigen Tests bleiben grün. → Aufgabe 7 (`test_old_modes_do_not_accept_generic_candidates`).

---

## Dateistruktur

```
ai/
  analysis/
    signals.py       # neu: Signalbibliothek
    events.py        # neu: Ereignisse je Signal
    scan.py          # neu: Assoziationsscan
    generic.py       # neu: allgemeines Vokabular
    config.py, models.py, store.py, timeline.py, presence.py, vocabulary.py,
    backtest.py, context.py, prompts.py, runner.py, candidates.py, cli.py   # geändert
  config/analysis.yaml      # geändert: signal_labels, scan
  sql/analysis.sql          # geändert: signals, associations
  tests/
    scan_fixture.py         # neu: Datenbank mit bekannter Gewohnheit, Wecker, Zufall
    test_signals.py, test_events.py, test_scan.py, test_generic.py,
    test_scan_mode.py, test_scan_cli.py                                    # neu
    test_analysis_config.py, test_store.py, test_backtest.py               # erweitert
  README.md                 # geändert
```

---

### Task 1: Konfiguration, Schema, Store und Signal-Kennungen

**Files:**
- Modify: `ai/tests/test_analysis_config.py`
- Modify: `ai/tests/test_store.py`
- Modify: `ai/analysis/config.py`
- Modify: `ai/analysis/models.py`
- Modify: `ai/analysis/store.py`
- Modify: `ai/analysis/timeline.py`
- Modify: `ai/config/analysis.yaml`
- Modify: `ai/sql/analysis.sql`

**Interfaces:**
- Consumes: `collector.db.Database` (Attribut `conn`), `collector.models.parse_utc`, `analysis.config.load_analysis_config`, bestehende `Store`-Methoden.
- Produces:
  - `analysis.config`: `ScanParams(min_events=8, min_hits=4, q_max=0.1, top_k=40, min_room_interventions=10, duration_edges=(45, 180), control_days=7, max_values=12, min_value_count=8)`; `AnalysisConfig.signal_labels: dict[str, str]` und `AnalysisConfig.scan: ScanParams`; neue Top-Level-Schlüssel `signal_labels` und `scan` in `analysis.yaml`.
  - `analysis.models.Association(signal, event, value, prev_class, room, win_start, win_end, events, hits, control_events, control_hits, lift, p, q, typical_action="")`.
  - `analysis.store.Store`: `assign_handles(entity_ids) -> dict[str, str]` (committet; Kennungen `sig_NN` dauerhaft, nie neu vergeben), `update_signal(handle, kind, value_kind, description)` (committet), `list_signals()`, `signal_descriptions() -> dict[str, str]`, `save_associations(run_id, items)` (committet nicht), `list_associations(run_id) -> list[Association]` (nach q, lift geordnet).
  - Tabellen `signals` und `associations` in `ai/sql/analysis.sql`.
  - `analysis.timeline.Timeline.points() -> list[tuple[datetime, str | None]]`.

- [ ] **Step 1: Tests schreiben**

Patch für `ai/tests/test_analysis_config.py`:

````diff
--- a/ai/tests/test_analysis_config.py
+++ b/ai/tests/test_analysis_config.py
@@ -121,3 +121,22 @@
     assert load_analysis_config(write(tmp_path, MINIMAL), env={}).model_source == "datei"
     cfg = load_analysis_config(write(tmp_path, MINIMAL), env={"OPENROUTER_MODEL": "v/m"})
     assert cfg.model_source == "env"
+
+
+def test_scan_defaults_and_signal_labels_of_the_shipped_config():
+    cfg = load_analysis_config(ROOT / "config" / "analysis.yaml")
+    assert cfg.scan.min_events == 8 and cfg.scan.min_hits == 4 and cfg.scan.q_max == 0.1
+    assert cfg.scan.duration_edges == (45, 180) and cfg.scan.top_k == 40 and cfg.scan.control_days == 7
+    assert cfg.signal_labels["sensor.pixel_9a_next_alarm"].startswith("Weckzeit")
+    assert "Fabian" not in " ".join(cfg.signal_labels.values())
+
+
+def test_scan_section_is_optional_and_validated(tmp_path):
+    cfg = load_analysis_config(write(tmp_path, MINIMAL))
+    assert cfg.scan.q_max == 0.1 and cfg.signal_labels == {}
+    custom = load_analysis_config(write(tmp_path, MINIMAL + "scan: {duration_edges: [90, 30], top_k: 5}\n"))
+    assert custom.scan.duration_edges == (30, 90) and custom.scan.top_k == 5
+    for bad, match in (("scan: {nope: 1}\n", "nope"), ("scan: {q_max: 0}\n", "q_max"),
+                       ("scan: {duration_edges: []}\n", "duration_edges"), ("scan: {min_hits: 0}\n", "min_hits")):
+        with pytest.raises(AnalysisConfigError, match=match):
+            load_analysis_config(write(tmp_path, MINIMAL + bad))
````

Patch für `ai/tests/test_store.py`:

````diff
--- a/ai/tests/test_store.py
+++ b/ai/tests/test_store.py
@@ -73,3 +73,32 @@
     store.save_pseudonyms({"person_1": ("person.a", "person"), "person_2": ("person.b", "person")})
     store.commit()
     assert store.load_pseudonyms() == {"person_1": "person.a", "person_2": "person.b"}
+
+
+def test_assign_handles_are_stable_and_never_reused(store):
+    first = store.assign_handles(["sensor.b", "sensor.a"])
+    assert first == {"sensor.a": "sig_01", "sensor.b": "sig_02"}
+    again = store.assign_handles(["sensor.b", "sensor.0_new", "sensor.a"])
+    assert again == {"sensor.b": "sig_02", "sensor.0_new": "sig_03", "sensor.a": "sig_01"}
+    store.update_signal("sig_03", "env", "numeric", "Sensor")
+    assert store.signal_descriptions()["sig_03"] == "Sensor"
+    assert [row["handle"] for row in store.list_signals()] == ["sig_01", "sig_02", "sig_03"]
+
+
+def test_associations_roundtrip_in_rank_order(store):
+    from analysis.models import Association
+    run_id = store.start_run("scan-only", "-", {}, NOW)
+    weak = Association("sig_01", "to_state", "home", "alle", None, 0, 30, 30, 8, 200, 20, 2.5, 0.01, 0.04, "Sollwert 21 °C (6 von 8)")
+    strong = Association("sig_02", "alarm_time", None, "alle", "r_living", 0, 30, 40, 38, 500, 30, 15.0, 1e-9, 1e-7)
+    store.save_associations(run_id, [weak, strong])
+    store.commit()
+    assert store.list_associations(run_id) == [strong, weak]
+    assert store.list_associations(run_id + 1) == []
+
+
+def test_association_rows_vanish_on_rollback(store):
+    from analysis.models import Association
+    run_id = store.start_run("scan", "m", {}, NOW)
+    store.save_associations(run_id, [Association("sig_01", "to_state", "home", "alle", None, 0, 30, 10, 5, 50, 2, 3.0, 0.01, 0.02)])
+    store.rollback()
+    assert store.list_associations(run_id) == []
````

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_analysis_config.py tests/test_store.py -v`
Expected: FAIL (5 der neuen Tests scheitern, die übrigen Tests der Dateien bestehen). Die neuen Tests greifen auf `cfg.scan`, `cfg.signal_labels` und die neuen `Store`-Methoden zu, die noch fehlen.

- [ ] **Step 3: Implementieren**

Patch für `ai/analysis/config.py`:

````diff
--- a/ai/analysis/config.py
+++ b/ai/analysis/config.py
@@ -16,7 +16,7 @@
     "model", "openrouter_url", "provider", "price_per_mtok", "max_output_tokens", "max_cost_usd",
     "temperature", "request_timeout_s", "retries", "retry_backoff_s", "max_candidates", "timezone",
     "min_interventions", "merge_interventions_s", "room_names", "exclusions", "context", "clusters",
-    "backtest",
+    "backtest", "signal_labels", "scan",
 }
 CONTEXT_KEYS = {"flat_state", "in_bed", "office_day", "outside_temperature"}
 
@@ -42,6 +42,19 @@
     min_abdeckung: float = 0.3
     max_fehlausloesungen: float = 0.4
     active_gap_days: int = 21        # Eingriffs-Pausen darüber trennen aktive Zeiträume; 0 schaltet den Filter ab
+
+
+@dataclass(frozen=True)
+class ScanParams:
+    min_events: int = 8              # Ereignisart mit weniger Ereignissen wird nicht geprüft
+    min_hits: int = 4                # so viele Ereignisse mit Eingriff mindestens
+    q_max: float = 0.1               # Grenze der korrigierten Irrtumswahrscheinlichkeit
+    top_k: int = 40                  # so viele Assoziationen gehen an das Modell
+    min_room_interventions: int = 10
+    duration_edges: tuple[int, ...] = (45, 180)    # Minuten, Klassengrenzen der Dauer des vorherigen Zustands
+    control_days: int = 7            # Kontrolltage vor und nach dem Ereignis
+    max_values: int = 12             # mehr verschiedene Zustände: Signal ist keine Kategorie
+    min_value_count: int = 8         # seltenere Zustände werden zu "other"
 
 
 @dataclass(frozen=True)
@@ -74,6 +87,8 @@
     context: dict[str, str]
     clusters: ClusterParams
     backtest: BacktestParams
+    signal_labels: dict[str, str] = field(default_factory=dict)
+    scan: ScanParams = field(default_factory=ScanParams)
     price_source: str = "datei"             # datei | env | openrouter
     model_source: str = "datei"             # datei | env
 
@@ -82,6 +97,18 @@
     unknown = set(data) - allowed
     if unknown:
         raise AnalysisConfigError(f"{where}: unbekannte Schlüssel {sorted(unknown)}")
+
+
+def _scan(raw: dict[str, Any]) -> ScanParams:
+    _only("scan", raw, {f.name for f in fields(ScanParams)})
+    params = ScanParams(**{key: value for key, value in raw.items() if key != "duration_edges"})
+    edges = tuple(sorted({int(edge) for edge in raw.get("duration_edges", params.duration_edges)}))
+    if not edges or edges[0] <= 0:
+        raise AnalysisConfigError("scan.duration_edges braucht mindestens eine positive Zahl")
+    if not 0 < params.q_max <= 1 or params.min_hits < 1 or params.min_events < 1:
+        raise AnalysisConfigError("scan: q_max muss in (0, 1] liegen, min_hits und min_events mindestens 1")
+    return ScanParams(**{**{key: value for key, value in raw.items() if key != "duration_edges"},
+                         "duration_edges": edges})
 
 
 def _clusters(raw: dict[str, Any]) -> ClusterParams:
@@ -170,6 +197,8 @@
         context={key: str(value) for key, value in context.items()},
         clusters=_clusters(dict(raw.get("clusters") or {})),
         backtest=BacktestParams(**backtest_raw),
+        signal_labels={str(k): str(v) for k, v in (raw.get("signal_labels") or {}).items()},
+        scan=_scan(dict(raw.get("scan") or {})),
         price_source="env" if from_env else "datei",
         model_source="env" if env_model else "datei",
     )
````

Patch für `ai/analysis/models.py`:

````diff
--- a/ai/analysis/models.py
+++ b/ai/analysis/models.py
@@ -24,6 +24,27 @@
 
 
 @dataclass
+class Association:
+    """Ergebnis des Scans: Um dieses Ereignis herum liegen auffällig oft Eingriffe."""
+
+    signal: str                  # sig_NN
+    event: str                   # to_state | rises_above | falls_below | alarm_time
+    value: str | None            # Zustand beziehungsweise Schwelle
+    prev_class: str              # "alle" oder Dauerklasse des vorherigen Zustands, z. B. "≥180"
+    room: str | None             # area_id, None = alle Räume
+    win_start: int               # Minuten relativ zum Ereignis
+    win_end: int
+    events: int
+    hits: int
+    control_events: int
+    control_hits: int
+    lift: float
+    p: float
+    q: float
+    typical_action: str = ""
+
+
+@dataclass
 class Candidate:
     room: str                    # area_id
     trigger: dict[str, Any]
````

Patch für `ai/analysis/store.py`:

````diff
--- a/ai/analysis/store.py
+++ b/ai/analysis/store.py
@@ -7,7 +7,7 @@
 from collector.db import Database
 from collector.models import to_utc_iso
 
-from .models import Candidate
+from .models import Association, Candidate
 
 SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "analysis.sql"
 
@@ -136,3 +136,46 @@
 
     def load_pseudonyms(self) -> dict[str, str]:
         return {row["alias"]: row["entity_id"] for row in self.conn.execute("SELECT * FROM pseudonyms")}
+
+    # --- Signale und Assoziationen ----------------------------------------------
+
+    def assign_handles(self, entity_ids: list[str]) -> dict[str, str]:
+        """Kennungen sig_NN: bestehende bleiben, neue Entitäten bekommen die nächste freie Nummer."""
+        known = {row["entity_id"]: row["handle"]
+                 for row in self.conn.execute("SELECT handle, entity_id FROM signals")}
+        highest = max((int(handle.split("_")[1]) for handle in known.values()), default=0)
+        for entity_id in sorted(entity_ids):
+            if entity_id not in known:
+                highest += 1
+                known[entity_id] = f"sig_{highest:02d}"
+                self.conn.execute(
+                    "INSERT INTO signals(handle, entity_id, kind, value_kind, description) "
+                    "VALUES (?, ?, '', 'none', '')", (known[entity_id], entity_id))
+        self.conn.commit()
+        return {entity_id: known[entity_id] for entity_id in entity_ids}
+
+    def update_signal(self, handle: str, kind: str, value_kind: str, description: str) -> None:
+        self.conn.execute("UPDATE signals SET kind = ?, value_kind = ?, description = ? WHERE handle = ?",
+                          (kind, value_kind, description, handle))
+        self.conn.commit()
+
+    def list_signals(self):
+        return self.conn.execute("SELECT * FROM signals ORDER BY handle").fetchall()
+
+    def signal_descriptions(self) -> dict[str, str]:
+        return {row["handle"]: row["description"] for row in self.list_signals()}
+
+    def save_associations(self, run_id: int, items: list[Association]) -> None:
+        self.conn.executemany(
+            "INSERT INTO associations(run_id, signal, event, value, prev_class, room, win_start, win_end, "
+            "events, hits, control_events, control_hits, lift, p, q, typical_action) "
+            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
+            [(run_id, a.signal, a.event, a.value, a.prev_class, a.room, a.win_start, a.win_end, a.events,
+              a.hits, a.control_events, a.control_hits, a.lift, a.p, a.q, a.typical_action) for a in items])
+
+    def list_associations(self, run_id: int) -> list[Association]:
+        rows = self.conn.execute("SELECT * FROM associations WHERE run_id = ? ORDER BY q, -lift, id", (run_id,))
+        return [Association(row["signal"], row["event"], row["value"], row["prev_class"], row["room"],
+                            row["win_start"], row["win_end"], row["events"], row["hits"],
+                            row["control_events"], row["control_hits"], row["lift"], row["p"], row["q"],
+                            row["typical_action"] or "") for row in rows]
````

Patch für `ai/analysis/timeline.py`:

````diff
--- a/ai/analysis/timeline.py
+++ b/ai/analysis/timeline.py
@@ -16,6 +16,9 @@
 
     def __bool__(self) -> bool:
         return bool(self._points)
+
+    def points(self) -> list[tuple[datetime, str | None]]:
+        return list(self._points)
 
     def first_ts(self) -> datetime | None:
         return self._times[0] if self._times else None
````

Patch für `ai/config/analysis.yaml`:

````diff
--- a/ai/config/analysis.yaml
+++ b/ai/config/analysis.yaml
@@ -39,6 +39,25 @@
   office_day: binary_sensor.office_day_template
   outside_temperature: sensor.environment_sensor_outside_temperature
 
+# Beschreibungen für das Modell (statt aus Namen abgeleitet). Keine Klarnamen verwenden.
+signal_labels:
+  sensor.pixel_9a_next_alarm: "Weckzeit (nächster Wecker, Gerät 1)"
+  sensor.sm_s901b_next_alarm: "Weckzeit (nächster Wecker, Gerät 2)"
+  sensor.fabians_mobile_alarmclock_triggered: "Wecker klingelt (Person 1)"
+  sensor.svenjas_mobile_alarmclock_triggered: "Wecker klingelt (Person 2)"
+
+# Automatische Voranalyse: Signale, Ereignisse, Kontrolle, Signifikanz
+scan:
+  min_events: 8
+  min_hits: 4
+  q_max: 0.1
+  top_k: 40
+  min_room_interventions: 10
+  duration_edges: [45, 180]
+  control_days: 7
+  max_values: 12
+  min_value_count: 8
+
 clusters:
   min_size: 3
   window_min: 90                           # Breite des Zeitfensters (±45 Minuten)
````

Patch für `ai/sql/analysis.sql`:

````diff
--- a/ai/sql/analysis.sql
+++ b/ai/sql/analysis.sql
@@ -35,6 +35,35 @@
   PRIMARY KEY(candidate_id, metric)
 );
 
+CREATE TABLE IF NOT EXISTS signals(
+  handle      TEXT PRIMARY KEY,                -- sig_01, sig_02, ... (dauerhaft, nie neu vergeben)
+  entity_id   TEXT NOT NULL UNIQUE,
+  kind        TEXT NOT NULL,
+  value_kind  TEXT NOT NULL,                   -- categorical|numeric|timestamp|none
+  description TEXT NOT NULL
+);
+
+CREATE TABLE IF NOT EXISTS associations(
+  id             INTEGER PRIMARY KEY,
+  run_id         INTEGER NOT NULL REFERENCES analysis_runs(id),
+  signal         TEXT NOT NULL,
+  event          TEXT NOT NULL,                -- to_state|rises_above|falls_below|alarm_time
+  value          TEXT,
+  prev_class     TEXT,                         -- alle|<45|45-180|>=180 ...
+  room           TEXT,                         -- area_id, NULL = alle Räume
+  win_start      INTEGER NOT NULL,             -- Minuten relativ zum Ereignis
+  win_end        INTEGER NOT NULL,
+  events         INTEGER NOT NULL,
+  hits           INTEGER NOT NULL,
+  control_events INTEGER NOT NULL,
+  control_hits   INTEGER NOT NULL,
+  lift           REAL NOT NULL,
+  p              REAL NOT NULL,
+  q              REAL NOT NULL,
+  typical_action TEXT
+);
+CREATE INDEX IF NOT EXISTS idx_assoc_run ON associations(run_id);
+
 CREATE TABLE IF NOT EXISTS pseudonyms(
   alias     TEXT PRIMARY KEY,                  -- person_1, person_2, ...
   entity_id TEXT NOT NULL,
````

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_analysis_config.py tests/test_store.py -v`
Expected: PASS (27 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (331 Tests insgesamt).

```bash
git add ai/analysis ai/config ai/sql ai/tests
git commit -m "feat(ai): Konfiguration, Schema und Store für Signale und Assoziationen"
```

---

### Task 2: Signalbibliothek

**Files:**
- Create: `ai/tests/test_signals.py`
- Create: `ai/analysis/signals.py`

**Interfaces:**
- Consumes: `Timeline` (`points`, `value_at`, `numeric_at`), `load_timeline`, `Store.assign_handles`/`update_signal`, `AnalysisConfig.scan`/`signal_labels`/`context`/`room_names`, `Pseudonyms`, `features.room_name`.
- Produces: `analysis.signals`: `SignalInfo(handle, value_kind, description, values=(), low=None, high=None, terciles=None)` (`value_kind` ist `categorical`, `numeric`, `timestamp` oder `none`), `Signal(info, entity_id, kind, room, timeline)` mit `.handle`, `build_signal(handle, entity_id, kind, room, raw_timeline, cfg, pseudo) -> Signal`, `SignalLibrary(signals)` mit `from_db(db, cfg, pseudo)`, `get(handle)`, `all()`, `usable()`, `infos() -> dict[str, SignalInfo]`, `catalog() -> list[dict]`, `leaks(pseudo) -> list[str]`.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_signals.py`:

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from analysis.pseudonym import Pseudonyms
from analysis.signals import SignalLibrary, build_signal
from analysis.timeline import Timeline
from collector.models import EntityRule
from tests.analysis_fixture import ROOM, build_habit_db

T0 = datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def timeline(*states, step_min=30):
    return Timeline([(T0 + timedelta(minutes=step_min * index), state) for index, state in enumerate(states)])


def pseudo(*persons):
    return Pseudonyms(tuple(persons), {}, {})


def build(acfg, entity_id, kind, states, *, room=None, persons=()):
    return build_signal("sig_01", entity_id, kind, room, timeline(*states), acfg, pseudo(*persons))


def test_person_zones_collapse_to_home_and_away(acfg):
    signal = build(acfg, "person.a", "person", ["home", "Work", "not_home", "unavailable", "home"],
                   persons=("person.a",))
    assert signal.info.value_kind == "categorical" and signal.info.values == ("away", "home")
    assert [state for _, state in signal.timeline.points()] == ["home", "away", "away", None, "home"]


def test_binary_states_are_categorical(acfg):
    states = ["on", "off"] * 10
    signal = build(acfg, "binary_sensor.x", "flat", states)
    assert signal.info.value_kind == "categorical" and signal.info.values == ("off", "on")


def test_rare_values_become_other(acfg):
    states = ["a", "b"] * 10 + ["selten"]
    signal = build(acfg, "input_select.x", "flat", states)
    assert signal.info.values == ("a", "b")
    assert signal.timeline.points()[-1][1] == "other"


def test_numbers_are_numeric_with_terciles(acfg):
    signal = build(acfg, "sensor.t", "env", [str(value) for value in range(30)])
    assert signal.info.value_kind == "numeric"
    assert (signal.info.low, signal.info.high) == (0.0, 29.0)
    assert signal.info.terciles == (10.0, 20.0)


def test_constant_numbers_are_unusable(acfg):
    assert build(acfg, "sensor.t", "env", ["5"] * 12).info.value_kind == "none"


def test_iso_timestamps_are_timestamp_signals(acfg):
    states = [f"2026-01-0{day}T06:30:00+00:00" for day in range(1, 7)]
    assert build(acfg, "sensor.alarm", "env", states).info.value_kind == "timestamp"


def test_free_text_is_not_a_signal(acfg):
    assert build(acfg, "sensor.text", "env", [f"text {n}" for n in range(60)]).info.value_kind == "none"


def test_signal_with_one_known_state_is_unusable(acfg):
    assert build(acfg, "sensor.x", "env", ["unavailable", "on", "unknown"]).info.value_kind == "none"


def test_description_never_comes_from_the_entity_id(acfg):
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"}, signal_labels={"sensor.alarm": "Weckzeit"},
                  context={**acfg.context, "in_bed": "input_boolean.bett"})
    contact = build_signal("sig_01", "binary_sensor.fabians_fenster", "contact", ROOM, timeline("on", "off"), cfg, pseudo())
    assert contact.info.description == "Kontakt, Wohnzimmer" and "fabian" not in contact.info.description.lower()
    assert build_signal("sig_02", "sensor.alarm", "env", None, timeline("a", "b"), cfg, pseudo()).info.description == "Weckzeit"
    assert build_signal("sig_03", "input_boolean.bett", "flat", None, timeline("on", "off"), cfg, pseudo()).info.description == "Jemand im Bett"
    person = build_signal("sig_04", "person.svenja", "person", None, timeline("home", "away"), cfg, pseudo("person.fabian", "person.svenja"))
    assert person.info.description == "Anwesenheit person_2 (home oder away)"


@pytest.fixture
def cfg(acfg):
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())


def test_library_builds_signals_from_the_database_and_skips_thermostats(db, cfg):
    build_habit_db(db)
    from analysis.pseudonym import build_pseudonyms
    library = SignalLibrary.from_db(db, cfg, build_pseudonyms(db))
    by_entity = {signal.entity_id: signal for signal in library.all()}
    assert "climate.bt" not in by_entity
    assert by_entity["person.svenja"].info.value_kind == "categorical"
    assert by_entity["person.fabian"].info.value_kind == "none"           # keine Zustandswechsel aufgezeichnet
    assert [signal.entity_id for signal in library.usable()] == ["person.svenja"]
    assert library.get(by_entity["person.svenja"].handle) is by_entity["person.svenja"]


def test_handles_stay_stable_when_entities_are_added(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    build_habit_db(db)
    first = {s.entity_id: s.handle for s in SignalLibrary.from_db(db, cfg, build_pseudonyms(db)).all()}
    db.upsert_entity(EntityRule("binary_sensor.a_new", "flat", "shared"), None, NOW)     # sortiert vor person.*
    second = {s.entity_id: s.handle for s in SignalLibrary.from_db(db, cfg, build_pseudonyms(db)).all()}
    assert all(second[entity] == handle for entity, handle in first.items())
    assert second["binary_sensor.a_new"] == "sig_03"


def test_catalog_lists_only_usable_signals_with_their_values(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    build_habit_db(db)
    catalog = SignalLibrary.from_db(db, cfg, build_pseudonyms(db)).catalog()
    assert catalog == [{"id": "sig_02", "beschreibung": "Anwesenheit person_2 (home oder away)",
                        "art": "Kategorie", "werte": ["away", "home"]}]


def test_leak_check_flags_names_in_labels(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    build_habit_db(db)
    bad = replace(cfg, signal_labels={"person.svenja": "Handy von Svenja"})
    pseudo_ = build_pseudonyms(db)
    assert SignalLibrary.from_db(db, bad, pseudo_).leaks(pseudo_) == ["svenja"]
    assert SignalLibrary.from_db(db, cfg, pseudo_).leaks(pseudo_) == []


def test_signal_rows_are_persisted_with_description(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    from analysis.store import Store
    build_habit_db(db)
    SignalLibrary.from_db(db, cfg, build_pseudonyms(db))
    rows = {row["entity_id"]: row for row in Store(db).list_signals()}
    assert rows["person.svenja"]["value_kind"] == "categorical"
    assert rows["person.svenja"]["description"].startswith("Anwesenheit person_")
    assert Store(db).signal_descriptions()[rows["person.svenja"]["handle"]] == rows["person.svenja"]["description"]
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_signals.py -v`
Expected: FAIL mit Collection-Fehler (`ModuleNotFoundError` oder `ImportError` der neuen Module).

- [ ] **Step 3: Implementieren**

`ai/analysis/signals.py`:

```python
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from collector.db import Database

from .config import AnalysisConfig
from .features import room_name
from .pseudonym import Pseudonyms
from .store import Store
from .timeline import Timeline, load_timeline

UNKNOWN_STATES = ("unavailable", "unknown", "none", "")
SKIP_KINDS = {"climate", "scene", "button"}      # Thermostate sind das Ziel; Szenen und Tasten haben keinen Verlauf
KIND_NAMES = {"person": "Anwesenheit", "presence": "Präsenz", "contact": "Kontakt", "flat": "Zustand",
              "env": "Sensor", "light": "Licht", "weather": "Wetter", "schedule": "Zeitplan"}
VALUE_KIND_NAMES = {"categorical": "Kategorie", "numeric": "Zahl", "timestamp": "Zeitstempel", "none": "unbrauchbar"}
CONTEXT_NAMES = {"flat_state": "Wohnungszustand", "in_bed": "Jemand im Bett", "office_day": "Bürotag",
                 "outside_temperature": "Außentemperatur"}
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
MAX_DISTINCT = 50                                  # mehr verschiedene Zustände: Freitext, keine Kategorie


@dataclass(frozen=True)
class SignalInfo:
    """Alles, was zum Prüfen von Kandidaten und für den Katalog nötig ist (ohne Zeitreihe)."""

    handle: str
    value_kind: str                               # categorical | numeric | timestamp | none
    description: str
    values: tuple[str, ...] = ()                  # Kategorien ohne "other"
    low: float | None = None
    high: float | None = None
    terciles: tuple[float, float] | None = None


@dataclass
class Signal:
    info: SignalInfo
    entity_id: str
    kind: str
    room: str | None
    timeline: Timeline                            # normalisiert, Unbekanntes als None

    @property
    def handle(self) -> str:
        return self.info.handle


def _is_number(state: str) -> bool:
    try:
        float(state)
    except ValueError:
        return False
    return True


def _known(state: str | None) -> bool:
    return state is not None and state.lower() not in UNKNOWN_STATES


def build_signal(handle: str, entity_id: str, kind: str, room: str | None, raw: Timeline,
                 cfg: AnalysisConfig, pseudo: Pseudonyms) -> Signal:
    """Erkennt die Art des Signals aus den Zuständen und normalisiert den Verlauf."""
    params = cfg.scan
    points = raw.points()
    states = [state for _, state in points if _known(state)]
    description = _describe(entity_id, kind, room, cfg, pseudo)
    is_person = entity_id in pseudo.persons

    def finish(value_kind: str, normalized: list, **extra) -> Signal:
        info = SignalInfo(handle, value_kind, description, **extra)
        return Signal(info, entity_id, kind, room, Timeline(normalized))

    if len(states) < 2:
        return finish("none", [])
    if not is_person and sum(_is_number(s) for s in states) / len(states) >= 0.95:
        numbers = sorted(float(s) for s in states if _is_number(s))
        if numbers[0] == numbers[-1]:
            return finish("none", [])
        terciles = (numbers[len(numbers) // 3], numbers[(2 * len(numbers)) // 3])
        normalized = [(ts, state if _known(state) and _is_number(state) else None) for ts, state in points]
        return finish("numeric", normalized, low=numbers[0], high=numbers[-1], terciles=terciles)
    if not is_person and sum(bool(ISO_RE.match(s)) for s in states) / len(states) >= 0.95:
        normalized = [(ts, state if _known(state) else None) for ts, state in points]
        return finish("timestamp", normalized)
    if is_person:
        normalized = [(ts, ("home" if state == "home" else "away") if _known(state) else None)
                      for ts, state in points]
        values = tuple(sorted({state for _, state in normalized if state is not None}))
        return finish("categorical", normalized, values=values)
    counts = Counter(states)
    if len(counts) > MAX_DISTINCT:
        return finish("none", [])
    frequent = sorted(value for value, count in counts.items() if count >= params.min_value_count)
    if not frequent or len(frequent) > params.max_values:
        return finish("none", [])
    keep = set(frequent)
    normalized = [(ts, (state if state in keep else "other") if _known(state) else None) for ts, state in points]
    return finish("categorical", normalized, values=tuple(frequent))


def _describe(entity_id: str, kind: str, room: str | None, cfg: AnalysisConfig, pseudo: Pseudonyms) -> str:
    """Beschreibung für das Modell: nie aus Entity-ID oder Namen, nur aus Art, Raum und Konfiguration."""
    label = cfg.signal_labels.get(entity_id)
    if label:
        return label
    alias = pseudo.alias_for_person(entity_id)
    if alias:
        return f"Anwesenheit {alias} (home oder away)"
    for key, name in CONTEXT_NAMES.items():
        if cfg.context.get(key) == entity_id:
            return name
    where = f", {room_name(cfg, room)}" if room else ""
    return f"{KIND_NAMES.get(kind, kind)}{where}"


class SignalLibrary:
    def __init__(self, signals: list[Signal]):
        self._signals = sorted(signals, key=lambda signal: signal.handle)
        self._by_handle = {signal.handle: signal for signal in self._signals}

    @classmethod
    def from_db(cls, db: Database, cfg: AnalysisConfig, pseudo: Pseudonyms) -> "SignalLibrary":
        rows = [row for row in db.conn.execute(
            "SELECT entity_id, kind, room FROM entities WHERE active = 1 ORDER BY entity_id")
            if row["kind"] not in SKIP_KINDS]
        store = Store(db)
        handles = store.assign_handles([row["entity_id"] for row in rows])
        signals = []
        for row in rows:
            signal = build_signal(handles[row["entity_id"]], row["entity_id"], row["kind"], row["room"],
                                  load_timeline(db, row["entity_id"]), cfg, pseudo)
            store.update_signal(signal.handle, signal.kind, signal.info.value_kind, signal.info.description)
            signals.append(signal)
        return cls(signals)

    def get(self, handle: str) -> Signal | None:
        return self._by_handle.get(handle)

    def all(self) -> list[Signal]:
        return list(self._signals)

    def usable(self) -> list[Signal]:
        return [signal for signal in self._signals if signal.info.value_kind != "none" and signal.timeline]

    def infos(self) -> dict[str, SignalInfo]:
        return {signal.handle: signal.info for signal in self.usable()}

    def catalog(self) -> list[dict]:
        entries = []
        for signal in self.usable():
            info = signal.info
            entry: dict = {"id": info.handle, "beschreibung": info.description,
                           "art": VALUE_KIND_NAMES[info.value_kind]}
            if info.value_kind == "categorical":
                entry["werte"] = list(info.values)
            elif info.value_kind == "numeric":
                entry["minimum"], entry["maximum"] = info.low, info.high
                entry["terzile"] = list(info.terciles)
            entries.append(entry)
        return entries

    def leaks(self, pseudo: Pseudonyms) -> list[str]:
        text = "\n".join(f"{s.info.description} {' '.join(s.info.values)}" for s in self.usable())
        return pseudo.leaks(text)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_signals.py -v`
Expected: PASS (14 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (345 Tests insgesamt).

```bash
git add ai/analysis ai/tests
git commit -m "feat(ai): Signalbibliothek mit Arten, Kennungen und Beschreibungen"
```

---

### Task 3: Ereignisse je Signal

**Files:**
- Create: `ai/tests/test_events.py`
- Create: `ai/analysis/events.py`
- Modify: `ai/analysis/presence.py`

**Interfaces:**
- Consumes: `Signal`, `SignalLibrary.usable()`, `ScanParams`, `collector.models.parse_utc`.
- Produces: `analysis.events`: `class_labels(edges)`, `class_of(minutes, edges)`, `EventSpec(signal, event, value, prev_class)`, `EventSeries(spec, times, prev)`, `signal_events(signal, event, value=None, until=None) -> list[tuple[datetime, float | None]]` (Ereignis `to_state` mit den Minuten im Zustand davor, `rises_above`, `falls_below`, `alarm_time`), `event_series(library, params, until=None) -> list[EventSeries]`, `event_text(spec) -> str`, Konstante `ALL = "alle"`. `analysis.presence` importiert `class_labels` von dort.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_events.py`:

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from analysis.config import ScanParams
from analysis.events import (ALL, EventSpec, class_labels, class_of, event_series, event_text, signal_events)
from analysis.signals import Signal, SignalInfo, SignalLibrary
from analysis.timeline import Timeline

T0 = datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def make(value_kind, points, **extra):
    info = SignalInfo("sig_01", value_kind, "Test", **extra)
    return Signal(info, "x.y", "env", None, Timeline([(at(minutes), state) for minutes, state in points]))


def test_class_labels_and_assignment():
    assert class_labels((45, 180)) == ["<45", "45-180", "≥180"]
    assert [class_of(m, (45, 180)) for m in (0, 44.9, 45, 179, 180, 5000)] == ["<45", "<45", "45-180", "45-180", "≥180", "≥180"]
    assert class_labels((60,)) == ["<60", "≥60"]


def test_to_state_events_carry_the_duration_of_the_previous_state():
    signal = make("categorical", [(0, "home"), (600, "away"), (620, "home"), (800, "away"), (1000, "home")],
                  values=("away", "home"))
    events = signal_events(signal, "to_state", "home")
    assert [(ts, round(prev)) for ts, prev in events] == [(at(620), 20), (at(1000), 200)]


def test_attribute_only_rows_do_not_restart_the_state_duration():
    signal = make("categorical", [(0, "away"), (100, "away"), (200, "home")], values=("away", "home"))
    assert [round(prev) for _, prev in signal_events(signal, "to_state", "home")] == [200]


def test_changes_from_an_unknown_state_are_not_events():
    signal = make("categorical", [(0, "home"), (100, "away"), (200, None), (300, "home")], values=("away", "home"))
    assert signal_events(signal, "to_state", "home") == []


def test_threshold_crossings_are_directional_and_throttled():
    points = [(0, "5"), (10, "9"), (20, "5"), (30, "9"), (200, "5"), (300, "9")]
    signal = make("numeric", points, low=5.0, high=9.0, terciles=(6.0, 8.0))
    rising = signal_events(signal, "rises_above", 8.0)
    assert [ts for ts, _ in rising] == [at(10), at(300)]                 # 30 liegt innerhalb einer Stunde nach 10
    assert [ts for ts, _ in signal_events(signal, "falls_below", 8.0)] == [at(20), at(200)]


def test_unknown_values_break_a_crossing():
    signal = make("numeric", [(0, "5"), (10, None), (20, "9")], low=5.0, high=9.0, terciles=(6.0, 8.0))
    assert signal_events(signal, "rises_above", 8.0) == []


def test_alarm_event_happens_at_the_alarm_time_if_it_stayed_set():
    alarm = "2026-01-05T08:00:00+00:00"                                     # T0 ist 06:00 UTC
    signal = make("timestamp", [(0, alarm), (300, "2026-01-06T08:00:00+00:00")])
    assert [ts for ts, _ in signal_events(signal, "alarm_time", until=T0 + timedelta(hours=10))] == [T0 + timedelta(hours=2)]


def test_deleted_or_moved_alarms_do_not_count():
    deleted = make("timestamp", [(0, "2026-01-05T08:00:00+00:00"), (60, None)])
    assert signal_events(deleted, "alarm_time") == []
    moved = make("timestamp", [(0, "2026-01-05T08:00:00+00:00"), (60, "2026-01-05T09:00:00+00:00")])
    assert [ts for ts, _ in signal_events(moved, "alarm_time")] == [T0 + timedelta(hours=3)]


def test_future_alarms_and_alarms_after_until_are_ignored():
    signal = make("timestamp", [(0, "2026-01-05T08:00:00+00:00")])
    assert signal_events(signal, "alarm_time", until=T0 + timedelta(hours=1)) == []
    in_past = make("timestamp", [(0, "2026-01-05T05:00:00+00:00")])
    assert signal_events(in_past, "alarm_time") == []


def test_unknown_event_name_raises():
    with pytest.raises(ValueError):
        signal_events(make("categorical", [(0, "a"), (10, "b")]), "explodiert", "a")


def library(*signals):
    return SignalLibrary(list(signals))


def test_event_series_for_categorical_signals_include_duration_classes(acfg):
    params = replace(acfg.scan, min_events=3)
    points = []
    minutes = 0
    for index in range(4):                                                  # vier Abwesenheiten à 20 Minuten
        points += [(minutes, "home"), (minutes + 100, "away")]
        minutes += 120
    points.append((minutes, "home"))
    signal = make("categorical", points, values=("away", "home"))
    series = event_series(library(signal), params)
    keys = {(item.spec.value, item.spec.prev_class): len(item.times) for item in series}
    assert keys[("home", ALL)] == 4 and keys[("home", "<45")] == 4 and keys[("away", ALL)] == 4
    assert ("home", "≥180") not in keys                                      # unter min_events


def test_event_series_for_numeric_and_timestamp_signals(acfg):
    params = replace(acfg.scan, min_events=2)
    numbers = make("numeric", [(i * 120, str(5 + (i % 2) * 5)) for i in range(12)], low=5.0, high=10.0,
                   terciles=(5.0, 10.0))
    alarms = make("timestamp", [(i * 1440, f"2026-01-{5 + i:02d}T08:00:00+00:00") for i in range(5)])
    alarms.info = replace(alarms.info, handle="sig_02")
    specs = {(item.spec.signal, item.spec.event, item.spec.value) for item in event_series(library(numbers, alarms), params)}
    assert {spec for spec in specs if spec[0] == "sig_01"} == {("sig_01", "rises_above", "10"), ("sig_01", "falls_below", "10")}
    assert ("sig_02", "alarm_time", None) in specs


def test_event_text():
    assert event_text(EventSpec("s", "to_state", "home", ALL)) == "wechselt zu home"
    assert "vorher ≥180 min" in event_text(EventSpec("s", "to_state", "home", "≥180"))
    assert event_text(EventSpec("s", "rises_above", "18", ALL)) == "steigt über 18"
    assert event_text(EventSpec("s", "falls_below", "5", ALL)) == "fällt unter 5"
    assert event_text(EventSpec("s", "alarm_time", None, ALL)) == "Weckzeit erreicht"
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_events.py tests/test_presence.py -v`
Expected: FAIL mit Collection-Fehler (`ModuleNotFoundError` oder `ImportError` der neuen Module).

- [ ] **Step 3: Implementieren**

`ai/analysis/events.py`:

```python
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta

from collector.models import parse_utc

from .config import ScanParams
from .signals import Signal, SignalLibrary

THROTTLE = timedelta(minutes=60)       # Schwellenüberschreitungen höchstens einmal je Stunde
ALL = "alle"


def class_labels(edges: tuple[int, ...]) -> list[str]:
    labels = [f"<{edges[0]}"]
    labels += [f"{low}-{high}" for low, high in zip(edges, edges[1:])]
    labels.append(f"≥{edges[-1]}")
    return labels


def class_of(minutes: float, edges: tuple[int, ...]) -> str:
    return class_labels(edges)[bisect_right(edges, minutes)]


@dataclass(frozen=True)
class EventSpec:
    signal: str
    event: str                       # to_state | rises_above | falls_below | alarm_time
    value: str | None                # Zustand oder Schwelle als Text
    prev_class: str                  # "alle" oder Dauerklasse des vorherigen Zustands


@dataclass
class EventSeries:
    spec: EventSpec
    times: list[datetime]
    prev: list[float | None]         # Minuten im Zustand davor (nur to_state)


def _to_state(signal: Signal, value: str) -> list[tuple[datetime, float | None]]:
    found = []
    previous_state, started = None, None
    for index, (ts, state) in enumerate(signal.timeline.points()):
        if index == 0:
            previous_state, started = state, ts
            continue
        if state == previous_state:
            continue
        if state == value and previous_state is not None:
            found.append((ts, (ts - started).total_seconds() / 60))
        previous_state, started = state, ts
    return found


def _crossings(signal: Signal, threshold: float, rising: bool) -> list[tuple[datetime, float | None]]:
    found: list[tuple[datetime, float | None]] = []
    previous: float | None = None
    last: datetime | None = None
    for ts, state in signal.timeline.points():
        current = None if state is None else float(state)
        if current is None:
            previous = None
            continue
        if previous is not None:
            crossed = previous < threshold <= current if rising else previous >= threshold > current
            if crossed and (last is None or ts - last >= THROTTLE):
                found.append((ts, None))
                last = ts
        previous = current
    return found


def _alarms(signal: Signal, until: datetime | None) -> list[tuple[datetime, float | None]]:
    """Ein beobachteter Zeitstempel T zählt, wenn der Sensor eine Minute vor T noch auf T stand."""
    found, seen = [], set()
    for ts, state in signal.timeline.points():
        if state is None or state in seen:
            continue
        try:
            moment = parse_utc(state)
        except ValueError:
            continue
        if moment <= ts or (until is not None and moment > until):
            continue
        if signal.timeline.value_at(moment - timedelta(minutes=1)) == state:
            found.append((moment, None))
            seen.add(state)
    return found


def signal_events(signal: Signal, event: str, value=None,
                  until: datetime | None = None) -> list[tuple[datetime, float | None]]:
    """Zeitpunkte (und beim Zustandswechsel die Minuten im Zustand davor) eines Ereignisses."""
    if event == "to_state":
        found = _to_state(signal, str(value))
    elif event == "rises_above":
        found = _crossings(signal, float(value), True)
    elif event == "falls_below":
        found = _crossings(signal, float(value), False)
    elif event == "alarm_time":
        found = _alarms(signal, until)
    else:
        raise ValueError(f"unbekanntes Ereignis {event!r}")
    return sorted(found, key=lambda item: item[0])


def event_series(library: SignalLibrary, params: ScanParams, until: datetime | None = None) -> list[EventSeries]:
    """Alle Ereignisarten, die es in den Daten genügend oft gibt."""
    series: list[EventSeries] = []

    def add(signal: Signal, event: str, value: str | None, prev_class: str, items: list) -> None:
        if len(items) >= params.min_events:
            series.append(EventSeries(EventSpec(signal.handle, event, value, prev_class),
                                      [ts for ts, _ in items], [prev for _, prev in items]))

    for signal in library.usable():
        kind = signal.info.value_kind
        if kind == "categorical":
            for value in signal.info.values:
                items = signal_events(signal, "to_state", value)
                add(signal, "to_state", value, ALL, items)
                for label in class_labels(params.duration_edges):
                    add(signal, "to_state", value, label,
                        [(ts, prev) for ts, prev in items if class_of(prev, params.duration_edges) == label])
        elif kind == "numeric":
            for threshold in sorted(set(signal.info.terciles)):
                for event in ("rises_above", "falls_below"):
                    add(signal, event, f"{threshold:g}", ALL, signal_events(signal, event, threshold))
        elif kind == "timestamp":
            add(signal, "alarm_time", None, ALL, signal_events(signal, "alarm_time", until=until))
    return series


def event_text(spec: EventSpec) -> str:
    if spec.event == "to_state":
        text = f"wechselt zu {spec.value}"
        return text if spec.prev_class == ALL else f"{text} (vorher {spec.prev_class} min im anderen Zustand)"
    if spec.event == "rises_above":
        return f"steigt über {spec.value}"
    if spec.event == "falls_below":
        return f"fällt unter {spec.value}"
    return "Weckzeit erreicht"
```

Patch für `ai/analysis/presence.py`:

````diff
--- a/ai/analysis/presence.py
+++ b/ai/analysis/presence.py
@@ -10,6 +10,7 @@
 
 from .config import AnalysisConfig
 from .context import UNKNOWN
+from .events import class_labels
 from .interventions import load_interventions
 from .pseudonym import build_pseudonyms
 from .timeline import load_timeline
@@ -53,13 +54,6 @@
             if minutes <= limit:
                 return label
     return LATER
-
-
-def class_labels(edges: tuple[int, ...]) -> list[str]:
-    labels = [f"<{edges[0]}"]
-    labels += [f"{low}-{high}" for low, high in zip(edges, edges[1:])]
-    labels.append(f"≥{edges[-1]}")
-    return labels
 
 
 def _arrivals(transitions: list, first: datetime | None) -> list[tuple[datetime, float]]:
````

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_events.py tests/test_presence.py -v`
Expected: PASS (23 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (358 Tests insgesamt).

```bash
git add ai/analysis ai/tests
git commit -m "feat(ai): Ereignisse je Signal (Zustandswechsel, Schwellen, Weckzeit)"
```

---

### Task 4: Assoziationsscan

**Files:**
- Create: `ai/tests/scan_fixture.py`
- Create: `ai/tests/test_scan.py`
- Create: `ai/analysis/scan.py`

**Interfaces:**
- Consumes: `event_series`, `event_text`, `EventSpec`, `SignalLibrary`, `Association`, `Intervention`, `AnalysisConfig.scan`, `features.room_name`.
- Produces: `analysis.scan`: `fisher_greater(hits, events, control_hits, control_events) -> float`, `benjamini_hochberg(p_values) -> list[float]`, `run_scan(cfg, library, interventions, start, end) -> list[Association]` (nach q, lift, Treffern geordnet), `association_row(item, cfg) -> dict` (Zeile für das Modell). Testhilfen `tests/scan_fixture.py`: `build_scan_db(db, interventions, *, weeks=6, arrival="17:00", alarm_time=None)` und `after_arrival(offsets, arrival="17:00")`.

- [ ] **Step 1: Tests schreiben**

`ai/tests/scan_fixture.py`:

```python
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
```

`ai/tests/test_scan.py`:

```python
import random
from dataclasses import replace
from datetime import timedelta
from math import comb

import pytest

from analysis.features import data_period
from analysis.interventions import load_interventions
from analysis.pseudonym import build_pseudonyms
from analysis.scan import benjamini_hochberg, fisher_greater, run_scan
from analysis.signals import SignalLibrary
from tests.analysis_fixture import ROOM, local_utc
from tests.scan_fixture import after_arrival, build_scan_db


def exact_fisher(hits, events, control_hits, control_events):
    total, successes = events + control_events, hits + control_hits
    return sum(comb(successes, k) * comb(total - successes, events - k)
               for k in range(hits, min(events, successes) + 1)) / comb(total, events)


@pytest.mark.parametrize("args", [(5, 10, 0, 40), (8, 12, 3, 30), (1, 20, 1, 100), (4, 4, 0, 4)])
def test_fisher_matches_the_exact_hypergeometric_tail(args):
    assert fisher_greater(*args) == pytest.approx(exact_fisher(*args), rel=1e-9)


def test_fisher_without_hits_or_controls_is_one():
    assert fisher_greater(0, 10, 3, 30) == 1.0 and fisher_greater(3, 10, 0, 0) == 1.0


def test_benjamini_hochberg_example():
    assert benjamini_hochberg([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert benjamini_hochberg([]) == []


@pytest.fixture
def cfg(acfg):
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())


def scan(db, cfg, **overrides):
    cfg = replace(cfg, scan=replace(cfg.scan, **overrides)) if overrides else cfg
    pseudo = build_pseudonyms(db)
    library = SignalLibrary.from_db(db, cfg, pseudo)
    start, end = data_period(db)
    return run_scan(cfg, library, load_interventions(db, cfg), start, end), library


def test_the_hidden_habit_is_the_strongest_association(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, library = scan(db, cfg)
    top = found[0]
    assert library.get(top.signal).entity_id == "person.svenja"
    assert (top.event, top.value) == ("to_state", "home")
    assert (top.win_start, top.win_end) == (0, 30)
    assert top.hits == top.events == 30 and top.control_hits == 0 and top.q < 0.01 and top.lift > 10
    assert top.typical_action == "Sollwert 21 °C (30 von 30)"


def test_leaving_is_not_associated(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg)
    assert [item for item in found if item.value == "away"] == []


def test_results_exist_for_the_room_and_for_all_rooms(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg)
    assert {item.room for item in found if item.value == "home" and item.prev_class == "alle"} == {None, ROOM}


def test_small_rooms_are_only_covered_by_all_rooms(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg, min_room_interventions=500)
    assert {item.room for item in found} == {None}


def test_duration_classes_separate_long_from_short_absences(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg)
    classes = {item.prev_class for item in found if item.value == "home"}
    assert "≥180" in classes and "alle" in classes and "<45" not in classes


def test_adjacent_bins_are_merged_into_one_window(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [10] if n % 2 == 0 else [40]))
    found, _ = scan(db, cfg)
    merged = [item for item in found if item.value == "home" and item.prev_class == "alle" and item.room is None]
    assert [(item.win_start, item.win_end) for item in merged] == [(0, 60)]
    assert merged[0].hits == merged[0].events


def test_interventions_before_the_event_are_found_in_negative_bins(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [-45]))
    found, _ = scan(db, cfg)
    top = [item for item in found if item.value == "home" and item.room is None][0]
    assert (top.win_start, top.win_end) == (-60, -30)


def test_random_interventions_produce_no_significant_associations(db, cfg):
    generator = random.Random(11)

    def scattered(n, day):
        return [local_utc(day, f"{generator.randint(9, 22):02d}:{generator.randint(0, 59):02d}")
                for _ in range(generator.choice([1, 2]))]

    build_scan_db(db, scattered)
    found, _ = scan(db, cfg)
    assert found == []


def test_alarm_signal_is_found_without_special_handling(db, cfg):
    build_scan_db(db, lambda n, day: [local_utc(day, "06:30") + timedelta(minutes=10)], alarm_time="06:30")
    found, library = scan(db, cfg)
    alarm = [item for item in found if item.event == "alarm_time"]
    assert alarm and library.get(alarm[0].signal).entity_id == "sensor.next_alarm"
    assert (alarm[0].win_start, alarm[0].win_end) == (0, 30)
    assert alarm[0].hits >= alarm[0].events - 1          # am ersten Tag fehlt die Vorgeschichte für einen Eingriff


def test_scan_without_interventions_returns_nothing(db, cfg):
    build_scan_db(db, lambda n, day: [])
    found, _ = scan(db, cfg)
    assert found == []
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_scan.py -v`
Expected: FAIL mit Collection-Fehler (`ModuleNotFoundError` oder `ImportError` der neuen Module).

- [ ] **Step 3: Implementieren**

`ai/analysis/scan.py`:

```python
from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, lgamma
from zoneinfo import ZoneInfo

from .config import AnalysisConfig
from .events import EventSeries, EventSpec, event_series, event_text
from .features import room_name
from .models import Association, Intervention
from .signals import SignalLibrary

BIN_MINUTES = 30
SPAN_MINUTES = 120
BIN_STARTS = tuple(range(-SPAN_MINUTES, SPAN_MINUTES, BIN_MINUTES))     # zwei Stunden davor bis danach


def fisher_greater(hits: int, events: int, control_hits: int, control_events: int) -> float:
    """Einseitiger exakter Test: Wie wahrscheinlich sind mindestens so viele Treffer wie beobachtet,
    wenn Ereignisse und Kontrolle denselben Trefferanteil hätten (hypergeometrische Verteilung)?"""
    if hits <= 0 or events <= 0 or control_events <= 0:
        return 1.0
    total = events + control_events
    successes = hits + control_hits
    log_p = (lgamma(successes + 1) - lgamma(hits + 1) - lgamma(successes - hits + 1)
             + lgamma(total - successes + 1) - lgamma(events - hits + 1)
             - lgamma(total - successes - events + hits + 1)
             - (lgamma(total + 1) - lgamma(events + 1) - lgamma(total - events + 1)))
    pmf = exp(log_p)
    tail = pmf
    for k in range(hits, min(events, successes)):
        pmf *= ((successes - k) * (events - k)) / ((k + 1) * (total - successes - events + k + 1))
        tail += pmf
    return min(1.0, tail)


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Korrigierte Werte (q) für viele gleichzeitige Tests; steuert den Anteil falscher Funde."""
    count = len(p_values)
    order = sorted(range(count), key=lambda index: p_values[index])
    q_values = [1.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * count / rank)
        q_values[index] = running
    return q_values


def _hit_bins(times: list[datetime], instant: datetime) -> set[int]:
    low = bisect_left(times, instant - timedelta(minutes=SPAN_MINUTES))
    high = bisect_left(times, instant + timedelta(minutes=SPAN_MINUTES))
    bins = set()
    for ts in times[low:high]:
        minutes = (ts - instant).total_seconds() / 60
        bins.add(int(minutes // BIN_MINUTES) * BIN_MINUTES)
    return bins


def _first_in_window(times: list[datetime], instant: datetime, low: int, high: int) -> int | None:
    index = bisect_left(times, instant + timedelta(minutes=low))
    if index < len(times) and times[index] < instant + timedelta(minutes=high):
        return index
    return None


@dataclass
class _Test:
    series: int
    room: str | None
    start: int
    hits: int
    events: int
    control_hits: int
    control_events: int
    p: float
    q: float = 1.0


def _lift(hits: int, events: int, control_hits: int, control_events: int) -> float:
    control_rate = max(control_hits / control_events, 1 / (control_events + 1))
    return (hits / events) / control_rate


def _action_text(items: list[Intervention], hits: int) -> str:
    if not items:
        return ""
    counts = Counter((item.kind, round(float(item.value) * 2) / 2 if item.kind == "setpoint" else item.value)
                     for item in items)
    (kind, value), count = counts.most_common(1)[0]
    what = f"Sollwert {value:g} °C" if kind == "setpoint" else f"Preset {value}"
    return f"{what} ({count} von {hits})"


def run_scan(cfg: AnalysisConfig, library: SignalLibrary, interventions: list[Intervention],
             start: datetime, end: datetime) -> list[Association]:
    """Prüft jede Ereignisart gegen die Eingriffe je Raum; Ergebnis nach q und lift geordnet."""
    params = cfg.scan
    if not interventions:
        return []
    tz = ZoneInfo(cfg.timezone)
    ordered = sorted(interventions, key=lambda item: item.ts)
    room_items: dict[str | None, list[Intervention]] = {None: ordered}
    for room, count in Counter(item.room for item in ordered).items():
        if count >= params.min_room_interventions:
            room_items[room] = [item for item in ordered if item.room == room]
    room_times = {room: [item.ts for item in items] for room, items in room_items.items()}
    days = {item.ts.astimezone(tz).date() for item in ordered}
    active = {day + timedelta(days=delta) for day in days for delta in (-1, 0, 1)}

    def shifted(ts: datetime, days_: int) -> datetime:
        local = ts.astimezone(tz)
        return datetime.combine(local.date() + timedelta(days=days_), local.time(), tz).astimezone(timezone.utc)

    all_series: list[EventSeries] = event_series(library, params, until=end)
    kept: dict[int, tuple[list[datetime], list[datetime]]] = {}
    tests: list[_Test] = []
    for index, series in enumerate(all_series):
        events = [ts for ts in series.times if start <= ts < end and ts.astimezone(tz).date() in active]
        if len(events) < params.min_events:
            continue
        controls = []
        for ts in events:
            for delta in range(-params.control_days, params.control_days + 1):
                other = shifted(ts, delta)
                if delta == 0 or other.astimezone(tz).date() not in active:
                    continue
                low = bisect_left(events, other - timedelta(minutes=SPAN_MINUTES))
                if bisect_left(events, other + timedelta(minutes=SPAN_MINUTES)) > low:
                    continue
                controls.append(other)
        if not controls:
            continue
        kept[index] = (events, controls)
        for room, times in room_times.items():
            hits: Counter[int] = Counter()
            control_hits: Counter[int] = Counter()
            for ts in events:
                hits.update(_hit_bins(times, ts))
            for ts in controls:
                control_hits.update(_hit_bins(times, ts))
            for bin_start in BIN_STARTS:
                p = 1.0
                if hits[bin_start] / len(events) > control_hits[bin_start] / len(controls):
                    p = fisher_greater(hits[bin_start], len(events), control_hits[bin_start], len(controls))
                tests.append(_Test(index, room, bin_start, hits[bin_start], len(events),
                                   control_hits[bin_start], len(controls), p))
    for test, q in zip(tests, benjamini_hochberg([test.p for test in tests])):
        test.q = q

    passing: dict[tuple[int, str | None], list[_Test]] = {}
    for test in tests:
        if test.hits >= params.min_hits and test.q <= params.q_max and \
                _lift(test.hits, test.events, test.control_hits, test.control_events) > 1:
            passing.setdefault((test.series, test.room), []).append(test)

    found: list[Association] = []
    for (index, room), group in passing.items():
        group.sort(key=lambda test: test.start)
        runs: list[list[_Test]] = [[group[0]]]
        for test in group[1:]:
            if test.start == runs[-1][-1].start + BIN_MINUTES:
                runs[-1].append(test)
            else:
                runs.append([test])
        events, controls = kept[index]
        times, items = room_times[room], room_items[room]
        spec = all_series[index].spec
        for run in runs:
            low, high = run[0].start, run[-1].start + BIN_MINUTES
            hit_items = []
            for ts in events:
                position = _first_in_window(times, ts, low, high)
                if position is not None:
                    hit_items.append(items[position])
            control_hits = sum(_first_in_window(times, ts, low, high) is not None for ts in controls)
            best = min(run, key=lambda test: (test.q, test.p))
            found.append(Association(
                spec.signal, spec.event, spec.value, spec.prev_class, room, low, high, len(events),
                len(hit_items), len(controls), control_hits,
                _lift(len(hit_items), len(events), control_hits, len(controls)), best.p, best.q,
                _action_text(hit_items, len(hit_items))))
    found.sort(key=lambda item: (item.q, -item.lift, -item.hits))
    return found


def association_row(item: Association, cfg: AnalysisConfig) -> dict:
    """Eine Assoziation als Zeile für das Modell (nur Kennungen und Klartext der Räume)."""
    return {
        "signal": item.signal,
        "ereignis": event_text(EventSpec(item.signal, item.event, item.value, item.prev_class)),
        "raum": "alle Räume" if item.room is None else room_name(cfg, item.room),
        "fenster_min": [item.win_start, item.win_end],
        "ereignisse": item.events,
        "mit_eingriff": item.hits,
        "kontrolle_anteil": round(item.control_hits / item.control_events, 2),
        "lift": round(item.lift, 1),
        "q": float(f"{item.q:.2g}"),
        "typische_aktion": item.typical_action,
    }
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_scan.py -v`
Expected: PASS (16 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (374 Tests insgesamt).

```bash
git add ai/analysis ai/tests
git commit -m "feat(ai): Assoziationsscan mit Kontrolle, Fisher-Test und Benjamini-Hochberg"
```

---

### Task 5: Allgemeines Vokabular

**Files:**
- Create: `ai/tests/test_generic.py`
- Create: `ai/analysis/generic.py`
- Modify: `ai/analysis/vocabulary.py`

**Interfaces:**
- Consumes: `SignalInfo`, `ParseContext`, `parse_candidate`.
- Produces: `analysis.generic`: `generic_vocabulary_text()`, `parse_signal_trigger(raw, signals) -> dict`, `parse_signal_condition(raw, signals) -> dict`. `ParseContext` bekommt `signals: dict[str, SignalInfo]` (Standard leer) und `max_conditions: int | None` (Standard `None`). `parse_candidate` akzeptiert die Formen `signal`, `signal_state`, `signal_range`; Unbekanntes ergibt `nicht_pruefbar`.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_generic.py`:

```python
from dataclasses import replace

import pytest

from analysis.generic import generic_vocabulary_text, parse_signal_condition, parse_signal_trigger
from analysis.models import STATUS_OPEN, STATUS_UNTESTABLE
from analysis.pseudonym import Pseudonyms
from analysis.signals import SignalInfo
from analysis.vocabulary import ParseContext, parse_candidate

SIGNALS = {
    "sig_01": SignalInfo("sig_01", "categorical", "Anwesenheit person_1", values=("away", "home")),
    "sig_02": SignalInfo("sig_02", "numeric", "Sensor", low=-5.0, high=30.0, terciles=(5.0, 15.0)),
    "sig_03": SignalInfo("sig_03", "timestamp", "Weckzeit"),
}
CTX = ParseContext(rooms={"Wohnzimmer": "r_living"}, pseudo=Pseudonyms((), {}, {}), presets=frozenset({"comfort"}),
                   flat_states=frozenset(), signals=SIGNALS, max_conditions=2)
ACTION = {"type": "setpoint", "value": 21}


def raw(trigger, conditions=None, **extra):
    return {"room": "Wohnzimmer", "trigger": trigger, "conditions": conditions or [], "action": ACTION, **extra}


def test_valid_signal_triggers_are_normalized():
    assert parse_signal_trigger({"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180},
                                SIGNALS) == {"type": "signal", "signal": "sig_01", "event": "to_state",
                                             "value": "home", "min_prev_min": 180, "offset_min": 0}
    assert parse_signal_trigger({"signal": "sig_02", "event": "rises_above", "value": 12, "offset_min": 15},
                                SIGNALS) == {"type": "signal", "signal": "sig_02", "event": "rises_above",
                                             "value": 12.0, "offset_min": 15}
    assert parse_signal_trigger({"signal": "sig_03", "event": "alarm_time", "offset_min": -30},
                                SIGNALS)["offset_min"] == -30


@pytest.mark.parametrize("trigger", [
    {"signal": "sig_99", "event": "to_state", "value": "home"},                          # unbekanntes Signal
    {"signal": "sig_02", "event": "to_state", "value": "home"},                          # falsche Art
    {"signal": "sig_01", "event": "to_state", "value": "sleeping"},                      # unbekannter Zustand
    {"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 60, "max_prev_min": 30},
    {"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": -5},
    {"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": True},
    {"signal": "sig_02", "event": "rises_above", "value": 99},                           # außerhalb des Bereichs
    {"signal": "sig_02", "event": "rises_above", "value": "warm"},
    {"signal": "sig_01", "event": "to_state", "value": "home", "offset_min": -10},       # negativ nur bei alarm_time
    {"signal": "sig_01", "event": "to_state", "value": "home", "offset_min": 500},
    {"signal": "sig_03", "event": "alarm_time", "offset_min": -500},
    {"signal": "sig_01", "event": "explodiert"},
    {"event": "to_state", "value": "home"},
])
def test_invalid_signal_triggers_raise(trigger):
    with pytest.raises(ValueError):
        parse_signal_trigger(trigger, SIGNALS)


def test_signal_conditions():
    assert parse_signal_condition({"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"},
                                  SIGNALS) == {"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"}
    assert parse_signal_condition({"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8},
                                  SIGNALS) == {"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8.0}
    for bad in ({"type": "signal_state", "signal": "sig_02", "op": "is", "value": "x"},
                {"type": "signal_state", "signal": "sig_01", "op": "gleich", "value": "home"},
                {"type": "signal_range", "signal": "sig_01", "op": "below", "value": 8},
                {"type": "signal_range", "signal": "sig_02", "op": "darunter", "value": 8}):
        with pytest.raises(ValueError):
            parse_signal_condition(bad, SIGNALS)


def test_candidate_with_generic_forms_is_accepted():
    trigger = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180}
    conditions = [{"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8}]
    candidate = parse_candidate(raw(trigger, conditions, hypothesis="Heimkehr"), CTX)
    assert candidate.status == STATUS_OPEN and candidate.trigger["offset_min"] == 0
    assert candidate.conditions[0]["value"] == 8.0


def test_garbage_in_generic_candidates_is_untestable_not_an_error():
    for trigger in ({"type": "signal", "signal": "sig_99", "event": "to_state", "value": "home"},
                    {"type": "signal", "event": "to_state"},
                    {"type": "signal", "signal": "sig_02", "event": "rises_above", "value": 1e9}):
        candidate = parse_candidate(raw(trigger), CTX)
        assert candidate.status == STATUS_UNTESTABLE and candidate.note


def test_more_than_two_conditions_are_rejected_in_scan_mode_only():
    conditions = [{"type": "weekdays", "weekdays": [0]}] * 3
    trigger = {"type": "time", "weekdays": [0], "time": "17:30"}
    assert parse_candidate(raw(trigger, conditions), CTX).status == STATUS_UNTESTABLE
    assert parse_candidate(raw(trigger, conditions), replace(CTX, max_conditions=None)).status == STATUS_OPEN


def test_generic_forms_need_signals_in_the_context():
    trigger = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home"}
    assert parse_candidate(raw(trigger), replace(CTX, signals={})).status == STATUS_UNTESTABLE


def test_vocabulary_text_names_every_form():
    text = generic_vocabulary_text()
    for needle in ("to_state", "rises_above", "falls_below", "alarm_time", "signal_state", "signal_range",
                   "offset_min", "min_prev_min", "setpoint", "preset"):
        assert needle in text
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_generic.py tests/test_vocabulary.py -v`
Expected: FAIL mit Collection-Fehler (`ModuleNotFoundError` oder `ImportError` der neuen Module).

- [ ] **Step 3: Implementieren**

`ai/analysis/generic.py`:

```python
from __future__ import annotations

from typing import Any

from .signals import SignalInfo

OFFSET_LIMIT = 120                   # Minuten
PREV_LIMIT = 7 * 24 * 60             # Minuten, eine Woche


def generic_vocabulary_text() -> str:
    return """Erlaubte Bausteine. Verwende ausschließlich diese, nichts anderes. Signale (sig_NN) stehen im Signalkatalog der Nachricht.

Auslöser ("trigger"), genau einer:
- {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"}
    Wochentage (0 = Montag bis 6 = Sonntag) und Uhrzeit in lokaler Zeit.
- {"type": "signal", "signal": "sig_03", "event": "to_state", "value": "home", "min_prev_min": 180, "max_prev_min": null, "offset_min": 0}
    Ein Signal (Art Kategorie) wechselt in den Zustand value. Optional: der Zustand davor galt mindestens
    min_prev_min und höchstens (ausschließlich) max_prev_min Minuten. offset_min verschiebt den Zeitpunkt
    (0 bis 120 Minuten nach dem Ereignis).
- {"type": "signal", "signal": "sig_07", "event": "rises_above", "value": 18.5, "offset_min": 0}
- {"type": "signal", "signal": "sig_07", "event": "falls_below", "value": 12, "offset_min": 0}
    Ein Signal (Art Zahl) steigt über beziehungsweise fällt unter die Schwelle.
- {"type": "signal", "signal": "sig_09", "event": "alarm_time", "offset_min": -30}
    Die Weckzeit eines Signals (Art Zeitstempel) ist erreicht; offset_min darf hier auch negativ sein (bis -120),
    weil die Weckzeit im Voraus bekannt ist.

Bedingungen ("conditions", optional, höchstens zwei):
- {"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]}
- {"type": "time_window", "from": "16:00", "to": "20:00"}   (from muss vor to liegen)
- {"type": "signal_state", "signal": "sig_03", "op": "is", "value": "home"}   ("is" oder "is_not", Art Kategorie)
- {"type": "signal_range", "signal": "sig_07", "op": "below", "value": 8}   ("below" oder "above", Art Zahl)

Aktion ("action"), genau eine:
- {"type": "preset", "value": "<Preset aus den Daten>"}
- {"type": "setpoint", "value": 21.0}   (Sollwert in Grad Celsius, 5 bis 30)"""


def _int(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} {value!r} ist keine ganze Zahl")
    if not low <= value <= high:
        raise ValueError(f"{what} {value} liegt außerhalb von {low} bis {high}")
    return value


def _threshold(value: Any, info: SignalInfo) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Schwelle {value!r} ist keine Zahl")
    if not info.low <= value <= info.high:
        raise ValueError(f"Schwelle {value} liegt außerhalb des beobachteten Bereichs {info.low:g} bis {info.high:g}")
    return float(value)


def _signal(raw: dict, signals: dict[str, SignalInfo], kind: str) -> SignalInfo:
    handle = raw.get("signal")
    info = signals.get(handle) if isinstance(handle, str) else None
    if info is None:
        raise ValueError(f"unbekanntes Signal {handle!r}")
    if info.value_kind != kind:
        raise ValueError(f"Signal {handle} hat die Art {info.value_kind}, hier ist {kind} nötig")
    return info


def _category(raw: dict, info: SignalInfo) -> str:
    value = raw.get("value")
    if not isinstance(value, str) or value not in info.values:
        raise ValueError(f"Zustand {value!r} ist für {info.handle} unbekannt (bekannt: {', '.join(info.values)})")
    return value


def parse_signal_trigger(raw: dict, signals: dict[str, SignalInfo]) -> dict:
    event = raw.get("event")
    if event == "to_state":
        info = _signal(raw, signals, "categorical")
        trigger: dict = {"type": "signal", "signal": info.handle, "event": event, "value": _category(raw, info)}
        low = raw.get("min_prev_min")
        high = raw.get("max_prev_min")
        if low is not None:
            trigger["min_prev_min"] = _int(low, 0, PREV_LIMIT, "min_prev_min")
        if high is not None:
            trigger["max_prev_min"] = _int(high, 1, PREV_LIMIT, "max_prev_min")
        if low is not None and high is not None and low >= high:
            raise ValueError("min_prev_min muss kleiner als max_prev_min sein")
    elif event in ("rises_above", "falls_below"):
        info = _signal(raw, signals, "numeric")
        trigger = {"type": "signal", "signal": info.handle, "event": event, "value": _threshold(raw.get("value"), info)}
    elif event == "alarm_time":
        info = _signal(raw, signals, "timestamp")
        trigger = {"type": "signal", "signal": info.handle, "event": event}
    else:
        raise ValueError(f"unbekanntes Signal-Ereignis {event!r}")
    offset = _int(raw.get("offset_min", 0), -OFFSET_LIMIT, OFFSET_LIMIT, "offset_min")
    if offset < 0 and event != "alarm_time":
        raise ValueError("Ein negativer Versatz ist nur bei alarm_time möglich, nur dort ist das Ereignis im Voraus bekannt")
    trigger["offset_min"] = offset
    return trigger


def parse_signal_condition(raw: dict, signals: dict[str, SignalInfo]) -> dict:
    kind = raw.get("type")
    if kind == "signal_state":
        info = _signal(raw, signals, "categorical")
        if raw.get("op") not in ("is", "is_not"):
            raise ValueError(f"signal_state: op {raw.get('op')!r} ist ungültig")
        return {"type": kind, "signal": info.handle, "op": raw["op"], "value": _category(raw, info)}
    info = _signal(raw, signals, "numeric")
    if raw.get("op") not in ("below", "above"):
        raise ValueError(f"signal_range: op {raw.get('op')!r} ist ungültig")
    return {"type": "signal_range", "signal": info.handle, "op": raw["op"], "value": _threshold(raw.get("value"), info)}
```

Patch für `ai/analysis/vocabulary.py`:

````diff
--- a/ai/analysis/vocabulary.py
+++ b/ai/analysis/vocabulary.py
@@ -1,11 +1,13 @@
 from __future__ import annotations
 
 import re
-from dataclasses import dataclass
+from dataclasses import dataclass, field
 from typing import Any
 
+from .generic import parse_signal_condition, parse_signal_trigger
 from .models import STATUS_OPEN, STATUS_UNTESTABLE, Candidate
 from .pseudonym import Pseudonyms
+from .signals import SignalInfo
 
 TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
 PERSON_EVENTS = ("person_arrives", "person_leaves")
@@ -21,6 +23,8 @@
     flat_states: frozenset[str]
     min_setpoint: float = 5.0
     max_setpoint: float = 30.0
+    signals: dict[str, SignalInfo] = field(default_factory=dict)   # nur im Modus scan: allgemeine Signale
+    max_conditions: int | None = None        # nur im Modus scan: höchstens zwei
 
 
 def vocabulary_text() -> str:
@@ -108,6 +112,8 @@
         if event in PLAIN_EVENTS:
             return {"type": "event", "event": event}
         raise ValueError(f"unbekanntes Ereignis {event!r}")
+    if kind == "signal":
+        return parse_signal_trigger(raw, ctx.signals)
     raise ValueError(f"unbekannter Auslöser {kind!r}")
 
 
@@ -129,6 +135,8 @@
         if not isinstance(raw.get("home"), bool):
             raise ValueError("person_home: home muss true oder false sein")
         return {"type": "person_home", "person": _person(raw.get("person"), ctx), "home": raw["home"]}
+    if kind in ("signal_state", "signal_range"):
+        return parse_signal_condition(raw, ctx.signals)
     raise ValueError(f"unbekannte Bedingung {kind!r}")
 
 
@@ -157,6 +165,8 @@
         conditions_raw = raw.get("conditions") or []
         if not isinstance(conditions_raw, list):
             raise ValueError("conditions muss eine Liste sein")
+        if ctx.max_conditions is not None and len(conditions_raw) > ctx.max_conditions:
+            raise ValueError(f"höchstens {ctx.max_conditions} Bedingungen erlaubt")
         return Candidate(
             room=ctx.rooms[name],
             trigger=_trigger(raw.get("trigger"), ctx),
````

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_generic.py tests/test_vocabulary.py -v`
Expected: PASS (62 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (394 Tests insgesamt).

```bash
git add ai/analysis ai/tests
git commit -m "feat(ai): allgemeines Vokabular für Signal-Auslöser und -Bedingungen"
```

---

### Task 6: Backtest für Signal-Auslöser und -Bedingungen

**Files:**
- Modify: `ai/tests/test_backtest.py`
- Modify: `ai/analysis/backtest.py`
- Modify: `ai/analysis/context.py`

**Interfaces:**
- Consumes: `signal_events`, `SignalLibrary`/`Signal`, bestehender `Backtester`.
- Produces: `ContextProvider.signals` (Klassenattribut `None`; der Lauf setzt die `SignalLibrary`). `Backtester` berechnet Auslöser `{"type": "signal", ...}` (Ereigniszeitpunkte plus `offset_min`, gefiltert nach `min_prev_min` einschließlich und `max_prev_min` ausschließlich) und die Bedingungen `signal_state` und `signal_range`. Fehlende oder leere Signale und Signale ohne Ereignisse ergeben `nicht_pruefbar`.

- [ ] **Step 1: Tests schreiben**

Patch für `ai/tests/test_backtest.py`:

````diff
--- a/ai/tests/test_backtest.py
+++ b/ai/tests/test_backtest.py
@@ -338,3 +338,96 @@
     assert result.metrics["ausloesungen"] == 12.0          # 10 im ersten Block, 2 um den Einzeleingriff
     unfiltered = backtester(no_gap_filter(acfg), block).evaluate(rule(time_rule()))
     assert unfiltered.metrics["ausloesungen"] == 30.0
+
+
+# --- Allgemeine Signal-Auslöser und -Bedingungen ------------------------------------------
+
+def signal(handle, value_kind, points, **info):
+    from analysis.signals import Signal, SignalInfo
+    return Signal(SignalInfo(handle, value_kind, "Test", **info), f"x.{handle}", "env", None, Timeline(points))
+
+
+def presence_signal():
+    points = []
+    for n in range(42):
+        points.append((local_utc(day(n), "07:00"), "away"))
+        if day(n).weekday() < 5:
+            points += [(local_utc(day(n), "17:00"), "home"), (local_utc(day(n), "22:30"), "away")]
+    return signal("sig_01", "categorical", points, values=("away", "home"))
+
+
+def signal_ctx(*signals):
+    from analysis.signals import SignalLibrary
+    ctx = ContextProvider.empty()
+    ctx.signals = SignalLibrary(list(signals))
+    return ctx
+
+
+def arrival_trigger(**extra):
+    return {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home", "offset_min": 0, **extra}
+
+
+def test_signal_trigger_matches_interventions_after_the_event(acfg):
+    result = backtester(acfg, weekday_interventions("17:12"), signal_ctx(presence_signal())).evaluate(rule(arrival_trigger()))
+    assert result.status == STATUS_PASSED
+    assert result.metrics["ausloesungen"] == 30.0 and result.metrics["faelle"] == 30.0
+    assert result.metrics["abdeckung"] == 1.0 and result.metrics["fehlausloesungen"] == 0.0
+
+
+def test_duration_of_the_previous_state_filters_events(acfg):
+    ctx = signal_ctx(presence_signal())
+    long_ones = backtester(acfg, weekday_interventions("17:12"), ctx).evaluate(rule(arrival_trigger(min_prev_min=180)))
+    assert long_ones.metrics["ausloesungen"] == 30.0
+    short_ones = backtester(acfg, weekday_interventions("17:12"), ctx).evaluate(rule(arrival_trigger(max_prev_min=60)))
+    assert short_ones.metrics["ausloesungen"] == 0.0 and short_ones.status == STATUS_FEW
+
+
+def test_positive_offset_moves_the_trigger_instant(acfg):
+    result = backtester(acfg, weekday_interventions("17:12"), signal_ctx(presence_signal())).evaluate(
+        rule(arrival_trigger(offset_min=15)))
+    assert result.metrics["faelle"] == 0.0                         # der Eingriff liegt vor 17:15
+
+
+def test_alarm_time_allows_a_negative_offset(acfg):
+    alarms = [(local_utc(day(n) - timedelta(days=1), "20:00"), local_utc(day(n), "06:30").isoformat()) for n in range(42)]
+    ctx = signal_ctx(signal("sig_03", "timestamp", alarms))
+    items = [iv(n, "06:02", 21.0) for n in range(42)]        # 28 Minuten vor der Weckzeit
+    trigger = {"type": "signal", "signal": "sig_03", "event": "alarm_time", "offset_min": -30}
+    result = backtester(acfg, items, ctx, start=local_utc(day(1), "00:00")).evaluate(rule(trigger))
+    assert result.status == STATUS_PASSED and result.metrics["faelle"] == result.metrics["ausloesungen"] >= 30
+
+
+def test_unknown_or_empty_signals_make_the_candidate_untestable(acfg):
+    assert backtester(acfg, weekday_interventions()).evaluate(rule(arrival_trigger())).status == STATUS_UNTESTABLE
+    ctx = signal_ctx(presence_signal())
+    unknown = backtester(acfg, weekday_interventions(), ctx).evaluate(rule(arrival_trigger(signal="sig_99")))
+    assert unknown.status == STATUS_UNTESTABLE and unknown.note
+    empty = signal_ctx(signal("sig_01", "categorical", [], values=("away", "home")))
+    assert backtester(acfg, weekday_interventions(), empty).evaluate(rule(arrival_trigger())).status == STATUS_UNTESTABLE
+
+
+def test_signal_without_events_is_untestable(acfg):
+    flat = signal("sig_01", "categorical", [(local_utc(day(0), "07:00"), "away"), (local_utc(day(1), "07:00"), "away")],
+                  values=("away", "home"))
+    result = backtester(acfg, weekday_interventions(), signal_ctx(flat)).evaluate(rule(arrival_trigger()))
+    assert result.status == STATUS_UNTESTABLE and "Ereignisse" in result.note
+
+
+def test_signal_state_condition(acfg):
+    ctx = signal_ctx(presence_signal())
+    home = {"type": "signal_state", "signal": "sig_01", "op": "is", "value": "home"}
+    away = {**home, "op": "is_not"}
+    base = time_rule("17:30")
+    assert backtester(acfg, weekday_interventions(), ctx).evaluate(rule(base, conditions=[home])).metrics["ausloesungen"] == 30.0
+    assert backtester(acfg, weekday_interventions(), ctx).evaluate(rule(base, conditions=[away])).metrics["ausloesungen"] == 0.0
+
+
+def test_signal_range_condition_and_missing_signal(acfg):
+    points = [(local_utc(day(0), "00:00"), "5.0"), (local_utc(day(21), "00:00"), "12.0")]
+    ctx = signal_ctx(signal("sig_02", "numeric", points, low=5.0, high=12.0, terciles=(5.0, 12.0)))
+    below = {"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8}
+    result = backtester(acfg, weekday_interventions(), ctx).evaluate(rule(time_rule(weekdays=range(7)), conditions=[below]))
+    assert result.metrics["ausloesungen"] == 21.0
+    missing = {**below, "signal": "sig_77"}
+    assert backtester(acfg, weekday_interventions(), ctx).evaluate(
+        rule(time_rule(), conditions=[missing])).status == STATUS_UNTESTABLE
````

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_backtest.py -v`
Expected: FAIL (7 der neuen Tests scheitern, die übrigen Tests der Dateien bestehen). Die neuen Tests verwenden Signal-Auslöser, die der Backtester noch nicht kennt (`nicht_pruefbar` statt Ergebnis).

- [ ] **Step 3: Implementieren**

Patch für `ai/analysis/backtest.py`:

````diff
--- a/ai/analysis/backtest.py
+++ b/ai/analysis/backtest.py
@@ -8,6 +8,7 @@
 
 from .config import AnalysisConfig
 from .context import UNKNOWN, ContextProvider
+from .events import signal_events
 from .interventions import excluded
 from .models import (STATUS_FEW, STATUS_PASSED, STATUS_REJECTED, STATUS_UNTESTABLE, Candidate,
                      Intervention)
@@ -113,7 +114,30 @@
         trigger = candidate.trigger
         if trigger["type"] == "time":
             return self._time_instants(trigger)
+        if trigger["type"] == "signal":
+            return self._signal_instants(trigger)
         return self._event_instants(trigger, candidate.room)
+
+    def _signal(self, handle: str):
+        library = getattr(self.ctx, "signals", None)
+        signal = library.get(handle) if library is not None else None
+        if signal is None or not signal.timeline:
+            raise NotTestable("Signal ohne aufgezeichnete Daten")
+        return signal
+
+    def _signal_instants(self, trigger: dict) -> list[datetime]:
+        signal = self._signal(trigger["signal"])
+        events = signal_events(signal, trigger["event"], trigger.get("value"), until=self.end)
+        if not events:
+            raise NotTestable("keine Ereignisse für das Signal")
+        low, high = trigger.get("min_prev_min"), trigger.get("max_prev_min")
+        offset = timedelta(minutes=trigger.get("offset_min", 0))
+        result = []
+        for ts, previous in events:
+            if previous is not None and ((low is not None and previous < low) or (high is not None and previous >= high)):
+                continue
+            result.append(ts + offset)
+        return result
 
     def _time_instants(self, trigger: dict) -> list[datetime]:
         hour, minute = (int(part) for part in trigger["time"].split(":"))
@@ -170,6 +194,8 @@
 
     def _check_condition_data(self, candidate: Candidate) -> None:
         for condition in candidate.conditions:
+            if condition.get("type") in ("signal_state", "signal_range"):
+                self._signal(condition["signal"])
             if condition.get("type") == "outside_temp" and not self.ctx.outside_temp:
                 raise NotTestable("kein Außensensor aufgezeichnet")
             if condition.get("type") == "person_home" and not self.ctx.person_timeline(condition["person"]):
@@ -196,6 +222,18 @@
             elif kind == "person_home":
                 state = self.ctx.person_timeline(condition["person"]).value_at(instant)
                 if state in UNKNOWN or (state == "home") != condition["home"]:
+                    return False
+            elif kind == "signal_state":
+                state = self._signal(condition["signal"]).timeline.value_at(instant)
+                if state is None or (state == condition["value"]) != (condition["op"] == "is"):
+                    return False
+            elif kind == "signal_range":
+                number = self._signal(condition["signal"]).timeline.numeric_at(instant)
+                if number is None:
+                    return False
+                if condition["op"] == "below" and not number < condition["value"]:
+                    return False
+                if condition["op"] == "above" and not number > condition["value"]:
                     return False
         return True
 
````

Patch für `ai/analysis/context.py`:

````diff
--- a/ai/analysis/context.py
+++ b/ai/analysis/context.py
@@ -46,6 +46,8 @@
 
     Fehlende Entitäten sind kein Fehler: Die zugehörigen Werte sind `None`.
     """
+
+    signals = None                       # SignalLibrary, vom Lauf gesetzt (Modus scan, allgemeine Kandidaten)
 
     def __init__(self, pseudo: Pseudonyms, *, persons: dict[str, Timeline] | None = None,
                  flat_state: Timeline | None = None, in_bed: Timeline | None = None,
````

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_backtest.py -v`
Expected: PASS (42 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (402 Tests insgesamt).

```bash
git add ai/analysis ai/tests
git commit -m "feat(ai): Backtest rechnet allgemeine Signal-Auslöser mit Versatz und Dauerfilter"
```

---

### Task 7: Modus scan: Daten, Prompt, Lauf und Darstellung

**Files:**
- Create: `ai/tests/test_scan_mode.py`
- Modify: `ai/analysis/prompts.py`
- Modify: `ai/analysis/runner.py`
- Modify: `ai/analysis/candidates.py`

**Interfaces:**
- Consumes: `run_scan`, `association_row`, `generic_vocabulary_text`, `SignalLibrary`, `ParseContext.signals`, bestehende `prepare`/`run_analysis`/`describe`/`signature`.
- Produces: `MODES = ("weekly", "clusters", "scan")`; `Environment.signals` (die `SignalLibrary`, auch an `ctx.signals` gehängt, damit `backtest_candidate` sie nutzt); `Prepared.associations`; Modus `scan` in `prepare` (Daten: `zeitraum`, `signale`, `assoziationen` (höchstens `scan.top_k`), `wochenraster`) mit dem allgemeinen Vokabular und `max_conditions=2`; `run_analysis` speichert die Assoziationen im selben Lauf (Rollback bei Fehler); `describe(candidate, cfg, pseudo, signals=None)` und `signature` kennen Signal-Auslöser.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_scan_mode.py`:

```python
import json
from dataclasses import replace

import pytest

from analysis.candidates import describe, signature
from analysis.llm import InvalidResponse
from analysis.models import STATUS_OPEN, Candidate
from analysis.prompts import build_messages
from analysis.pseudonym import Pseudonyms
from analysis.runner import NothingToAnalyze, backtest_candidate, prepare, run_analysis
from analysis.store import Store
from tests.analysis_fixture import ROOM, local_utc
from tests.scan_fixture import after_arrival, build_scan_db

ARRIVAL = {"room": "Wohnzimmer", "hypothesis": "Heimkehr von der Arbeit",
           "trigger": {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home",
                       "min_prev_min": 180, "offset_min": 0},
           "action": {"type": "setpoint", "value": 21}}
LEGACY = {"room": "Wohnzimmer", "trigger": {"type": "event", "event": "person_arrives", "person": "person_1"},
          "action": {"type": "setpoint", "value": 21}}


def answer(*items, **extra):
    return {"content": json.dumps({"candidates": list(items)}, ensure_ascii=False), **extra}


@pytest.fixture
def cfg(db, acfg, fake_llm):
    build_scan_db(db, after_arrival(lambda n: [12]))
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(), openrouter_url=fake_llm.url,
                   retry_backoff_s=0.0)


async def run(db, cfg, mode="scan", **kwargs):
    return await run_analysis(db, cfg, mode, api_key="geheim", **kwargs)


# --- Vorbereitung und Datenschutz -------------------------------------------------------------

def test_scan_data_has_catalog_associations_and_weekly_grid(db, cfg):
    prepared = prepare(db, cfg, "scan")
    assert set(prepared.data) == {"zeitraum", "signale", "assoziationen", "wochenraster"}
    assert prepared.data["signale"] == [{"id": "sig_01", "beschreibung": "Anwesenheit person_1 (home oder away)",
                                         "art": "Kategorie", "werte": ["away", "home"]}]
    top = prepared.data["assoziationen"][0]
    assert top["signal"] == "sig_01" and top["fenster_min"] == [0, 30] and top["mit_eingriff"] == top["ereignisse"]
    assert top["raum"] in ("alle Räume", "Wohnzimmer") and top["typische_aktion"].startswith("Sollwert 21")
    assert prepared.associations and "raeume" in prepared.data["wochenraster"]


def test_scan_prompt_uses_the_general_vocabulary_and_leaks_nothing(db, cfg):
    prepared = prepare(db, cfg, "scan")
    text = "\n".join(message["content"] for message in prepared.messages)
    assert "Modus: Scan" in text and "signal_state" in text and "alarm_time" in text
    assert "person_event" not in text and '"person_arrives"' not in text           # kein altes Vokabular
    for forbidden in ("svenja", "person.svenja", "u-svenja"):
        assert forbidden not in text.lower()


def test_names_in_signal_labels_abort_the_run_before_sending(db, cfg):
    from analysis.runner import PrivacyLeak
    bad = replace(cfg, signal_labels={"person.svenja": "Handy von Svenja"})
    with pytest.raises(PrivacyLeak):
        prepare(db, bad, "scan")


def test_parse_context_for_scan_knows_signals_and_limits_conditions(db, cfg):
    ctx = prepare(db, cfg, "scan").parse_ctx
    assert set(ctx.signals) == {"sig_01"} and ctx.max_conditions == 2
    assert prepare(db, cfg, "weekly").parse_ctx.signals == {} and prepare(db, cfg, "weekly").parse_ctx.max_conditions is None


def test_scan_without_findings_stops_with_a_hint(db, cfg):
    strict = replace(cfg, scan=replace(cfg.scan, min_hits=10_000))
    with pytest.raises(NothingToAnalyze, match="scan"):
        prepare(db, strict, "scan")


# --- Lauf --------------------------------------------------------------------------------

async def test_scan_run_backtests_generic_candidates_and_stores_associations(db, cfg, fake_llm):
    fake_llm.responses = [answer(ARRIVAL, ARRIVAL | {"trigger": {**ARRIVAL["trigger"], "signal": "sig_77"}})]
    summary = await run(db, cfg)
    assert summary.by_status == {"bestanden": 1, "nicht_pruefbar": 1}
    store = Store(db)
    passed = [c for c in store.list_candidates(run_id=summary.run_id) if c.status == "bestanden"][0]
    assert passed.trigger["min_prev_min"] == 180 and store.results(passed.id)["faelle"] == 30.0
    assert store.list_associations(summary.run_id)


async def test_old_modes_do_not_accept_generic_candidates(db, cfg, fake_llm):
    fake_llm.responses = [answer(ARRIVAL)]
    summary = await run(db, cfg, mode="weekly")
    assert summary.by_status == {"nicht_pruefbar": 1}


async def test_failed_scan_run_stores_no_associations(db, cfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": "immer noch nicht"}]
    with pytest.raises(InvalidResponse):
        await run(db, cfg)
    store = Store(db)
    (row,) = store.list_runs()
    assert row["status"] == "fehlgeschlagen" and store.list_associations(row["id"]) == []


async def test_stored_generic_candidates_can_be_backtested_again(db, cfg, fake_llm):
    fake_llm.responses = [answer(ARRIVAL)]
    summary = await run(db, cfg)
    (candidate,) = Store(db).list_candidates(run_id=summary.run_id)
    assert backtest_candidate(db, cfg, candidate.id).status == "bestanden"


# --- Darstellung --------------------------------------------------------------------------

SIGNALS = {"sig_01": "Anwesenheit person_1 (home oder away)", "sig_02": "Sensor", "sig_03": "Weckzeit"}


def candidate(trigger, conditions=()):
    return Candidate(room=ROOM, trigger=trigger, conditions=list(conditions),
                     action={"type": "setpoint", "value": 21.0}, status=STATUS_OPEN)


def test_describe_generic_triggers_and_conditions(acfg):
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"})
    pseudo = Pseudonyms((), {}, {})
    trigger = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home",
               "min_prev_min": 180, "offset_min": 10}
    conditions = [{"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8.0},
                  {"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"}]
    room, when, action = describe(candidate(trigger, conditions), cfg, pseudo, SIGNALS)
    assert room == "Wohnzimmer" and action == "21 °C"
    assert when == ("Anwesenheit person_1 (home oder away) wechselt zu home (vorher 180–∞ min im anderen Zustand), "
                    "+10 min wenn Sensor unter 8; Anwesenheit person_1 (home oder away) ist nicht away")
    alarm = {"type": "signal", "signal": "sig_03", "event": "alarm_time", "offset_min": -30}
    assert describe(candidate(alarm), cfg, pseudo, SIGNALS)[1] == "Weckzeit: Weckzeit erreicht, -30 min"
    assert describe(candidate(alarm), cfg, pseudo)[1].startswith("sig_03")            # ohne Katalog: die Kennung


def test_signature_groups_similar_signal_triggers():
    base = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180}
    assert signature(candidate({**base, "offset_min": 5})) == signature(candidate({**base, "offset_min": 20}))
    assert signature(candidate({**base, "offset_min": 5})) != signature(candidate({**base, "offset_min": 60}))
    assert signature(candidate({**base, "value": "away"})) != signature(candidate(base))


def test_scan_intro_is_part_of_the_messages():
    messages = build_messages("scan", {"a": 1}, "V", 20, presets=[], flat_states=[], rooms=["Wohnzimmer"])
    assert messages[1]["content"].startswith("Modus: Scan")
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_scan_mode.py -v`
Expected: FAIL (11 der neuen Tests scheitern, die übrigen Tests der Dateien bestehen).

- [ ] **Step 3: Implementieren**

Patch für `ai/analysis/prompts.py`:

````diff
--- a/ai/analysis/prompts.py
+++ b/ai/analysis/prompts.py
@@ -27,6 +27,16 @@
         "wurde, welche Aktion am häufigsten war und welche Personen dabei zu Hause waren; dazu je Wochentag "
         "den durchschnittlichen Wohnungszustand und die Anwesenheit. Suche selbst nach Mustern."
     ),
+    "scan": (
+        "Modus: Scan. Die Daten enthalten den Signalkatalog (sig_NN mit Beschreibung), die Assoziationen und das "
+        "Wochenraster. Eine Assoziation sagt: Um dieses Ereignis herum (fenster_min in Minuten, negativ = davor) "
+        "wurde in dem Raum bei mit_eingriff von ereignisse Ereignissen manuell eingegriffen, an Kontrolltagen zur "
+        "selben Uhrzeit nur bei kontrolle_anteil; lift ist das Verhältnis, q die korrigierte Irrtumswahrscheinlichkeit. "
+        "Formuliere daraus Regeln: Auslöser ist das Ereignis (mit offset_min und gegebenenfalls min_prev_min), "
+        "Aktion ist die typische Aktion. Belege jede Regel mit mindestens einer Assoziation oder dem Raster; "
+        "reine Zeitgewohnheiten aus dem Raster sind als time-Auslöser zulässig. Nutze Bedingungen nur, wenn sie "
+        "die Auslösung erkennbar eingrenzen. Nenne in der Hypothese die vermutete Ursache."
+    ),
     "clusters": (
         "Modus: Cluster. Die Daten zeigen gebündelte Eingriffe (gleicher Raum, gleiche Aktion, ähnliche "
         "Uhrzeit, gleiche Wochentagsgruppe) mit dem Kontext zum Zeitpunkt der Eingriffe als Anteile. "
````

Patch für `ai/analysis/runner.py`:

````diff
--- a/ai/analysis/runner.py
+++ b/ai/analysis/runner.py
@@ -2,7 +2,7 @@
 
 import hashlib
 from collections import Counter
-from dataclasses import dataclass
+from dataclasses import dataclass, field
 from datetime import datetime
 
 from collector.db import Database
@@ -11,16 +11,19 @@
 from .backtest import Backtester, BacktestResult
 from .config import AnalysisConfig
 from .context import ContextProvider
+from .generic import generic_vocabulary_text
 from .features import build_clusters, build_weekly, data_period, known_presets, room_name
 from .interventions import load_interventions
 from .llm import request_candidates
-from .models import STATUS_OPEN, STATUS_UNTESTABLE, Intervention
+from .models import STATUS_OPEN, STATUS_UNTESTABLE, Association, Intervention
 from .prompts import build_messages
 from .pseudonym import Pseudonyms, build_pseudonyms
+from .scan import association_row, run_scan
+from .signals import SignalLibrary
 from .store import Store
 from .vocabulary import ParseContext, parse_candidates, vocabulary_text
 
-MODES = ("weekly", "clusters")
+MODES = ("weekly", "clusters", "scan")
 
 
 class AnalysisError(Exception):
@@ -47,6 +50,7 @@
     pseudo: Pseudonyms
     ctx: ContextProvider
     interventions: list[Intervention]
+    signals: SignalLibrary | None = None
 
 
 @dataclass
@@ -56,6 +60,7 @@
     data: dict
     messages: list[dict]
     parse_ctx: ParseContext
+    associations: list[Association] = field(default_factory=list)
 
 
 @dataclass
@@ -76,8 +81,10 @@
         raise NothingToAnalyze("Die Datenbank enthält keine Zustandsänderungen.")
     start, end = start or period[0], end or period[1]
     pseudo = build_pseudonyms(db)
-    return Environment(start, end, pseudo, ContextProvider.from_db(db, cfg, pseudo),
-                       load_interventions(db, cfg, start=start, end=end))
+    ctx = ContextProvider.from_db(db, cfg, pseudo)
+    library = SignalLibrary.from_db(db, cfg, pseudo)
+    ctx.signals = library
+    return Environment(start, end, pseudo, ctx, load_interventions(db, cfg, start=start, end=end), library)
 
 
 def prepare(db: Database, cfg: AnalysisConfig, mode: str, *, start: datetime | None = None,
@@ -87,8 +94,18 @@
     env = load_environment(db, cfg, start, end)
     if len(env.interventions) < cfg.min_interventions:
         raise TooFewInterventions(len(env.interventions), cfg.min_interventions)
+    associations: list[Association] = []
     if mode == "weekly":
         data = build_weekly(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
+    elif mode == "scan":
+        associations = run_scan(cfg, env.signals, env.interventions, env.start, env.end)
+        if not associations:
+            raise NothingToAnalyze(
+                "Der Scan fand keine auffälligen Zusammenhänge (Grenzen unter scan: in analysis.yaml).")
+        weekly = build_weekly(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
+        data = {"zeitraum": weekly["zeitraum"], "signale": env.signals.catalog(),
+                "assoziationen": [association_row(item, cfg) for item in associations[:cfg.scan.top_k]],
+                "wochenraster": {key: value for key, value in weekly.items() if key != "zeitraum"}}
     else:
         data = build_clusters(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
         if not data["cluster"]:
@@ -97,14 +114,18 @@
     rooms = {room_name(cfg, area): area for area in {item.room for item in env.interventions}}
     presets = known_presets(db) | {str(item.value) for item in env.interventions if item.kind == "preset"}
     flat_states = env.ctx.flat_state.states()
+    scanning = mode == "scan"
     parse_ctx = ParseContext(rooms=rooms, pseudo=env.pseudo, presets=frozenset(presets),
-                             flat_states=frozenset(flat_states))
-    messages = build_messages(mode, data, vocabulary_text(), cfg.max_candidates, presets=sorted(presets),
+                             flat_states=frozenset(flat_states),
+                             signals=env.signals.infos() if scanning else {},
+                             max_conditions=2 if scanning else None)
+    vocabulary = generic_vocabulary_text() if scanning else vocabulary_text()
+    messages = build_messages(mode, data, vocabulary, cfg.max_candidates, presets=sorted(presets),
                               flat_states=sorted(flat_states), rooms=sorted(rooms))
     leaks = env.pseudo.leaks("\n".join(message["content"] for message in messages))
     if leaks:
         raise PrivacyLeak(f"Der zu sendende Text enthält schützenswerte Begriffe: {', '.join(leaks)}")
-    return Prepared(mode, env, data, messages, parse_ctx)
+    return Prepared(mode, env, data, messages, parse_ctx, associations)
 
 
 async def run_analysis(db: Database, cfg: AnalysisConfig, mode: str, *, api_key: str,
@@ -122,6 +143,7 @@
     run_id = store.start_run(mode, cfg.model, params, now())
     try:
         raws, result = await completer(cfg, prepared.messages, api_key)
+        store.save_associations(run_id, prepared.associations)
         candidates = parse_candidates(raws, prepared.parse_ctx)
         tester = Backtester(cfg, env.interventions, env.ctx, env.start, env.end)
         counts: Counter[str] = Counter()
````

Patch für `ai/analysis/candidates.py`:

````diff
--- a/ai/analysis/candidates.py
+++ b/ai/analysis/candidates.py
@@ -21,9 +21,29 @@
     return ",".join(SHORT_DAYS[day] for day in days)
 
 
-def _trigger_text(trigger: dict, pseudo: Pseudonyms) -> str:
+def _signal_trigger_text(trigger: dict, signals: dict[str, str]) -> str:
+    name = signals.get(trigger["signal"], trigger["signal"])
+    event = trigger["event"]
+    if event == "to_state":
+        text = f"{name} wechselt zu {trigger['value']}"
+        low, high = trigger.get("min_prev_min"), trigger.get("max_prev_min")
+        if low is not None or high is not None:
+            text += f" (vorher {low or 0}–{high if high is not None else '∞'} min im anderen Zustand)"
+    elif event == "rises_above":
+        text = f"{name} steigt über {trigger['value']:g}"
+    elif event == "falls_below":
+        text = f"{name} fällt unter {trigger['value']:g}"
+    else:
+        text = f"{name}: Weckzeit erreicht"
+    offset = trigger.get("offset_min", 0)
+    return text + (f", {offset:+d} min" if offset else "")
+
+
+def _trigger_text(trigger: dict, pseudo: Pseudonyms, signals: dict[str, str] | None = None) -> str:
     if trigger["type"] == "time":
         return f"{weekday_label(trigger['weekdays'])} {trigger['time']}"
+    if trigger["type"] == "signal":
+        return _signal_trigger_text(trigger, signals or {})
     event = trigger["event"]
     if event == "person_arrives":
         return f"{pseudo.display_name(trigger['person'])} kommt heim"
@@ -35,8 +55,13 @@
             "window_closed": "Fenster geschlossen", "office_day_start": "Büro-Tag beginnt"}[event]
 
 
-def _condition_text(condition: dict, pseudo: Pseudonyms) -> str:
+def _condition_text(condition: dict, pseudo: Pseudonyms, signals: dict[str, str] | None = None) -> str:
     kind = condition["type"]
+    if kind in ("signal_state", "signal_range"):
+        name = (signals or {}).get(condition["signal"], condition["signal"])
+        if kind == "signal_state":
+            return f"{name} ist {'' if condition['op'] == 'is' else 'nicht '}{condition['value']}"
+        return f"{name} {'unter' if condition['op'] == 'below' else 'über'} {condition['value']:g}"
     if kind == "weekdays":
         return weekday_label(condition["weekdays"])
     if kind == "time_window":
@@ -54,13 +79,14 @@
     return f"{action['value']:g} °C"
 
 
-def describe(candidate: Candidate, cfg: AnalysisConfig, pseudo: Pseudonyms) -> tuple[str, str, str]:
+def describe(candidate: Candidate, cfg: AnalysisConfig, pseudo: Pseudonyms,
+             signals: dict[str, str] | None = None) -> tuple[str, str, str]:
     """Lesbarer Text für die lokale Anzeige (mit Klarnamen, geht nie an das Modell)."""
     room = room_name(cfg, candidate.room)
     try:
-        when = _trigger_text(candidate.trigger, pseudo)
+        when = _trigger_text(candidate.trigger, pseudo, signals)
         if candidate.conditions:
-            when += " wenn " + "; ".join(_condition_text(item, pseudo) for item in candidate.conditions)
+            when += " wenn " + "; ".join(_condition_text(item, pseudo, signals) for item in candidate.conditions)
         return room, when, _action_text(candidate.action)
     except (KeyError, TypeError, ValueError):
         return (room, json.dumps(candidate.trigger, ensure_ascii=False),
@@ -76,6 +102,9 @@
         if trigger["type"] == "time":
             hour, minute = (int(part) for part in trigger["time"].split(":"))
             key: tuple = ("time", tuple(sorted(trigger["weekdays"])), (hour * 60 + minute) // 30)
+        elif trigger["type"] == "signal":
+            key = ("signal", trigger["signal"], trigger["event"], str(trigger.get("value", "")),
+                   trigger.get("min_prev_min"), trigger.get("offset_min", 0) // 30)
         else:
             key = ("event", trigger["event"], trigger.get("person") or trigger.get("value") or "")
         action = candidate.action
````

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_scan_mode.py -v`
Expected: PASS (12 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (414 Tests insgesamt).

```bash
git add ai/analysis ai/tests
git commit -m "feat(ai): Modus scan sendet Signalkatalog und Assoziationen an das Modell"
```

---

### Task 8: Kommandos signals und scan

**Files:**
- Create: `ai/tests/test_scan_cli.py`
- Modify: `ai/analysis/cli.py`

**Interfaces:**
- Consumes: `load_environment`, `run_scan`, `Store.list_signals`/`save_associations`/`start_run`/`finish_run`, `event_text`, `EventSpec`, `describe`.
- Produces: Kommandos `analysis signals` und `analysis scan [--room R] [--top N]` (Lauf mit Modus `scan-only`, damit `compare` ihn nicht mit dem Modus `scan` verwechselt); `analysis compare` zeigt die Überlappung für jedes Paar von Läufen; `analysis list` zeigt Signal-Auslöser lesbar.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_scan_cli.py`:

```python
import asyncio

import pytest
import yaml

from analysis.cli import main
from analysis.store import Store
from collector.db import Database
from tests.analysis_fixture import ROOM
from tests.conftest import ROOT
from tests.scan_fixture import after_arrival, build_scan_db
from tests.test_scan_mode import ARRIVAL, answer


@pytest.fixture
def files(tmp_path, fake_llm):
    database = Database(tmp_path / "home_ai.sqlite")
    build_scan_db(database, after_arrival(lambda n: [12]))
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


async def test_signals_command_lists_handles_with_local_entity_ids(files, capsys):
    assert await cli(files, "signals") == 1                                   # noch keine Signale
    assert await cli(files, "scan") == 0
    capsys.readouterr()
    assert await cli(files, "signals") == 0
    out = capsys.readouterr().out
    assert "sig_01" in out and "person.svenja" in out and "categorical" in out and "Anwesenheit person_1" in out


async def test_scan_command_prints_associations_and_stores_a_run(files, capsys):
    assert await cli(files, "scan") == 0
    out = capsys.readouterr().out
    assert "wechselt zu home" in out and "Fenster +0 bis +30 min" in out and "30 von 30" in out and "lift" in out
    (row,) = stored(files).list_runs()
    assert row["mode"] == "scan-only" and row["status"] == "abgeschlossen"
    assert stored(files).list_associations(row["id"])


async def test_scan_command_filters_by_room_and_rejects_unknown_rooms(files, capsys):
    assert await cli(files, "scan", "--room", "Wohnzimmer") == 0
    assert "Wohnzimmer" in capsys.readouterr().out
    assert await cli(files, "scan", "--room", "Keller") == 2


async def test_scan_dry_run_shows_the_prompt_without_names(files, fake_llm, capsys):
    assert await cli(files, "run", "--mode", "scan", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Modus: Scan" in out and "sig_01" in out and "kein Aufruf" in out
    assert fake_llm.requests == [] and "svenja" not in out.lower()


async def test_scan_run_and_list_show_generic_candidates_readably(files, fake_llm, capsys):
    fake_llm.responses = [answer(ARRIVAL)]
    assert await cli(files, "run", "--mode", "scan") == 0
    assert "Lauf 1 (scan) abgeschlossen: 1 Kandidaten" in capsys.readouterr().out
    assert await cli(files, "list") == 0
    out = capsys.readouterr().out
    assert "Anwesenheit person_1 (home oder away) wechselt zu home" in out and "[bestanden]" in out
    assert await cli(files, "compare") == 0
    assert "scan" in capsys.readouterr().out


async def test_compare_prints_the_overlap_for_every_pair_of_modes(files, fake_llm, capsys):
    fake_llm.responses = [answer(ARRIVAL), answer(), answer()]
    for mode in ("scan", "weekly", "clusters"):
        await cli(files, "run", "--mode", mode)
    capsys.readouterr()
    assert await cli(files, "compare") == 0
    out = capsys.readouterr().out
    assert out.count("Überlappung Lauf") == 3
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_scan_cli.py -v`
Expected: FAIL (5 der neuen Tests scheitern, die übrigen Tests der Dateien bestehen).

- [ ] **Step 3: Implementieren**

Patch für `ai/analysis/cli.py`:

````diff
--- a/ai/analysis/cli.py
+++ b/ai/analysis/cli.py
@@ -7,14 +7,18 @@
 import sys
 
 from collector.db import Database
-from collector.models import parse_utc
+from collector.models import parse_utc, utcnow
 
 from .candidates import describe, signature
 from .config import AnalysisConfigError, load_analysis_config
+from .events import EventSpec, event_text
+from .features import room_name
 from .presence import analyze as analyze_presence, render as render_presence
 from .llm import LlmError, estimate_cost, with_live_prices
 from .pseudonym import build_pseudonyms
-from .runner import MODES, AnalysisError, backtest_candidate, prepare, run_analysis
+from .runner import (MODES, AnalysisError, TooFewInterventions, backtest_candidate, load_environment, prepare,
+                     run_analysis)
+from .scan import run_scan
 from .store import Store
 
 STATUSES = ("bestanden", "zu_wenig_belege", "abgelehnt", "nicht_pruefbar")
@@ -38,6 +42,10 @@
     presence = sub.add_parser("presence", help="Anwesenheit und manuelle Eingriffe gegenüberstellen")
     presence.add_argument("--classes", default="45,180",
                           help="Grenzen der Abwesenheitsklassen in Minuten, Komma getrennt (Standard 45,180)")
+    sub.add_parser("signals", help="aufgezeichnete Signale mit Kennung und Art anzeigen")
+    scan = sub.add_parser("scan", help="Assoziationen zwischen Ereignissen und Eingriffen suchen (ohne Modell)")
+    scan.add_argument("--room", help="nur dieser Raum (Klartextname)")
+    scan.add_argument("--top", type=int, default=30, help="so viele Zeilen anzeigen (Standard 30)")
     backtest = sub.add_parser("backtest", help="Backtest eines Kandidaten erneut rechnen")
     backtest.add_argument("candidate_id", type=int)
     backtest.add_argument("--from", dest="since")
@@ -107,18 +115,20 @@
               f"{counts['bestanden']:>9} {counts['zu_wenig_belege']:>15} {counts['abgelehnt']:>9} "
               f"{counts['nicht_pruefbar']:>14} {cost:>8.4f}")
         signatures.append({key for key in map(signature, candidates) if key is not None})
-    if len(rows) == 2:
-        first, second = rows[0]["id"], rows[1]["id"]
-        both = signatures[0] & signatures[1]
-        print(f"Überlappung Lauf {first} / Lauf {second}: gemeinsam {len(both)}, "
-              f"nur Lauf {first}: {len(signatures[0] - both)}, nur Lauf {second}: {len(signatures[1] - both)}")
+    for left in range(len(rows)):
+        for right in range(left + 1, len(rows)):
+            first, second = rows[left]["id"], rows[right]["id"]
+            both = signatures[left] & signatures[right]
+            print(f"Überlappung Lauf {first} / Lauf {second}: gemeinsam {len(both)}, "
+                  f"nur Lauf {first}: {len(signatures[left] - both)}, nur Lauf {second}: {len(signatures[right] - both)}")
     return 0
 
 
 def _cmd_list(cfg, db: Database, store: Store, args) -> int:
     pseudo = build_pseudonyms(db)
+    signals = store.signal_descriptions()
     for item in store.list_candidates(run_id=args.run_id, status=args.status):
-        room, when, action = describe(item, cfg, pseudo)
+        room, when, action = describe(item, cfg, pseudo, signals)
         line = f"{item.id:>4}  {room}  {when}  ->  {action}  [{item.status}]"
         metrics = store.results(item.id)
         if metrics:
@@ -138,6 +148,48 @@
     if not edges or edges[0] <= 0:
         raise AnalysisError("--classes braucht mindestens eine positive Zahl")
     print(render_presence(analyze_presence(db, cfg, edges=edges)))
+    return 0
+
+
+def _cmd_signals(db: Database, store: Store) -> int:
+    rows = store.list_signals()
+    if not rows:
+        print("Noch keine Signale. Sie entstehen beim ersten Scan oder Lauf.")
+        return 1
+    print(f"{'Kennung':<8} {'Art':<12} {'Kind':<9} {'Entität (nur lokal)':<52} Beschreibung")
+    for row in rows:
+        print(f"{row['handle']:<8} {row['value_kind']:<12} {row['kind']:<9} {row['entity_id']:<52} {row['description']}")
+    return 0
+
+
+def _cmd_scan(cfg, db: Database, store: Store, args) -> int:
+    env = load_environment(db, cfg)
+    if len(env.interventions) < cfg.min_interventions:
+        raise TooFewInterventions(len(env.interventions), cfg.min_interventions)
+    found = run_scan(cfg, env.signals, env.interventions, env.start, env.end)
+    run_id = store.start_run("scan-only", "-", {"eingriffe": len(env.interventions)}, utcnow())
+    store.save_associations(run_id, found)
+    store.finish_run(run_id, "abgeschlossen", utcnow())
+    if args.room:
+        area = {name: area for area, name in cfg.room_names.items()}.get(args.room)
+        if area is None:
+            raise AnalysisError(f"Unbekannter Raum {args.room!r} (bekannt: {', '.join(sorted(cfg.room_names.values()))})")
+        found = [item for item in found if item.room == area]
+    if not found:
+        print("Keine auffälligen Zusammenhänge gefunden.")
+        return 0
+    print(f"{len(env.interventions)} Eingriffe, {len(env.signals.usable())} nutzbare Signale. Treffer: Ereignisse mit Eingriff "
+          "im Fenster (Kontrolle: dieselbe Uhrzeit an anderen Tagen).")
+    shown = found[:args.top]
+    for item in shown:
+        info = env.signals.get(item.signal).info
+        spec = EventSpec(item.signal, item.event, item.value, item.prev_class)
+        room = "alle Räume" if item.room is None else room_name(cfg, item.room)
+        print(f"{item.signal} {info.description}: {event_text(spec)}\n"
+              f"      {room}, Fenster {item.win_start:+d} bis {item.win_end:+d} min: {item.hits} von {item.events} "
+              f"({100 * item.hits / item.events:.0f}%), Kontrolle {100 * item.control_hits / item.control_events:.0f}%, "
+              f"lift {item.lift:.1f}, q {item.q:.3g}" + (f", {item.typical_action}" if item.typical_action else ""))
+    print(f"{len(shown)} von {len(found)} Zeilen. Lauf {run_id} gespeichert.")
     return 0
 
 
@@ -162,6 +214,10 @@
             store = Store(db)
             if args.command == "run":
                 return _cmd_run(cfg, db, args)
+            if args.command == "signals":
+                return _cmd_signals(db, store)
+            if args.command == "scan":
+                return _cmd_scan(cfg, db, store, args)
             if args.command == "presence":
                 return _cmd_presence(cfg, db, args)
             if args.command == "compare":
````

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_scan_cli.py -v`
Expected: PASS (6 Tests).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (420 Tests insgesamt).

```bash
git add ai/analysis ai/tests
git commit -m "feat(ai): Kommandos signals und scan, Vergleich über alle Modi"
```

---

### Task 9: Dokumentation und Container

**Files:**
- Modify: `ai/README.md`

**Interfaces:**
- Consumes: alles aus den Aufgaben 1 bis 8.
- Produces: README-Abschnitt „Signale und automatische Voranalyse (Scan)“ mit den Kommandos, den Konfigurationsabschnitten `signal_labels` und `scan` und dem Hinweis zu `unique_id` an den Wecker-Template-Sensoren.

- [ ] **Step 1: Dokumentation ergänzen**

Patch für `ai/README.md`:

````diff
--- a/ai/README.md
+++ b/ai/README.md
@@ -120,3 +120,29 @@
 - Ein Lauf bricht ohne Aufruf ab, wenn weniger als `min_interventions` Eingriffe im Zeitraum liegen oder das geschätzte Kostenlimit überschritten würde.
 - Kandidaten mit Status `nicht_pruefbar` nutzen etwas, das das System nicht nachrechnen kann (unbekannter Raum, fehlender Sensor); die Notiz nennt den Grund.
 - Das Ende des Ausschlusses für das Schlafzimmer in `analysis.yaml` ist das Datum des Thermostattauschs und beim Einrichten anzupassen.
+
+### Signale und automatische Voranalyse (Scan)
+
+Jede Entität mit dem Label `KI_DB_Sync` (außer den Thermostaten) ist ein **Signal** mit einer dauerhaften Kennung `sig_01`, `sig_02` …, deren Art (Kategorie, Zahl, Zeitstempel) aus den Daten erkannt wird. Der Scan prüft für jedes Signal automatisch, ob in der Nähe seiner Ereignisse auffällig oft manuell eingegriffen wird (Vergleich mit derselben Uhrzeit an anderen Tagen, Korrektur für viele Tests). Das Modell bekommt im Modus `scan` die Ergebnisse und formuliert daraus Regeln mit einem allgemeinen Vokabular (beliebiges Signal als Auslöser oder Bedingung, Versatz, Dauer des vorherigen Zustands).
+
+```bash
+docker exec home-ai-collector python -m analysis signals          # Kennungen, Art, Beschreibung, lokal die Entität
+docker exec home-ai-collector python -m analysis scan             # Assoziationen ohne Modell, mit Klarnamen lokal
+docker exec home-ai-collector python -m analysis scan --room Wohnzimmer --top 50
+docker exec home-ai-collector python -m analysis run --mode scan --dry-run
+docker exec home-ai-collector python -m analysis run --mode scan
+```
+
+Beschreibungen für das Modell stehen in `signal_labels:` in `analysis.yaml` (keine Klarnamen). Die Grenzen des Scans (`min_events`, `min_hits`, `q_max`, `top_k`, Dauerklassen) stehen unter `scan:`.
+
+**Wecker:** Die beiden `*_next_alarm`-Sensoren (Zeitstempel) liefern das Ereignis „Weckzeit erreicht“. Die beiden `*_alarmclock_triggered`-Sensoren sind Template-Sensoren aus der `configuration.yaml` und brauchen ein `unique_id`, damit sie in der Registry stehen und das Label tragen können:
+
+```yaml
+template:
+  - sensor:
+      - name: fabians_mobile_alarmclock_triggered
+        unique_id: fabians_mobile_alarmclock_triggered
+        state: "..."          # wie bisher
+```
+
+Danach Template-Entitäten neu laden, Label setzen und den Backfill der Snapshots wiederholen (idempotent).
````

- [ ] **Step 2: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS (420 Tests insgesamt).

```bash
git add ai/README.md
git commit -m "docs(ai): README zu Signalen, Scan und Wecker-Sensoren"
```

- [ ] **Step 3: Image bauen**

Run: `cd ai && docker build -t home-ai-collector .` und `docker run --rm home-ai-collector python -m analysis --help`
Expected: Bau ohne Fehler; der Hilfetext nennt die Unterbefehle `run`, `compare`, `list`, `presence`, `signals`, `scan`, `backtest`. Falls auf diesem Rechner kein Docker läuft, den Bau auf dem Docker-Host ausführen und hier abhaken.

---

### Task 10: Abnahme mit den echten Daten

Diese Aufgabe führt der Mensch aus (Zugangsdaten, Server). Der Agent bereitet nur vor und liest die Ausgaben.

**Files:**
- Modify: Home Assistant `configuration.yaml` (`unique_id` an den beiden Wecker-Template-Sensoren)

- [ ] **Step 1: [du] Code holen, Image bauen, Stack neu deployen**

```bash
cd /opt/heating_control && git pull && docker build -t home-ai-collector ai
```
Danach den Stack in Dockhand neu deployen.
Expected: `docker exec home-ai-collector python -m analysis --help` nennt `signals` und `scan`.

- [ ] **Step 2: [du] Wecker-Sensoren labelbar machen**

In der `configuration.yaml` bei den beiden Template-Sensoren `fabians_mobile_alarmclock_triggered` und `svenjas_mobile_alarmclock_triggered` je eine `unique_id` ergänzen (zum Beispiel denselben Namen), Konfiguration prüfen und die Template-Entitäten neu laden. Danach beim Agenten melden; er setzt über den Home-Assistant-MCP das Label `KI_DB_Sync` (vor der Änderung nachfragen). Die beiden `*_next_alarm`-Sensoren tragen es bereits.
Expected: Die vier Wecker-Entitäten stehen in der Entity-Registry und tragen das Label. Ihr Verlauf bleibt unter derselben Entity-ID erhalten.

- [ ] **Step 3: [du] Backfill der Snapshots wiederholen**

Derselbe Befehl wie beim ersten Einspielen (`python -m collector backfill --db /snapshots/june/home-assistant_v2.db`, `HA_TOKEN` und `HA_URL` gesetzt). Der Lauf ist idempotent und holt den Verlauf der neu gelabelten Entitäten nach.
Expected: `neu gespeichert` größer als 0 (Verlauf der Wecker-Sensoren), `verify` unverändert 100 %.

- [ ] **Step 4: [du] Signale ansehen**

```bash
docker exec home-ai-collector python -m analysis signals
```
Expected: Tabelle mit Kennung, Art, Kind, Entität (lokal) und Beschreibung. Die beiden `*_next_alarm`-Sensoren haben die Art `timestamp`, die `*_alarmclock_triggered`-Sensoren die Art `categorical`. Signale mit der Art `none` sind erwartbar (zum Beispiel Freitext wie der Raum-Sensor des Handys); stehen dort Signale, die eigentlich nutzbar sein sollten, die Zustände ansehen und `scan.min_value_count` oder `max_values` anpassen.

- [ ] **Step 5: [du] Scan ohne Modell**

```bash
docker exec home-ai-collector python -m analysis scan --top 40
```
Expected (Erfolgskriterium 1 der Spezifikation): Für das Wohnzimmer steht die Heimkehr einer Person nach langer Abwesenheit (Ereignis „wechselt zu home“ mit der Dauerklasse ≥180) unter den obersten Treffern im Fenster 0 bis +30 oder +60 Minuten, mit lift deutlich über 2, **ohne dass dem Scan das vorgegeben wurde**. Falls nicht, nicht weitermachen: Kontrolle und Ereignisdefinition prüfen (`analysis presence` zeigt dieselbe Zahl von Hand).

- [ ] **Step 6: [du] Datenschutzprüfung und Lauf**

```bash
docker exec home-ai-collector python -m analysis run --mode scan --dry-run
```
Expected: Der Text enthält `sig_NN`, Beschreibungen mit `person_1`/`person_2`, Räume im Klartext und keine Namen oder Entity-IDs. Danach `docker exec home-ai-collector python -m analysis run --mode scan`, dann `analysis list --run <id>` und `analysis compare`. **Halt:** Steht im Dry-Run ein Klarname, abbrechen und melden.

- [ ] **Step 7: Auswertung**

Die Qualität der Regeln ist bewusst nicht Teil der Abnahme. Geprüft wird: Der Scan findet die bekannte Heimkehr-Assoziation, die Wecker-Signale erscheinen im Scan (wenn sie etwas erklären) oder bleiben still (wenn nicht), der Modus `scan` läuft durch, und `compare` zeigt alle drei Modi nebeneinander. Danach entscheiden Mensch und Agent, ob Kombinationen (Entscheidungsbaum) oder ein Werkzeug-Modus (Spezifikation, Abschnitt 2) als nächster Schritt folgen.
