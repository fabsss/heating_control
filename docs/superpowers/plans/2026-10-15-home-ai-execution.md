# KI-Regeln: Freigabe und Ausführung Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Geprüfte Regelkandidaten als Automationen in Home Assistant anlegen (zuerst im Schattenmodus), auswerten, per Dashboard aktivieren und den Container im Normalbetrieb aus dem Dashboard bedienen.

**Architecture:** Der Collector zeichnet zusätzlich `call_service` und `automation_triggered` auf und berechnet je Thermostat-Änderung eine Herkunft (Benutzer, Automation, Gerät/unklar, unbekannt). Alles, was schaltet, liegt in HA (Blueprint `ki_regel.yaml`, Schalter, Mindestabstand) und läuft ohne den Container. Ein neuer Ausführungsdienst (`execution`) legt Regeln über die HA-API an, wertet die MQTT-Meldungen `homeai/rule/<id>/fired` aus, veröffentlicht Kennzahlen und führt Aufträge des Dashboards aus einer festen Liste aus.

**Tech Stack:** Python 3.12, aiohttp, aiomqtt, PyYAML, SQLite, pytest und pytest-asyncio (`asyncio_mode = auto`), Jinja2 (nur Tests, für die HA-Templates), Home Assistant 2026.10 (Blueprints, MQTT-Discovery).

**Spec:** `docs/superpowers/specs/2026-10-15-home-ai-execution-design.md` (vorherige Teilprojekte: `2026-10-13-home-ai-data-foundation-design.md`, `2026-10-13-home-ai-analysis-design.md`, `2026-10-14-home-ai-signal-scan-design.md`)

## Global Constraints

- Sprache: Code-Bezeichner Englisch, Meldungen, Kommentare, Dashboard-Texte und Commit-Nachrichten Deutsch (wie im übrigen Repo, Format `feat(ai): …`). Commit-Nachrichten enden mit der Zeile `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Alle Befehle laufen im Verzeichnis `ai/` (`cd ai && python -m pytest …`); `ai/pytest.ini` setzt `pythonpath = .` und `asyncio_mode = auto`.
- Zeiten in der Datenbank sind UTC-ISO-Texte mit Mikrosekunden (`collector.models.to_utc_iso`), Vergleich als Text entspricht dem Zeitvergleich.
- Keine Zugangsdaten im Repo: HA-Schreibtoken `HA_WRITE_TOKEN`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD` nur aus der Umgebung.
- Pseudonymisierung bleibt unberührt: Weder `sig_NN` noch Pseudonyme gelangen nach HA; Herkunft, Benutzerkennungen und Entity-IDs gehen nie an das Modell.
- MQTT-Präfix `homeai/`; Discovery unter `homeassistant/`; Entity-IDs der Discovery über `default_entity_id` (Home Assistant 2026.10).
- Standardwerte: Mindestabstand 60 Minuten; Sollwert-Grenzen 15 bis 25 °C; gesperrt nur `Vacation`; Auswertungsfenster 60 Minuten vor bis 90 Minuten nach der Auslösung; Toleranz gleiches Ziel ±0,5 °C; Aktivierungsreife ab 10 gewerteten Auslösungen und unter 20 % Fehl- bzw. Korrekturquote; höchstens 3 Läufe mit dem Modell je Tag; Kosten-Freigabe 60 Sekunden; Ergebnistext höchstens 12000 Zeichen.
- `heating_control.yaml` ändert sich nur um die optionale Eingabe `ki_rules_toggle` (V4.29); Trigger und Aktionen bleiben unverändert.
- Der Blueprint `ki_regel.yaml` setzt Home Assistant 2024.10 oder neuer voraus (neue Schreibweise `triggers`/`conditions`/`actions`, Selektoren `trigger` und `condition`).

## Review Focus

Eingaben und Zustände, die die Spezifikation voraussetzt, die aber keine Aufgabe von sich aus prüfen würde (jede Zeile hat einen Test in der genannten Aufgabe; die Zahlen verweisen auf die Aufgaben dieses Plans):

1. **Heizkörper nicht erreichbar (`unavailable`/`unknown`) bei der Auslösung:** keine Schaltung, Grund `nicht_erreichbar`, keine Wertung (Aufgabe 6, Aufgabe 12).
2. **Eingriff genau am Rand des Mindestabstands (60 Minuten):** die Regel schaltet ab 60 Minuten, davor nicht (Aufgabe 6).
3. **Mehrere Eingriffe im Auswertungsfenster:** der letzte zählt; eine Automation dazwischen ergibt `unterbrochen` (Aufgabe 12).
4. **Doppelte oder nachgelieferte `fired`-Meldung** (Broker liefert erneut, Container startet neu): keine doppelte Zählung (Aufgabe 15).
5. **Deploy bei unerreichbarem HA oder halb fertigem Zustand:** kein Eintrag in `deployments`, Discovery und Automation werden zurückgenommen (Aufgabe 14); Kandidaten mit altem Vokabular oder Sollwert außerhalb der Grenzen werden mit klarer Meldung abgelehnt (Aufgabe 9).
6. **Zweiter Auftrag während einer laufenden Analyse, Lauf mit dem Modell ohne Kosten-Freigabe oder über dem Tageslimit:** wird abgelehnt und gemeldet, nichts startet (Aufgabe 16).

## Dateistruktur

```
ai/collector/models.py          # + ServiceCall, AutomationRun
ai/collector/db.py              # + insert_service_call, insert_automation_run, mark_capture_start, capture_start
ai/collector/origins.py         # NEU: Herkunft je Thermostat-Änderung
ai/collector/live.py            # + Abos call_service/automation_triggered, Herkunft nach jedem Flush
ai/collector/cli.py             # + Kommando origins
ai/sql/schema.sql               # + service_calls, automation_runs, change_origin, capture_state; View manual_actions
ai/analysis/interventions.py    # zählt auch Herkunft "Gerät"
heating_control.yaml            # V4.29: Eingabe ki_rules_toggle
blueprints/ki_regel.yaml        # NEU: Blueprint der Regel-Automation
ai/execution/__init__.py        # NEU
ai/execution/__main__.py
ai/execution/config.py          # execution.yaml laden
ai/execution/models.py          # Deployment, RuleEvent, Stats
ai/execution/store.py           # Tabellen deployments, rule_events, rule_outcomes, jobs
ai/execution/helpers.py         # Helfer-Namen, Hilfsautomationen (Mindestabstand)
ai/execution/translate.py       # Kandidat -> Blueprint-Eingaben
ai/execution/ha_api.py          # REST und WebSocket gegen HA
ai/execution/mqtt.py            # Bus (aiomqtt / in memory), Discovery der Regeln
ai/execution/outcomes.py        # Auswertung und Kennzahlen
ai/execution/deploy.py          # init, deploy, undeploy, check
ai/execution/service.py         # Dauerbetrieb: fired auswerten, Kennzahlen veröffentlichen
ai/execution/controls.py        # Bedienelemente (Discovery) und Dashboard
ai/execution/jobs.py            # Auftragsläufer, Auswahllisten, Statustext
ai/execution/actions.py         # LiveActions: Aktionen des Dashboards gegen die echte HA
ai/execution/serve.py           # Dauerbetrieb zusammensetzen (Bus, Service, Auftragsläufer)
ai/execution/cli.py             # init, deploy, undeploy, rules, check, serve
ai/sql/execution.sql            # NEU
ai/config/execution.yaml        # NEU
ai/tests/                       # fake_hass.py (events_by_type), fake_ha_rest.py, ha_template.py, execution_fixture.py, test_*.py
ai/Dockerfile, ai/docker-compose.yml, ai/requirements*.txt, ai/.env.example, ai/README.md
```

---

### Task 1: Tabellen, Modelle und Datenbank-Methoden für Aufrufe, Läufe und Herkunft

**Files:**
- Modify: `ai/sql/schema.sql` (vor `DROP VIEW IF EXISTS manual_actions;`)
- Modify: `ai/collector/models.py` (am Ende)
- Modify: `ai/collector/db.py` (Import und neue Methoden)
- Test: `ai/tests/test_origin_tables.py`

**Interfaces:**
- Consumes: bestehend `Database`, `to_utc_iso`, `attrs_json`, `parse_utc`.
- Produces:
  - `collector.models.ServiceCall(ts: datetime, domain: str, service: str, entity_ids: tuple[str, ...] = (), data: dict = {}, context_id: str | None = None, user_id: str | None = None, parent_id: str | None = None)`
  - `collector.models.AutomationRun(ts: datetime, automation_entity: str, name: str | None = None, context_id: str | None = None)`
  - `Database.insert_service_call(call: ServiceCall) -> bool` (True, wenn neu), `Database.insert_automation_run(run: AutomationRun) -> bool`
  - `Database.mark_capture_start(key: str, now: datetime) -> None` (nur beim ersten Aufruf je Schlüssel), `Database.capture_start(key: str) -> datetime | None`
  - Tabellen `service_calls`, `automation_runs`, `change_origin(state_change_id PK, origin, via, actor, automation)`, `capture_state(key PK, value)`.

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_origin_tables.py`:

```python
from datetime import datetime, timezone

from collector.db import Database
from collector.models import AutomationRun, ServiceCall

T0 = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)


def test_service_call_is_stored_once(db: Database):
    call = ServiceCall(T0, "climate", "set_preset_mode", ("climate.bt",), {"preset_mode": "comfort"},
                       "c1", "u1", None)
    assert db.insert_service_call(call) is True
    assert db.insert_service_call(call) is False
    row = db.conn.execute("SELECT * FROM service_calls").fetchone()
    assert (row["domain"], row["service"], row["context_id"], row["user_id"], row["parent_id"]) == (
        "climate", "set_preset_mode", "c1", "u1", None)
    assert row["entity_ids"] == '["climate.bt"]'
    assert row["data"] == '{"preset_mode":"comfort"}'


def test_automation_run_is_stored_once(db: Database):
    run = AutomationRun(T0, "automation.hc", "Heating Control", "c9")
    assert db.insert_automation_run(run) is True
    assert db.insert_automation_run(run) is False
    row = db.conn.execute("SELECT * FROM automation_runs").fetchone()
    assert (row["automation_entity"], row["name"], row["context_id"]) == ("automation.hc", "Heating Control", "c9")


def test_capture_start_keeps_the_first_value(db: Database):
    assert db.capture_start("calls_since") is None
    db.mark_capture_start("calls_since", T0)
    db.mark_capture_start("calls_since", datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert db.capture_start("calls_since") == T0


def test_change_origin_table_exists(db: Database):
    db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via) VALUES (1, 'benutzer', 'kontext')")
    assert db.conn.execute("SELECT origin FROM change_origin").fetchone()["origin"] == "benutzer"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_origin_tables.py -v`
Expected: FAIL mit `ImportError: cannot import name 'AutomationRun'`.

- [ ] **Step 3: Implementieren**

In `ai/sql/schema.sql` die Zeile `DROP VIEW IF EXISTS manual_actions;` ersetzen durch:

```sql
CREATE TABLE IF NOT EXISTS service_calls(
  id          INTEGER PRIMARY KEY,
  ts_utc      TEXT NOT NULL,
  domain      TEXT NOT NULL,
  service     TEXT NOT NULL,
  entity_ids  TEXT NOT NULL DEFAULT '[]',          -- JSON-Liste, leer = Ziel über Bereich/Gerät
  data        TEXT NOT NULL DEFAULT '{}',
  context_id  TEXT,
  user_id     TEXT,
  parent_id   TEXT,
  UNIQUE(ts_utc, domain, service, entity_ids, context_id)
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON service_calls(ts_utc);
CREATE INDEX IF NOT EXISTS idx_calls_ctx ON service_calls(context_id);

CREATE TABLE IF NOT EXISTS automation_runs(
  id                INTEGER PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  automation_entity TEXT NOT NULL,
  name              TEXT,
  context_id        TEXT,
  UNIQUE(ts_utc, automation_entity, context_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_ctx ON automation_runs(context_id);

CREATE TABLE IF NOT EXISTS change_origin(
  state_change_id INTEGER PRIMARY KEY,
  origin          TEXT NOT NULL,                   -- benutzer|automation|geraet_unklar|unbekannt
  via             TEXT NOT NULL,                   -- kontext|zeit|keine
  actor           TEXT,                            -- Benutzerkennung bei "benutzer"
  automation      TEXT                             -- Entity-ID der Automation bei "automation"
);

CREATE TABLE IF NOT EXISTS capture_state(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

DROP VIEW IF EXISTS manual_actions;
```

An `ai/collector/models.py` anhängen:

```python


@dataclass(frozen=True)
class ServiceCall:
    ts: datetime
    domain: str
    service: str
    entity_ids: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    context_id: str | None = None
    user_id: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True)
class AutomationRun:
    ts: datetime
    automation_entity: str
    name: str | None = None
    context_id: str | None = None
```

In `ai/collector/db.py` den Import ändern:

```python
from .models import AutomationRun, Change, EntityRule, ServiceCall, attrs_hash, attrs_json, parse_utc, to_utc_iso
```

und vor dem Abschnitt `# --- Verwaltung` einfügen:

```python
    # --- Aufrufe, Automationen, Erfassung ------------------------------------------

    def insert_service_call(self, call: ServiceCall) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO service_calls(ts_utc, domain, service, entity_ids, data, context_id, user_id, parent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (to_utc_iso(call.ts), call.domain, call.service, json.dumps(list(call.entity_ids)),
             attrs_json(call.data), call.context_id, call.user_id, call.parent_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def insert_automation_run(self, run: AutomationRun) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO automation_runs(ts_utc, automation_entity, name, context_id) VALUES (?, ?, ?, ?)",
            (to_utc_iso(run.ts), run.automation_entity, run.name, run.context_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def mark_capture_start(self, key: str, now: datetime) -> None:
        self.conn.execute("INSERT OR IGNORE INTO capture_state(key, value) VALUES (?, ?)", (key, to_utc_iso(now)))
        self.conn.commit()

    def capture_start(self, key: str) -> datetime | None:
        row = self.conn.execute("SELECT value FROM capture_state WHERE key = ?", (key,)).fetchone()
        return parse_utc(row["value"]) if row else None
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd ai && python -m pytest tests/test_origin_tables.py tests/test_db.py -v`
Expected: PASS (alle).

- [ ] **Step 5: Commit**

```bash
git add ai/sql/schema.sql ai/collector/models.py ai/collector/db.py ai/tests/test_origin_tables.py
git commit -m "feat(ai): Tabellen und Methoden fuer Dienstaufrufe, Automationslaeufe und Herkunft" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Ereignisse `call_service` und `automation_triggered` im Collector erfassen

**Files:**
- Modify: `ai/tests/fake_hass.py` (Ereignisse nach Art)
- Modify: `ai/collector/live.py`
- Test: `ai/tests/test_live_events.py`

**Interfaces:**
- Consumes: `ServiceCall`, `AutomationRun`, `Database.insert_service_call`, `Database.insert_automation_run`, `Database.mark_capture_start` (Aufgabe 1).
- Produces:
  - `collector.live.service_call_from_event(event: dict) -> ServiceCall | None` (nur Domäne `climate`)
  - `collector.live.automation_run_from_event(event: dict) -> AutomationRun | None`
  - `FakeHass.events_by_type: dict[str, list[dict]]` (Ereignisse für Abos anderer Art als `state_changed`)
  - `Collector` abonniert beide Ereignisse, speichert sie und setzt `capture_start("calls_since")`.
  - `Collector._flush` ruft `origins.update_origins` auf (Aufgabe 3 liefert die Funktion; in dieser Aufgabe wird nur gespeichert, siehe Step 3 Hinweis).

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_live_events.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from collector.hass_ws import HassClient, HassConnectionError
from collector.live import automation_run_from_event, service_call_from_event
from tests.test_live import HISTORY, entity_entry, make_collector, prepare, state_event


def call_event(service, entity_id, stamp, *, user=None, parent=None, ctx="01CTX", domain="climate", extra=None):
    data = {} if entity_id is None else {"entity_id": entity_id}
    data.update(extra or {})
    return {"event_type": "call_service", "time_fired": stamp,
            "data": {"domain": domain, "service": service, "service_data": data},
            "context": {"id": ctx, "parent_id": parent, "user_id": user}}


def run_event(entity_id, stamp, name="Heating Control", ctx="01AUTO"):
    return {"event_type": "automation_triggered", "time_fired": stamp,
            "data": {"name": name, "entity_id": entity_id, "source": "state of x"},
            "context": {"id": ctx, "parent_id": None, "user_id": None}}


def test_service_call_with_entity_list_and_user():
    call = service_call_from_event(call_event(
        "set_preset_mode", ["climate.bt"], "2026-10-15T11:45:25.022794+00:00", user="u1",
        extra={"preset_mode": "comfort"}))
    assert (call.domain, call.service, call.entity_ids) == ("climate", "set_preset_mode", ("climate.bt",))
    assert call.data == {"preset_mode": "comfort"}
    assert (call.context_id, call.user_id, call.parent_id) == ("01CTX", "u1", None)
    assert call.ts.isoformat() == "2026-10-15T11:45:25.022794+00:00"


def test_service_call_with_single_entity_string():
    call = service_call_from_event(call_event("set_preset_mode", "climate.bt", "2026-10-15T11:46:17+00:00"))
    assert call.entity_ids == ("climate.bt",)


def test_service_call_without_entity_id_keeps_empty_targets():
    call = service_call_from_event(call_event("set_temperature", None, "2026-10-15T11:46:17+00:00",
                                              extra={"area_id": "bad"}))
    assert call.entity_ids == ()
    assert call.data == {"area_id": "bad"}


def test_service_calls_of_other_domains_are_ignored():
    assert service_call_from_event(call_event("turn_on", "fan.x", "2026-10-15T11:46:17+00:00", domain="fan")) is None
    assert service_call_from_event({}) is None


def test_automation_run_from_event():
    run = automation_run_from_event(run_event("automation.hc", "2026-10-15T01:00:00+00:00"))
    assert (run.automation_entity, run.name, run.context_id) == ("automation.hc", "Heating Control", "01AUTO")
    assert automation_run_from_event({"data": {}}) is None


async def test_run_session_stores_calls_and_runs_and_derives_origin(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("climate.better_thermostat_office")])
    base = datetime.now(timezone.utc) - timedelta(minutes=5)      # jünger als der Rückblick, älter als die Wartezeit

    def at(microseconds):
        return (base + timedelta(microseconds=microseconds)).isoformat()

    fake_hass.events = [state_event("climate.better_thermostat_office", "heat", at(500),
                                    {"temperature": 21, "preset_mode": "comfort"}, user="u-fabian", ctx="01CTX")]
    fake_hass.events_by_type = {
        "call_service": [
            call_event("set_preset_mode", ["climate.better_thermostat_office"], at(100), user="u-fabian"),
            call_event("set_temperature", ["climate.trv_office"], at(200), ctx="01TRV"),
            call_event("turn_on", ["fan.x"], at(300), domain="fan"),
        ],
        "automation_triggered": [run_event("automation.hc", at(0))],
    }
    fake_hass.close_after_command = HISTORY
    client = HassClient(fake_hass.url, "secret")
    collector = make_collector(cfg, db, fake_hass)
    with pytest.raises(HassConnectionError):
        await collector.run_session(client)
    await client.close()
    calls = db.conn.execute("SELECT service, entity_ids, user_id FROM service_calls").fetchall()
    assert [tuple(r) for r in calls] == [("set_preset_mode", '["climate.better_thermostat_office"]', "u-fabian")]
    runs = db.conn.execute("SELECT automation_entity FROM automation_runs").fetchall()
    assert [r["automation_entity"] for r in runs] == ["automation.hc"]
    assert db.capture_start("calls_since") is not None
    origin = db.conn.execute("SELECT origin, via, actor FROM change_origin").fetchall()
    assert [tuple(r) for r in origin] == [("benutzer", "kontext", "u-fabian")]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_live_events.py -v`
Expected: FAIL mit `ImportError: cannot import name 'service_call_from_event'`.

- [ ] **Step 3: Implementieren**

`ai/tests/fake_hass.py`: im Konstruktor nach `self.events: list[dict] = []` ergänzen:

```python
        self.events_by_type: dict[str, list[dict]] = {}
```

und den Block unter `if msg["type"] == "subscribe_events":` ersetzen durch:

```python
            if msg["type"] == "subscribe_events":
                await ws.send_json({"id": msg["id"], "type": "result", "success": True, "result": None})
                event_type = msg.get("event_type")
                events = self.events if event_type in (None, "state_changed") else self.events_by_type.get(event_type, [])
                if self.batch_events and events:
                    await ws.send_json(
                        [{"id": msg["id"], "type": "event", "event": event} for event in events]
                    )
                else:
                    for event in events:
                        await ws.send_json({"id": msg["id"], "type": "event", "event": event})
                if self.close_after_events:
                    await ws.close()
                continue
```

`ai/collector/live.py`: Imports anpassen:

```python
from .models import AutomationRun, Change, ServiceCall, parse_utc, utcnow
from .origins import update_origins
```

vor `def seconds_until(` einfügen:

```python
def _entity_ids(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw)
    return ()


def _event_time(event: dict[str, Any]) -> datetime:
    stamp = event.get("time_fired")
    return parse_utc(stamp) if stamp else utcnow()


def service_call_from_event(event: dict[str, Any]) -> ServiceCall | None:
    data = event.get("data") or {}
    if data.get("domain") != "climate" or not data.get("service"):
        return None
    context = event.get("context") or {}
    service_data = data.get("service_data") or {}
    return ServiceCall(
        ts=_event_time(event), domain="climate", service=str(data["service"]),
        entity_ids=_entity_ids(service_data.get("entity_id")),
        data={key: value for key, value in service_data.items() if key != "entity_id"},
        context_id=context.get("id"), user_id=context.get("user_id"), parent_id=context.get("parent_id"),
    )


def automation_run_from_event(event: dict[str, Any]) -> AutomationRun | None:
    data = event.get("data") or {}
    entity_id = data.get("entity_id")
    if not entity_id:
        return None
    context = event.get("context") or {}
    return AutomationRun(_event_time(event), str(entity_id), data.get("name"), context.get("id"))


```

In `run_session` den Block

```python
        stream = await client.subscribe("state_changed")   # vor dem Lückenabgleich, damit nichts fehlt
        await self.gap_fill(client)
        tasks = [
            asyncio.create_task(self._consume(stream)),
```

ersetzen durch:

```python
        stream = await client.subscribe("state_changed")   # vor dem Lückenabgleich, damit nichts fehlt
        call_stream = await client.subscribe("call_service")
        run_stream = await client.subscribe("automation_triggered")
        self.db.mark_capture_start("calls_since", self._now())
        await self.gap_fill(client)
        tasks = [
            asyncio.create_task(self._consume(stream)),
            asyncio.create_task(self._consume_calls(call_stream)),
            asyncio.create_task(self._consume_runs(run_stream)),
```

nach `_consume` einfügen:

```python
    def _tracked_climates(self) -> set[str]:
        return {entity_id for entity_id, rule in self.pipeline.rules.items() if rule.kind == "climate"}

    async def _consume_calls(self, stream) -> None:
        async for event in stream:
            call = service_call_from_event(event)
            if call is None:
                continue
            if call.entity_ids and not self._tracked_climates().intersection(call.entity_ids):
                continue
            try:
                self.db.insert_service_call(call)
            except sqlite3.Error:
                log.exception("Dienstaufruf konnte nicht gespeichert werden")

    async def _consume_runs(self, stream) -> None:
        async for event in stream:
            run = automation_run_from_event(event)
            if run is None:
                continue
            try:
                self.db.insert_automation_run(run)
            except sqlite3.Error:
                log.exception("Automationslauf konnte nicht gespeichert werden")

```

und `_flush` ersetzen durch:

```python
    def _flush(self) -> None:
        try:
            self.pipeline.flush()
        except sqlite3.Error:
            log.exception("Schreiben in die Datenbank fehlgeschlagen, Puffer bleibt erhalten")
        try:
            update_origins(self.db, self._now())
        except sqlite3.Error:
            log.exception("Herkunft der Änderungen konnte nicht berechnet werden")
```

Hinweis: `update_origins` liefert Aufgabe 3. Damit dieser Schritt für sich laufen kann, legt man `ai/collector/origins.py` zunächst mit diesem Rumpf an (wird in Aufgabe 3 ersetzt):

```python
from __future__ import annotations

from datetime import datetime

from .db import Database


def update_origins(db: Database, now: datetime, *, rebuild: bool = False) -> int:
    return 0
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd ai && python -m pytest tests/test_live_events.py::test_service_call_with_entity_list_and_user tests/test_live_events.py::test_service_call_with_single_entity_string tests/test_live_events.py::test_service_call_without_entity_id_keeps_empty_targets tests/test_live_events.py::test_service_calls_of_other_domains_are_ignored tests/test_live_events.py::test_automation_run_from_event tests/test_live.py -v`
Expected: PASS. (`test_run_session_stores_calls_and_runs_and_derives_origin` wird erst nach Aufgabe 3 grün, weil `change_origin` dort befüllt wird.)

- [ ] **Step 5: Commit**

```bash
git add ai/tests/fake_hass.py ai/collector/live.py ai/collector/origins.py ai/tests/test_live_events.py
git commit -m "feat(ai): call_service und automation_triggered im Collector erfassen" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Herkunft je Thermostat-Änderung berechnen

**Files:**
- Modify (ersetzen): `ai/collector/origins.py`
- Modify: `ai/collector/cli.py` (Kommando `origins`)
- Test: `ai/tests/test_origins.py`, `ai/tests/test_origins_cli.py`

**Interfaces:**
- Consumes: Tabellen und Methoden aus Aufgabe 1; `state_changes`, `entities` (Spalte `kind`).
- Produces:
  - `collector.origins.Origin(origin: str, via: str, actor: str | None = None, automation: str | None = None)`
  - `collector.origins.classify_row(db, row, calls_since) -> Origin`
  - `collector.origins.update_origins(db, now: datetime, *, rebuild: bool = False, lookback: timedelta = timedelta(days=2)) -> int` (Zahl der klassifizierten Zeilen; Zeilen jünger als 5 Sekunden warten)
  - Kommando `collector origins [--rebuild]`

- [ ] **Step 1: Failing tests schreiben**

`ai/tests/test_origins.py`:

```python
from datetime import datetime, timedelta, timezone

from collector.db import Database
from collector.models import AutomationRun, Change, EntityRule, ServiceCall, to_utc_iso
from collector.origins import update_origins

T0 = datetime(2026, 10, 15, 11, 0, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=1)
CLIMATE = EntityRule(entity_id="climate.bt", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))


def prepare(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    db.mark_capture_start("calls_since", T0)


def change(db, seconds, temperature, *, ctx=None, user=None, parent=None, source="live"):
    db.insert_changes([Change(T0 + timedelta(seconds=seconds), "climate.bt", "heat",
                              {"temperature": temperature, "preset_mode": "eco"}, ctx, user, parent)], source)


def call(db, seconds, *, ctx=None, user=None, parent=None, entities=("climate.bt",)):
    db.insert_service_call(ServiceCall(T0 + timedelta(seconds=seconds), "climate", "set_temperature",
                                       tuple(entities), {}, ctx, user, parent))


def origin_of(db, seconds):
    row = db.conn.execute(
        "SELECT co.origin, co.via, co.actor, co.automation FROM change_origin co "
        "JOIN state_changes sc ON sc.id = co.state_change_id WHERE sc.ts_utc = ?",
        (to_utc_iso(T0 + timedelta(seconds=seconds)),)).fetchone()
    return None if row is None else tuple(row)


def test_user_click_is_matched_by_context(db):
    prepare(db)
    call(db, 10.0, ctx="c1", user="u1")
    change(db, 10.0004, 21, ctx="c1", user="u1")
    update_origins(db, NOW)
    assert origin_of(db, 10.0004) == ("benutzer", "kontext", "u1", None)


def test_user_click_is_matched_by_time_when_the_state_lost_the_context(db):
    prepare(db)
    call(db, 10.0, ctx="c1", user="u1")
    change(db, 10.3, 21, ctx="other")
    update_origins(db, NOW)
    assert origin_of(db, 10.3) == ("benutzer", "zeit", "u1", None)


def test_automation_call_names_the_automation(db):
    prepare(db)
    db.insert_automation_run(AutomationRun(T0 + timedelta(seconds=19), "automation.hc", "Heating Control", "a1"))
    call(db, 20.0, ctx="a1")
    change(db, 20.01, 18, ctx="a1")
    update_origins(db, NOW)
    assert origin_of(db, 20.01) == ("automation", "kontext", None, "automation.hc")


def test_call_with_parent_is_an_automation_even_with_a_user(db):
    prepare(db)
    call(db, 20.0, ctx="s1", user="u1", parent="p1")
    change(db, 20.01, 18, ctx="s1")
    update_origins(db, NOW)
    assert origin_of(db, 20.01)[0] == "automation"


def test_state_with_user_beats_an_unrelated_automation_call(db):
    prepare(db)
    call(db, 20.0, ctx="a1")
    change(db, 20.4, 18, ctx="x", user="u9")
    update_origins(db, NOW)
    assert origin_of(db, 20.4) == ("benutzer", "zeit", "u9", None)


def test_turning_the_device_has_no_call(db):
    prepare(db)
    change(db, 30, 22)
    update_origins(db, NOW)
    assert origin_of(db, 30) == ("geraet_unklar", "keine", None, None)


def test_calls_for_other_entities_do_not_match(db):
    prepare(db)
    call(db, 30.0, ctx="c2", user="u1", entities=("climate.other",))
    change(db, 30.1, 22)
    update_origins(db, NOW)
    assert origin_of(db, 30.1)[0] == "geraet_unklar"


def test_area_targeted_call_matches_by_time(db):
    prepare(db)
    call(db, 40.0, ctx="c3", user="u1", entities=())
    change(db, 40.2, 23, ctx="y")
    update_origins(db, NOW)
    assert origin_of(db, 40.2) == ("benutzer", "zeit", "u1", None)


def test_rows_from_gaps_without_context_are_unknown(db):
    prepare(db)
    change(db, 50, 20, source="gap:history_api")
    update_origins(db, NOW)
    assert origin_of(db, 50) == ("unbekannt", "keine", None, None)


def test_rows_before_the_capture_started_are_unknown(db):
    db.upsert_entity(CLIMATE, "buero", T0)
    db.mark_capture_start("calls_since", T0 + timedelta(minutes=10))
    change(db, 50, 20)
    update_origins(db, NOW)
    assert origin_of(db, 50)[0] == "unbekannt"


def test_state_context_alone_still_classifies_old_rows(db):
    prepare(db)
    change(db, 60, 20, user="u2", source="backfill:x.db")
    change(db, 70, 21, parent="p9", source="backfill:x.db")
    update_origins(db, NOW)
    assert origin_of(db, 60) == ("benutzer", "kontext", "u2", None)
    assert origin_of(db, 70) == ("automation", "kontext", None, None)


def test_young_rows_wait_for_their_call(db):
    prepare(db)
    change(db, 3598, 20)                       # zwei Sekunden vor NOW
    assert update_origins(db, NOW) == 0
    assert origin_of(db, 3598) is None


def test_second_run_is_idempotent_and_rebuild_reclassifies(db):
    prepare(db)
    change(db, 30, 22)
    assert update_origins(db, NOW) == 1
    assert update_origins(db, NOW) == 0
    call(db, 30.0, ctx="late", user="u2")
    update_origins(db, NOW)
    assert origin_of(db, 30)[0] == "geraet_unklar"
    assert update_origins(db, NOW, rebuild=True) == 1
    assert origin_of(db, 30) == ("benutzer", "zeit", "u2", None)


def test_only_climate_entities_get_an_origin(db):
    prepare(db)
    other = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
    db.upsert_entity(other, None, T0)
    db.insert_changes([Change(T0 + timedelta(seconds=5), "input_boolean.x", "on")], "live")
    update_origins(db, NOW)
    assert db.conn.execute("SELECT COUNT(*) FROM change_origin").fetchone()[0] == 0
```

`ai/tests/test_origins_cli.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector.cli import main
from collector.db import Database
from collector.models import Change, EntityRule

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def test_origins_command_rebuilds(tmp_path, monkeypatch, capsys):
    path = tmp_path / "x.sqlite"
    db = Database(path)
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating", attrs=("temperature",)), "buero", T0)
    db.mark_capture_start("calls_since", T0)
    db.insert_changes([Change(T0 + timedelta(seconds=5), "climate.bt", "heat", {"temperature": 21}, None, "u1")], "live")
    db.close()
    monkeypatch.setenv("HA_URL", "ws://ha.test/api/websocket")
    monkeypatch.setenv("DB_PATH", str(path))
    code = main(["--config", str(ROOT / "config" / "collector.yaml"), "origins", "--rebuild"])
    assert code == 0
    assert "Herkunft berechnet für 1 Zeilen" in capsys.readouterr().out
    db = Database(path)
    assert db.conn.execute("SELECT origin FROM change_origin").fetchone()["origin"] == "benutzer"
    db.close()
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_origins.py tests/test_origins_cli.py -v`
Expected: FAIL (`ImportError` bei `Origin`/leerer Rumpf liefert keine Zeilen; CLI kennt `origins` nicht).

- [ ] **Step 3: Implementieren**

`ai/collector/origins.py` komplett ersetzen:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from .db import Database
from .models import parse_utc, to_utc_iso

MATCH_WINDOW_S = 1.0      # Dienstaufruf und Zustandszeile liegen höchstens so weit auseinander
SETTLE_S = 5.0            # junge Zeilen warten, bis ihr Aufruf sicher gespeichert ist


@dataclass(frozen=True)
class Origin:
    origin: str           # benutzer | automation | geraet_unklar | unbekannt
    via: str              # kontext | zeit | keine
    actor: str | None = None
    automation: str | None = None


def _calls_around(db: Database, ts: datetime, entity_id: str) -> list:
    low = to_utc_iso(ts - timedelta(seconds=MATCH_WINDOW_S))
    high = to_utc_iso(ts + timedelta(seconds=MATCH_WINDOW_S))
    rows = db.conn.execute(
        "SELECT * FROM service_calls WHERE ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc", (low, high)).fetchall()
    result = []
    for row in rows:
        targets = json.loads(row["entity_ids"] or "[]")
        if targets and entity_id not in targets:
            continue
        result.append(row)
    return result


def _automation_for(db: Database, call) -> str | None:
    for context in (call["context_id"], call["parent_id"]):
        if not context:
            continue
        row = db.conn.execute(
            "SELECT automation_entity FROM automation_runs WHERE context_id = ? ORDER BY ts_utc LIMIT 1",
            (context,)).fetchone()
        if row is not None:
            return row["automation_entity"]
    return None


def _from_call(db: Database, call, via: str) -> Origin:
    if call["parent_id"]:
        return Origin("automation", via, None, _automation_for(db, call))
    if call["user_id"]:
        return Origin("benutzer", via, call["user_id"])
    return Origin("automation", via, None, _automation_for(db, call))


def classify_row(db: Database, row, calls_since: datetime | None) -> Origin:
    ts = parse_utc(row["ts_utc"])
    calls = _calls_around(db, ts, row["entity_id"])
    by_context = [call for call in calls if row["context_id"] and call["context_id"] == row["context_id"]]
    if by_context:
        found: Origin | None = _from_call(db, by_context[0], "kontext")
    elif calls:
        nearest = min(calls, key=lambda call: abs((parse_utc(call["ts_utc"]) - ts).total_seconds()))
        found = _from_call(db, nearest, "zeit")
    else:
        found = None
    if found is not None:
        if found.origin == "automation" and row["context_user_id"] and not row["context_parent_id"]:
            return Origin("benutzer", found.via, row["context_user_id"])
        return found
    if row["context_parent_id"]:
        return Origin("automation", "kontext")
    if row["context_user_id"]:
        return Origin("benutzer", "kontext", row["context_user_id"])
    if row["source"] == "live" and calls_since is not None and ts >= calls_since:
        return Origin("geraet_unklar", "keine")
    return Origin("unbekannt", "keine")


def update_origins(db: Database, now: datetime, *, rebuild: bool = False,
                   lookback: timedelta = timedelta(days=2)) -> int:
    """Berechnet die Herkunft für Thermostat-Zeilen ohne Eintrag; `rebuild` rechnet alle neu."""
    calls_since = db.capture_start("calls_since")
    if rebuild:
        db.conn.execute("DELETE FROM change_origin")
    sql = ("SELECT sc.* FROM state_changes sc JOIN entities e ON e.entity_id = sc.entity_id "
           "LEFT JOIN change_origin co ON co.state_change_id = sc.id "
           "WHERE e.kind = 'climate' AND co.state_change_id IS NULL AND sc.ts_utc <= ?")
    params: list = [to_utc_iso(now - timedelta(seconds=SETTLE_S))]
    if not rebuild:
        sql += " AND sc.ts_utc >= ?"
        params.append(to_utc_iso(now - lookback))
    rows = db.conn.execute(sql + " ORDER BY sc.ts_utc", params).fetchall()
    for row in rows:
        origin = classify_row(db, row, calls_since)
        db.conn.execute(
            "INSERT OR REPLACE INTO change_origin(state_change_id, origin, via, actor, automation) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["id"], origin.origin, origin.via, origin.actor, origin.automation))
    db.conn.commit()
    return len(rows)
```

`ai/collector/cli.py`: Import ergänzen

```python
from .models import parse_utc, utcnow
from .origins import update_origins
```

Im Parser nach `purge.add_argument("--yes", ...)`:

```python
    origins = sub.add_parser("origins", help="Herkunft der Thermostat-Änderungen berechnen (nach einem Backfill neu rechnen)")
    origins.add_argument("--rebuild", action="store_true", help="alle Zeilen neu berechnen")
```

Neue Funktion vor `main`:

```python
def _cmd_origins(cfg: Config, args: argparse.Namespace) -> int:
    db = Database(cfg.database)
    try:
        count = update_origins(db, utcnow(), rebuild=args.rebuild)
    finally:
        db.close()
    print(f"Herkunft berechnet für {count} Zeilen")
    return 0
```

und in `main` vor `return _cmd_purge(cfg, args)`:

```python
        if args.command == "origins":
            return _cmd_origins(cfg, args)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_origins.py tests/test_origins_cli.py tests/test_live_events.py tests/test_live.py tests/test_cli.py -v`
Expected: PASS (alle, auch `test_run_session_stores_calls_and_runs_and_derives_origin`).

- [ ] **Step 5: Commit**

```bash
git add ai/collector/origins.py ai/collector/cli.py ai/tests/test_origins.py ai/tests/test_origins_cli.py
git commit -m "feat(ai): Herkunft der Thermostat-Aenderungen aus Dienstaufrufen berechnen" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `manual_actions` und Eingriffe nutzen die Herkunft

**Files:**
- Modify: `ai/sql/schema.sql` (View `manual_actions`)
- Modify: `ai/analysis/interventions.py`
- Test: `ai/tests/test_origin_view.py`

**Interfaces:**
- Consumes: `change_origin` (Aufgabe 1/3), View `manual_actions` (bestehend).
- Produces: View-Spalten `change_id` (Zeilen-ID aus `state_changes`), `origin_automation`; `actor_class` kennt zusätzlich `'device'` (Herkunft `geraet_unklar`); ohne `change_origin`-Zeile bleibt das bisherige Verhalten (`manual`/`automation`/`unknown` aus dem Kontext). `load_interventions` zählt `actor_class IN ('manual', 'device')`.

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_origin_view.py`:

```python
import json
from datetime import datetime, timedelta, timezone

from analysis.interventions import load_interventions
from collector.models import Change, EntityRule, to_utc_iso

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
CLIMATE = EntityRule(entity_id="climate.bt", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))


def add(db, seconds, temperature, origin=None, actor=None, automation=None, user=None, parent=None):
    ts = T0 + timedelta(seconds=seconds)
    db.insert_changes([Change(ts, "climate.bt", "heat", {"temperature": temperature, "preset_mode": "eco"},
                              f"c{seconds}", user, parent)], "live")
    if origin is not None:
        sid = db.conn.execute("SELECT id FROM state_changes WHERE ts_utc = ?", (to_utc_iso(ts),)).fetchone()["id"]
        db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via, actor, automation) "
                        "VALUES (?, ?, 'kontext', ?, ?)", (sid, origin, actor, automation))
        db.conn.commit()


def rows(db):
    return db.conn.execute("SELECT change_id, actor_class, actor_user_id, origin_automation, new "
                           "FROM manual_actions ORDER BY ts_utc").fetchall()


def test_origin_overrides_the_context_heuristic(db):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, 17)
    add(db, 600, 18, origin="geraet_unklar")
    add(db, 1200, 19, origin="automation", automation="automation.hc", user="u1")
    add(db, 1800, 20, origin="benutzer", actor="u7")
    add(db, 2400, 21, origin="unbekannt")
    result = rows(db)
    assert [r["actor_class"] for r in result] == ["device", "automation", "manual", "unknown"]
    assert [r["actor_user_id"] for r in result] == [None, "u1", "u7", None]
    assert result[1]["origin_automation"] == "automation.hc"
    assert all(r["change_id"] for r in result)


def test_rows_without_origin_keep_the_old_behaviour(db):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, 17)
    add(db, 60, 18, user="u1")
    assert [r["actor_class"] for r in rows(db)] == ["manual"]


def test_interventions_include_devices_but_not_automations(db, acfg):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, 17)
    add(db, 600, 18, origin="geraet_unklar")
    add(db, 1200, 21, origin="automation", automation="automation.hc")
    add(db, 1800, 19, origin="benutzer", actor="u1")
    found = load_interventions(db, acfg)
    assert [(item.kind, item.value, item.user_id) for item in found] == [
        ("setpoint", 18.0, None), ("setpoint", 19.0, "u1")]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_origin_view.py -v`
Expected: FAIL mit `sqlite3.OperationalError: no such column: change_id`.

- [ ] **Step 3: Implementieren**

In `ai/sql/schema.sql` drei Ersetzungen im View:

1. Zeile `  SELECT sc.ts_utc, sc.entity_id, e.domain,` ersetzen durch:

```sql
  SELECT sc.id AS change_id, sc.ts_utc, sc.entity_id, e.domain,
```

2. Den Block

```sql
         sc.context_user_id, sc.context_parent_id
  FROM state_changes sc
  JOIN entities e ON e.entity_id = sc.entity_id
  WHERE e.kind = 'climate'
```

ersetzen durch:

```sql
         sc.context_user_id, sc.context_parent_id,
         co.origin AS origin, co.actor AS origin_actor, co.automation AS origin_automation
  FROM state_changes sc
  JOIN entities e ON e.entity_id = sc.entity_id
  LEFT JOIN change_origin co ON co.state_change_id = sc.id
  WHERE e.kind = 'climate'
```

3. Den Block ab `SELECT ts_utc, domain, room, entity_id, changed AS changed_attrs,` bis zum Ende ersetzen durch:

```sql
SELECT change_id, ts_utc, domain, room, entity_id, changed AS changed_attrs,
       json_object('temperature', old_temp, 'preset_mode', old_preset, 'hvac_mode', old_state) AS old,
       json_object('temperature', new_temp, 'preset_mode', new_preset, 'hvac_mode', new_state) AS new,
       CASE origin
         WHEN 'benutzer' THEN 'manual'
         WHEN 'automation' THEN 'automation'
         WHEN 'geraet_unklar' THEN 'device'
         WHEN 'unbekannt' THEN 'unknown'
         ELSE CASE WHEN context_parent_id IS NOT NULL THEN 'automation'
                   WHEN context_user_id IS NOT NULL THEN 'manual'
                   ELSE 'unknown' END
       END AS actor_class,
       COALESCE(origin_actor, context_user_id) AS actor_user_id,
       origin_automation
FROM diff
WHERE changed <> '';
```

In `ai/analysis/interventions.py` die Abfrage ersetzen:

```python
        "SELECT ts_utc, room, entity_id, changed_attrs, new, actor_user_id FROM manual_actions "
        "WHERE actor_class IN ('manual', 'device') ORDER BY entity_id, ts_utc")
```

- [ ] **Step 4: Tests laufen lassen (gesamte Suite)**

Run: `cd ai && python -m pytest -q`
Expected: PASS (alle bisherigen Tests unverändert grün, neue Tests grün).

- [ ] **Step 5: Commit**

```bash
git add ai/sql/schema.sql ai/analysis/interventions.py ai/tests/test_origin_view.py
git commit -m "feat(ai): manual_actions und Eingriffe nutzen die Herkunft (Geraet zaehlt, Automation nie)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 5: `heating_control` V4.29 mit der Eingabe `ki_rules_toggle`

**Files:**
- Modify: `heating_control.yaml` (Name und neue Eingabe)
- Create: `ai/tests/yaml_blueprint.py` (Lader für `!input`)
- Test: `ai/tests/test_blueprints.py`

**Interfaces:**
- Consumes: bestehende Blueprint-Datei `heating_control.yaml` (V4.28).
- Produces:
  - Blueprint-Eingabe `ki_rules_toggle` (Entität, Domäne `input_boolean`, Standard `[]`), Name `Heizungssteuerung (V4.29)`; Verhalten unverändert.
  - `tests.yaml_blueprint.load_blueprint(path: Path) -> dict` (liest YAML mit `yaml.safe_load`; jedes `!input name` wird vorher zum Text `@@input:name`), `tests.yaml_blueprint.resolve(value, inputs: dict)` (ersetzt diese Texte rekursiv durch die Werte aus `inputs`).

- [ ] **Step 1: Failing test schreiben**

`ai/tests/yaml_blueprint.py`:

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

MARKER = "@@input:"


def load_blueprint(path: Path) -> dict:
    """Liest einen Blueprint ohne eigenen YAML-Lader: `!input name` wird zum Text `@@input:name`."""
    text = re.sub(r"!input\s+(\w+)", rf'"{MARKER}\1"', Path(path).read_text(encoding="utf-8"))
    return yaml.safe_load(text)


def resolve(value: Any, inputs: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith(MARKER):
        return inputs[value[len(MARKER):]]
    if isinstance(value, dict):
        return {key: resolve(item, inputs) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, inputs) for item in value]
    return value
```

`ai/tests/test_blueprints.py`:

```python
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_blueprints.py -v`
Expected: FAIL mit `KeyError: 'ki_rules_toggle'` (und Name noch V4.28).

- [ ] **Step 3: Implementieren**

In `heating_control.yaml` Zeile 2: `  name: Heizungssteuerung (V4.28)` ersetzen durch `  name: Heizungssteuerung (V4.29)`.

Die Zeile `    # Optionaler täglicher Reset` ersetzen durch:

```yaml
    ki_rules_toggle:
      name: (Optional) KI-Regeln erlaubt (Schalter)
      description: >
        Optional - Input Boolean, der die selbstlernenden KI-Regeln für diesen Heizkörper erlaubt (an) oder in den
        Schattenmodus zwingt (aus). Diese Automation wertet den Schalter nicht aus; er ordnet dem Heizkörper
        seinen KI-Schalter zu und wird vom Programm "execution deploy" gelesen.
      default: []
      selector:
        entity:
          domain: input_boolean
          multiple: false

    # Optionaler täglicher Reset
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_blueprints.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add heating_control.yaml ai/tests/yaml_blueprint.py ai/tests/test_blueprints.py
git commit -m "feat: heating_control V4.29 mit optionaler Eingabe ki_rules_toggle" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Blueprint `ki_regel.yaml` mit getesteter Entscheidungslogik

**Files:**
- Create: `blueprints/ki_regel.yaml`
- Create: `ai/tests/ha_template.py` (Auswertung der HA-Templates mit Jinja2)
- Modify: `ai/requirements-dev.txt` (+ `jinja2`)
- Test: `ai/tests/test_ki_blueprint.py`

**Interfaces:**
- Consumes: `tests.yaml_blueprint.load_blueprint`, `resolve` (Aufgabe 5).
- Produces:
  - Blueprint `blueprints/ki_regel.yaml` mit den Eingaben `rule_id`, `heating_entity`, `room_toggle`, `rule_toggle`, `stop_toggle`, `flat_state`, `blocked_states`, `winter_toggle`, `heating_automation`, `last_intervention`, `min_distance_min`, `setpoint_min`, `setpoint_max`, `delay_min`, `action_kind`, `action_preset`, `action_setpoint`, `rule_triggers`, `rule_conditions`.
  - Die Aktion mit `alias: Entscheidung` setzt die Variablen `hvac_now`, `preset_now`, `temp_now`, `target_value`, `unreachable`, `state_blocked`, `already_there`, `since_intervention_min`, `too_close`, `switches_on`, `reason`, `will_act`, `mode`, `payload`; `reason` ist einer von `nicht_erreichbar`, `wohnungszustand`, `bereits_erreicht`, `handeingriff`, `raum_aus`, `schatten` oder `''`.
  - `payload` (JSON) mit `ts`, `rule_id`, `room`, `mode` (`aktiv`|`schatten`), `result` (`ausgefuehrt`|`nicht_ausgefuehrt`), `reason`, `target {kind, value}`, `current {preset, temperature, hvac_mode}`; gesendet an `homeai/rule/<rule_id>/fired`.
  - `tests.ha_template.HaEnv(states, attrs, now)` mit `render_variables(block: dict, context: dict) -> dict` und `render(template: str, context: dict)`.

- [ ] **Step 1: Failing tests schreiben**

`ai/requirements-dev.txt` ergänzen um die Zeile `jinja2>=3.1,<4`.

`ai/tests/ha_template.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2.nativetypes import NativeEnvironment


def _as_timestamp(value: Any, default: Any = None) -> Any:
    if isinstance(value, datetime):
        return value.timestamp()
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except ValueError:
            return default


class HaEnv:
    """Wertet HA-Templates mit Jinja2 aus (nur die hier benutzten Funktionen)."""

    def __init__(self, states: dict[str, str], attrs: dict[str, dict], now: datetime):
        self.states = states
        self.attrs = attrs
        self.now = now
        self.env = NativeEnvironment()
        self.env.globals.update(
            states=lambda entity: self.states.get(entity, "unknown"),
            state_attr=lambda entity, name: self.attrs.get(entity, {}).get(name),
            is_state=lambda entity, value: self.states.get(entity, "unknown") == value,
            now=lambda: self.now,
            as_timestamp=_as_timestamp,
        )
        self.env.filters["as_timestamp"] = _as_timestamp
        self.env.filters["to_json"] = lambda value: json.dumps(value, ensure_ascii=False)

    def render(self, template: Any, context: dict[str, Any]) -> Any:
        if not isinstance(template, str):
            return template
        return self.env.from_string(template).render(**context)

    def render_variables(self, block: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Rendert die Einträge der Reihe nach; spätere sehen die früheren (wie das HA-Schritt `variables`)."""
        local = dict(context)
        result: dict[str, Any] = {}
        for name, template in block.items():
            local[name] = result[name] = self.render(template, local)
        return result


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def minutes_ago(now: datetime, minutes: float) -> float:
    return (now - timedelta(minutes=minutes)).timestamp()
```

`ai/tests/test_ki_blueprint.py`:

```python
import json
from datetime import timedelta
from pathlib import Path

import pytest

from tests.ha_template import HaEnv, minutes_ago, utc
from tests.yaml_blueprint import load_blueprint, resolve

BLUEPRINT = Path(__file__).resolve().parents[2] / "blueprints" / "ki_regel.yaml"
NOW = utc(2026, 10, 15, 6, 30)

INPUTS = dict(
    rule_id="r5", heating_entity="climate.bt", room_toggle="input_boolean.room", rule_toggle="switch.rule",
    stop_toggle="input_boolean.stop", flat_state="input_select.flat", blocked_states=["Vacation"],
    winter_toggle=[], heating_automation="automation.hc", last_intervention="input_datetime.li",
    min_distance_min=60, setpoint_min=15, setpoint_max=25, delay_min=0, action_kind="preset",
    action_preset="comfort", action_setpoint=21, rule_triggers=[], rule_conditions=[],
)


def decide(*, states=None, attrs=None, **inputs):
    blueprint = load_blueprint(BLUEPRINT)
    values = {**INPUTS, **inputs}
    base_states = {"input_boolean.room": "on", "switch.rule": "on", "input_boolean.stop": "off",
                   "input_select.flat": "Home", "climate.bt": "heat"}
    base_attrs = {"climate.bt": {"preset_mode": "eco", "temperature": 18.0},
                  "input_datetime.li": {"timestamp": minutes_ago(NOW, 180)},
                  "automation.hc": {"current": 0, "last_triggered": None}}
    env = HaEnv({**base_states, **(states or {})}, {**base_attrs, **(attrs or {})}, NOW)
    context = resolve(blueprint["variables"], values)
    step = next(step for step in blueprint["actions"] if step.get("alias") == "Entscheidung")
    result = env.render_variables(resolve(step["variables"], values), context)
    payload = result["payload"]
    result["payload"] = json.loads(payload) if isinstance(payload, str) else payload
    return result


def test_rule_acts_when_everything_is_on():
    result = decide()
    assert result["reason"] == "" and result["will_act"] is True and result["mode"] == "aktiv"
    assert result["payload"]["result"] == "ausgefuehrt"
    assert result["payload"]["target"] == {"kind": "preset", "value": "comfort"}
    assert result["payload"]["current"] == {"preset": "eco", "temperature": 18.0, "hvac_mode": "heat"}
    assert result["payload"]["room"] == "climate.bt" and result["payload"]["rule_id"] == "r5"


@pytest.mark.parametrize("states,reason,mode", [
    ({"switch.rule": "off"}, "schatten", "schatten"),
    ({"input_boolean.stop": "on"}, "schatten", "schatten"),
    ({"input_boolean.room": "off"}, "raum_aus", "schatten"),
])
def test_switches_decide_between_shadow_and_active(states, reason, mode):
    result = decide(states=states)
    assert (result["reason"], result["mode"], result["will_act"]) == (reason, mode, False)
    assert result["payload"]["result"] == "nicht_ausgefuehrt"


def test_unset_rule_switch_means_shadow():
    assert decide(states={"switch.rule": "unknown"})["reason"] == "schatten"


@pytest.mark.parametrize("flat", ["Home", "Away", "Sleep", "Coming Home", "Guest Mode"])
def test_only_vacation_is_blocked_by_default(flat):
    assert decide(states={"input_select.flat": flat})["reason"] == ""


def test_vacation_and_winter_mode_off_block_the_rule():
    assert decide(states={"input_select.flat": "Vacation"})["reason"] == "wohnungszustand"
    result = decide(winter_toggle="input_boolean.winter", states={"input_boolean.winter": "off"})
    assert result["reason"] == "wohnungszustand"
    assert decide(winter_toggle="input_boolean.winter", states={"input_boolean.winter": "on"})["reason"] == ""


def test_blocked_states_are_configurable():
    assert decide(blocked_states=["Vacation", "Sleep"], states={"input_select.flat": "Sleep"})["reason"] == "wohnungszustand"


def test_preset_already_set():
    result = decide(attrs={"climate.bt": {"preset_mode": "comfort", "temperature": 21.0}})
    assert result["reason"] == "bereits_erreicht"


def test_setpoint_already_set_and_clamped():
    already = decide(action_kind="setpoint", action_setpoint=21, attrs={"climate.bt": {"temperature": 21.0}})
    assert already["reason"] == "bereits_erreicht"
    high = decide(action_kind="setpoint", action_setpoint=30)
    assert high["target_value"] == 25.0 and high["payload"]["target"]["value"] == 25.0
    low = decide(action_kind="setpoint", action_setpoint=10)
    assert low["target_value"] == 15.0


@pytest.mark.parametrize("minutes,reason", [(20, "handeingriff"), (59, "handeingriff"), (60, ""), (61, "")])
def test_minimum_distance_to_the_last_intervention(minutes, reason):
    result = decide(attrs={"input_datetime.li": {"timestamp": minutes_ago(NOW, minutes)}})
    assert result["reason"] == reason


def test_other_distance_is_configurable():
    attrs = {"input_datetime.li": {"timestamp": minutes_ago(NOW, 20)}}
    assert decide(attrs=attrs, min_distance_min=10)["reason"] == ""


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_unreachable_thermostat_is_not_touched(state):
    result = decide(states={"climate.bt": state})
    assert (result["reason"], result["will_act"]) == ("nicht_erreichbar", False)


def test_unreachable_wins_over_everything_else():
    result = decide(states={"climate.bt": "unavailable", "switch.rule": "off", "input_select.flat": "Vacation"})
    assert result["reason"] == "nicht_erreichbar"


def test_shadow_reports_what_it_would_have_done():
    result = decide(states={"switch.rule": "off"}, action_kind="setpoint", action_setpoint=22)
    assert result["payload"]["target"] == {"kind": "setpoint", "value": 22.0}
    assert result["payload"]["mode"] == "schatten"


def test_blueprint_structure():
    blueprint = load_blueprint(BLUEPRINT)
    assert set(blueprint["blueprint"]["input"]) == set(INPUTS)
    assert blueprint["blueprint"]["domain"] == "automation"
    assert blueprint["mode"] == "single"
    names = [step.get("alias") for step in blueprint["actions"]]
    assert names == ["Verzögerung", "Auf heating_control warten", "Bedingungen erneut prüfen", "Entscheidung",
                     "Ausführen", "Meldung"]
    dumped = json.dumps(blueprint["actions"], default=str)
    assert "climate.set_preset_mode" in dumped and "climate.set_temperature" in dumped
    assert "homeai/rule/{{ rule_id }}/fired" in dumped
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && pip install -r requirements-dev.txt && python -m pytest tests/test_ki_blueprint.py -v`
Expected: FAIL mit `FileNotFoundError` für `blueprints/ki_regel.yaml`.

- [ ] **Step 3: Blueprint schreiben**

`blueprints/ki_regel.yaml`:

```yaml
blueprint:
  name: KI-Regel (V1.0)
  description: >
    Eine vom Programm "execution deploy" angelegte Regel der KI-Analyse für einen Heizkörper.
    Die Regel setzt ein Preset oder einen Sollwert, wenn ihr Auslöser feuert, nur wenn Raumschalter und Regelschalter
    an sind, der Sammelschalter nicht gesetzt ist, der Wohnungszustand nicht gesperrt ist und der letzte Eingriff
    mindestens den Mindestabstand zurückliegt. Sonst meldet sie nur, was sie getan hätte (Schattenmodus).
    Jede Auslösung wird auf homeai/rule/<id>/fired gemeldet.
  domain: automation
  source_url: https://github.com/fabsss/heating_control/blob/main/blueprints/ki_regel.yaml
  homeassistant:
    min_version: 2024.10.0
  input:
    rule_id:
      name: Regel-ID
      selector:
        text: {}
    heating_entity:
      name: Heizkörper
      selector:
        entity:
          domain: climate
    room_toggle:
      name: Raumschalter (KI-Regeln erlaubt)
      selector:
        entity:
          domain: input_boolean
    rule_toggle:
      name: Regelschalter (aktiv, sonst Schatten)
      selector:
        entity:
          domain: switch
    stop_toggle:
      name: Sammelschalter (alle Regeln in Schatten)
      selector:
        entity:
          domain: input_boolean
    flat_state:
      name: Wohnungszustand
      selector:
        entity:
          domain: input_select
    blocked_states:
      name: Gesperrte Wohnungszustände
      default:
        - Vacation
      selector:
        select:
          multiple: true
          custom_value: true
          options: []
    winter_toggle:
      name: (Optional) Heizung An/Aus (Winter-Modus)
      default: []
      selector:
        entity:
          domain: input_boolean
          multiple: false
    heating_automation:
      name: Automation von heating_control für diesen Raum
      selector:
        entity:
          domain: automation
    last_intervention:
      name: Zeitpunkt des letzten Eingriffs
      selector:
        entity:
          domain: input_datetime
    min_distance_min:
      name: Mindestabstand nach einem Eingriff (Minuten)
      default: 60
      selector:
        number:
          min: 0
          max: 720
          step: 5
          unit_of_measurement: min
    setpoint_min:
      name: Kleinster erlaubter Sollwert
      default: 15
      selector:
        number:
          min: 5
          max: 30
          step: 0.5
          unit_of_measurement: "°C"
    setpoint_max:
      name: Größter erlaubter Sollwert
      default: 25
      selector:
        number:
          min: 5
          max: 30
          step: 0.5
          unit_of_measurement: "°C"
    delay_min:
      name: Verzögerung nach dem Auslöser (Minuten)
      default: 0
      selector:
        number:
          min: 0
          max: 120
          unit_of_measurement: min
    action_kind:
      name: Art der Aktion
      default: preset
      selector:
        select:
          options:
            - preset
            - setpoint
    action_preset:
      name: Preset
      default: ""
      selector:
        text: {}
    action_setpoint:
      name: Sollwert
      default: 21
      selector:
        number:
          min: 5
          max: 30
          step: 0.5
          unit_of_measurement: "°C"
    rule_triggers:
      name: Auslöser
      selector:
        trigger: {}
    rule_conditions:
      name: Bedingungen
      default: []
      selector:
        condition: {}

mode: single
max_exceeded: silent

variables:
  rule_id: !input rule_id
  heating_entity: !input heating_entity
  room_toggle: !input room_toggle
  rule_toggle: !input rule_toggle
  stop_toggle: !input stop_toggle
  flat_state: !input flat_state
  blocked_states: !input blocked_states
  winter_toggle: !input winter_toggle
  heating_automation: !input heating_automation
  last_intervention: !input last_intervention
  min_distance_min: !input min_distance_min
  setpoint_min: !input setpoint_min
  setpoint_max: !input setpoint_max
  delay_min: !input delay_min
  action_kind: !input action_kind
  action_preset: !input action_preset
  action_setpoint: !input action_setpoint

triggers: !input rule_triggers
conditions: !input rule_conditions

actions:
  - alias: Verzögerung
    if:
      - condition: template
        value_template: "{{ (delay_min | int(0)) > 0 }}"
    then:
      - delay:
          minutes: "{{ delay_min | int(0) }}"

  - alias: Auf heating_control warten
    if:
      - condition: template
        value_template: >-
          {{ (state_attr(heating_automation, 'current') | int(0)) > 0
             or (as_timestamp(now()) - (state_attr(heating_automation, 'last_triggered') | as_timestamp(0))) < 120 }}
    then:
      - wait_template: "{{ (state_attr(heating_automation, 'current') | int(0)) == 0 }}"
        timeout: "00:02:00"
        continue_on_timeout: true
      - delay: "00:00:05"

  - alias: Bedingungen erneut prüfen
    condition: and
    conditions: !input rule_conditions

  - alias: Entscheidung
    variables:
      hvac_now: "{{ states(heating_entity) }}"
      preset_now: "{{ state_attr(heating_entity, 'preset_mode') }}"
      temp_now: "{{ state_attr(heating_entity, 'temperature') }}"
      target_value: >-
        {{ action_preset if action_kind == 'preset'
           else ([[action_setpoint | float(0), setpoint_min | float(15)] | max, setpoint_max | float(25)] | min) }}
      unreachable: "{{ hvac_now in ['unavailable', 'unknown'] }}"
      state_blocked: >-
        {{ states(flat_state) in blocked_states
           or (winter_toggle != [] and is_state(winter_toggle, 'off')) }}
      already_there: >-
        {{ (preset_now == action_preset) if action_kind == 'preset'
           else (temp_now is not none and ((temp_now | float(0)) - (target_value | float(0))) | abs < 0.01) }}
      since_intervention_min: >-
        {{ ((as_timestamp(now()) - (state_attr(last_intervention, 'timestamp') | float(0))) / 60) | round(1) }}
      too_close: "{{ since_intervention_min < (min_distance_min | float(60)) }}"
      switches_on: >-
        {{ is_state(room_toggle, 'on') and is_state(rule_toggle, 'on') and is_state(stop_toggle, 'off') }}
      reason: >-
        {{ 'nicht_erreichbar' if unreachable
           else 'wohnungszustand' if state_blocked
           else 'bereits_erreicht' if already_there
           else 'handeingriff' if too_close
           else 'raum_aus' if is_state(room_toggle, 'off')
           else 'schatten' if not switches_on
           else '' }}
      will_act: "{{ reason == '' }}"
      mode: "{{ 'aktiv' if switches_on else 'schatten' }}"
      payload: >-
        {{ {'ts': now().isoformat(), 'rule_id': rule_id, 'room': heating_entity, 'mode': mode,
            'result': 'ausgefuehrt' if will_act else 'nicht_ausgefuehrt', 'reason': reason,
            'target': {'kind': action_kind, 'value': target_value},
            'current': {'preset': preset_now, 'temperature': temp_now, 'hvac_mode': hvac_now}} | to_json }}

  - alias: Ausführen
    if:
      - condition: template
        value_template: "{{ will_act }}"
    then:
      - choose:
          - conditions:
              - condition: template
                value_template: "{{ action_kind == 'preset' }}"
            sequence:
              - action: climate.set_preset_mode
                continue_on_error: true
                target:
                  entity_id: "{{ heating_entity }}"
                data:
                  preset_mode: "{{ action_preset }}"
        default:
          - if:
              - condition: template
                value_template: "{{ hvac_now == 'off' }}"
            then:
              - action: climate.set_hvac_mode
                continue_on_error: true
                target:
                  entity_id: "{{ heating_entity }}"
                data:
                  hvac_mode: heat
              - wait_template: "{{ states(heating_entity) != 'off' }}"
                timeout: "00:00:10"
                continue_on_timeout: true
              - action: climate.set_temperature
                continue_on_error: true
                target:
                  entity_id: "{{ heating_entity }}"
                data:
                  temperature: "{{ target_value }}"
              - action: climate.set_hvac_mode
                continue_on_error: true
                target:
                  entity_id: "{{ heating_entity }}"
                data:
                  hvac_mode: "{{ hvac_now }}"
            else:
              - action: climate.set_temperature
                continue_on_error: true
                target:
                  entity_id: "{{ heating_entity }}"
                data:
                  temperature: "{{ target_value }}"

  - alias: Meldung
    action: mqtt.publish
    data:
      topic: "homeai/rule/{{ rule_id }}/fired"
      payload: "{{ payload }}"
      qos: 1
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_ki_blueprint.py -v`
Expected: PASS. Schlägt ein Test der Template-Auswertung wegen eines Unterschieds zwischen Jinja2 und Home Assistant fehl (zum Beispiel ein nicht vorhandener Filter), ist der Fehler in `ai/tests/ha_template.py` zu beheben, nicht im Blueprint, solange das Template in Home Assistant gültig ist.

- [ ] **Step 5: Commit**

```bash
git add blueprints/ki_regel.yaml ai/tests/ha_template.py ai/tests/test_ki_blueprint.py ai/requirements-dev.txt
git commit -m "feat: Blueprint ki_regel mit getesteter Entscheidungslogik" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Helfer und Hilfsautomationen für den Mindestabstand

**Files:**
- Create: `ai/execution/__init__.py` (leer), `ai/execution/__main__.py` (Rumpf, wird in Aufgabe 16 gefüllt)
- Create: `ai/execution/helpers.py`
- Test: `ai/tests/test_helpers.py`

**Interfaces:**
- Consumes: `tests.ha_template.HaEnv` (Aufgabe 6).
- Produces:
  - `execution.helpers.STOP_NAME = "ki_regeln_alle_schatten"`
  - `execution.helpers.slug(text: str) -> str`
  - `execution.helpers.HelperNames(toggle: str, last_intervention: str, last_auto: str)` und `helper_names(key: str) -> HelperNames` (`ki_regeln_<key>`, `ki_eingriff_<key>`, `ki_aufruf_<key>`)
  - `execution.helpers.helper_specs(key: str) -> list[tuple[str, str, dict]]` (Domäne, Name, Optionen für `<domain>/create`)
  - `execution.helpers.eingriff_automations(key: str, climate_entity: str) -> list[tuple[str, dict]]` (Konfigurations-ID, Automation): `homeai_aufrufe_<key>` und `homeai_beobachter_<key>`.

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_helpers.py`:

```python
from execution.helpers import HelperNames, eingriff_automations, helper_names, helper_specs, slug
from tests.ha_template import HaEnv, utc

NOW = utc(2026, 10, 15, 12, 0)
CLIMATE = "climate.better_thermostat_bathroom"


def test_slug_and_names():
    assert slug("Küche & Bad") == "k_che_bad"
    assert helper_names("Bad") == HelperNames("ki_regeln_bad", "ki_eingriff_bad", "ki_aufruf_bad")


def test_helper_specs_cover_toggle_and_two_datetimes():
    specs = helper_specs("bad")
    assert [(domain, name) for domain, name, _ in specs] == [
        ("input_boolean", "ki_regeln_bad"), ("input_datetime", "ki_eingriff_bad"), ("input_datetime", "ki_aufruf_bad")]
    assert specs[1][2]["has_date"] is True and specs[1][2]["has_time"] is True


def automations():
    return dict(eingriff_automations("bad", CLIMATE))


def test_two_automations_with_stable_ids():
    assert list(automations()) == ["homeai_aufrufe_bad", "homeai_beobachter_bad"]


def trigger_event(entity_ids, *, user=None, parent=None):
    return {"event": {"data": {"domain": "climate", "service": "set_temperature",
                               "service_data": {} if entity_ids is None else {"entity_id": entity_ids}},
                      "context": {"user_id": user, "parent_id": parent}}}


def call_condition(entity_ids):
    config = automations()["homeai_aufrufe_bad"]
    env = HaEnv({}, {}, NOW)
    return env.render(config["conditions"][0]["value_template"], {"trigger": trigger_event(entity_ids)})


def test_call_automation_matches_list_and_string_targets_only_for_its_thermostat():
    assert call_condition([CLIMATE]) is True
    assert call_condition(CLIMATE) is True
    assert call_condition(["climate.other"]) is False
    assert call_condition(None) is False


def route(user, parent):
    config = automations()["homeai_aufrufe_bad"]
    branch = config["actions"][0]["choose"][0]
    env = HaEnv({}, {}, NOW)
    is_user = env.render(branch["conditions"][0]["value_template"], {"trigger": trigger_event([CLIMATE], user=user, parent=parent)})
    return branch["sequence"][0]["target"]["entity_id"] if is_user else config["actions"][0]["default"][0]["target"]["entity_id"]


def test_user_calls_set_the_intervention_stamp_and_automation_calls_the_other():
    assert route("u1", None) == "input_datetime.ki_eingriff_bad"
    assert route(None, None) == "input_datetime.ki_aufruf_bad"
    assert route("u1", "p1") == "input_datetime.ki_aufruf_bad"
    assert route(None, "p1") == "input_datetime.ki_aufruf_bad"


def observer_condition(old, new):
    config = automations()["homeai_beobachter_bad"]
    env = HaEnv({}, {}, NOW)
    trigger = {"from_state": old, "to_state": new}
    return env.render(config["conditions"][0]["value_template"], {"trigger": trigger})


def snap(state="heat", temperature=21.0, preset="comfort"):
    return {"state": state, "attributes": {"temperature": temperature, "preset_mode": preset}}


def test_observer_reacts_to_setpoint_preset_and_mode_changes_only():
    assert observer_condition(snap(), snap(temperature=22.0)) is True
    assert observer_condition(snap(), snap(preset="eco")) is True
    assert observer_condition(snap(), snap(state="off")) is True
    assert observer_condition(snap(), snap()) is False
    attributes = {"state": "heat", "attributes": {"temperature": 21.0, "preset_mode": "comfort", "window_open": True}}
    assert observer_condition(snap(), attributes) is False


def test_observer_ignores_unavailable_transitions_and_missing_old_state():
    assert observer_condition(snap(state="unavailable"), snap()) is False
    assert observer_condition(snap(), snap(state="unknown", temperature=1.0)) is False
    assert observer_condition(None, snap()) is False


def observer_decides_hand(minutes_since_intervention, minutes_since_auto):
    config = automations()["homeai_beobachter_bad"]
    env = HaEnv({}, {
        "input_datetime.ki_eingriff_bad": {"timestamp": (NOW.timestamp() - minutes_since_intervention * 60)},
        "input_datetime.ki_aufruf_bad": {"timestamp": (NOW.timestamp() - minutes_since_auto * 60)},
    }, NOW)
    return env.render(config["actions"][1]["value_template"], {})


def test_observer_only_marks_a_hand_intervention_without_a_recent_call():
    assert observer_decides_hand(60, 60) is True
    assert observer_decides_hand(0.05, 60) is False          # Nutzeraufruf vor 3 Sekunden
    assert observer_decides_hand(60, 0.05) is False          # Automationsaufruf vor 3 Sekunden
    assert observer_decides_hand(60, 0.2) is True            # Aufruf vor 12 Sekunden zählt nicht mehr


def test_observer_waits_three_seconds_and_writes_the_intervention_stamp():
    config = automations()["homeai_beobachter_bad"]
    assert config["actions"][0] == {"delay": {"seconds": 3}}
    write = config["actions"][2]
    assert write["action"] == "input_datetime.set_datetime"
    assert write["target"]["entity_id"] == "input_datetime.ki_eingriff_bad"
    assert config["mode"] == "queued"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_helpers.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution'`.

- [ ] **Step 3: Implementieren**

`ai/execution/__init__.py`: leere Datei.

`ai/execution/__main__.py`:

```python
import sys

from .cli import main

sys.exit(main())
```

`ai/execution/cli.py` (Rumpf, damit das Paket importierbar bleibt; wird in Aufgabe 13/16 gefüllt):

```python
from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    raise SystemExit("execution: noch nicht eingerichtet")
```

`ai/execution/helpers.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass

STOP_NAME = "ki_regeln_alle_schatten"
WAIT_S = 3
CALL_MEMORY_S = 6


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass(frozen=True)
class HelperNames:
    toggle: str              # input_boolean: KI-Regeln erlaubt (Raumschalter)
    last_intervention: str   # input_datetime: letzter Eingriff von Benutzer oder Gerät
    last_auto: str           # input_datetime: letzter Aufruf einer Automation


def helper_names(key: str) -> HelperNames:
    key = slug(key)
    return HelperNames(f"ki_regeln_{key}", f"ki_eingriff_{key}", f"ki_aufruf_{key}")


def helper_specs(key: str) -> list[tuple[str, str, dict]]:
    names = helper_names(key)
    return [
        ("input_boolean", names.toggle, {"icon": "mdi:robot"}),
        ("input_datetime", names.last_intervention, {"has_date": True, "has_time": True, "icon": "mdi:hand-back-right"}),
        ("input_datetime", names.last_auto, {"has_date": True, "has_time": True, "icon": "mdi:robot-outline"}),
    ]


def _stamp(entity: str) -> dict:
    return {"action": "input_datetime.set_datetime", "target": {"entity_id": entity},
            "data": {"timestamp": "{{ as_timestamp(now()) }}"}}


def eingriff_automations(key: str, climate_entity: str) -> list[tuple[str, dict]]:
    """Zwei Hilfsautomationen je Raum: Aufrufe merken und Eingriffe am Gerät erkennen (läuft ohne Container)."""
    names = helper_names(key)
    intervention = f"input_datetime.{names.last_intervention}"
    auto = f"input_datetime.{names.last_auto}"
    calls = {
        "alias": f"KI Aufrufe {slug(key)}",
        "description": "Merkt, ob ein Dienstaufruf an den Heizkörper von einem Benutzer oder einer Automation kam.",
        "mode": "queued",
        "max": 20,
        "triggers": [{"trigger": "event", "event_type": "call_service", "event_data": {"domain": "climate"}}],
        "conditions": [{"condition": "template", "value_template": (
            "{% set ids = trigger.event.data.service_data.entity_id | default([], true) %}"
            "{{ '" + climate_entity + "' in ([ids] if ids is string else ids) }}")}],
        "actions": [{"choose": [{
            "conditions": [{"condition": "template", "value_template": (
                "{{ trigger.event.context.user_id is not none and trigger.event.context.parent_id is none }}")}],
            "sequence": [_stamp(intervention)]}],
            "default": [_stamp(auto)]}],
    }
    observer = {
        "alias": f"KI Beobachter {slug(key)}",
        "description": "Erkennt Änderungen am Heizkörper ohne zugehörigen Dienstaufruf (Drehen am Gerät) als Eingriff.",
        "mode": "queued",
        "max": 10,
        "triggers": [{"trigger": "state", "entity_id": climate_entity}],
        "conditions": [{"condition": "template", "value_template": (
            "{{ trigger.from_state is not none"
            " and trigger.from_state.state not in ['unavailable', 'unknown']"
            " and trigger.to_state.state not in ['unavailable', 'unknown']"
            " and (trigger.from_state.state != trigger.to_state.state"
            " or trigger.from_state.attributes.get('temperature') != trigger.to_state.attributes.get('temperature')"
            " or trigger.from_state.attributes.get('preset_mode') != trigger.to_state.attributes.get('preset_mode')) }}")}],
        "actions": [
            {"delay": {"seconds": WAIT_S}},
            {"condition": "template", "value_template": (
                f"{{{{ as_timestamp(now()) - state_attr('{intervention}', 'timestamp') | float(0) > {CALL_MEMORY_S}"
                f" and as_timestamp(now()) - state_attr('{auto}', 'timestamp') | float(0) > {CALL_MEMORY_S} }}}}")},
            _stamp(intervention),
        ],
    }
    return [(f"homeai_aufrufe_{slug(key)}", calls), (f"homeai_beobachter_{slug(key)}", observer)]
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_helpers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution ai/tests/test_helpers.py
git commit -m "feat(ai): Helfer und Hilfsautomationen fuer den Mindestabstand" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Konfiguration, Tabellen und Speicher der Ausführung

**Files:**
- Create: `ai/execution/config.py`, `ai/execution/models.py`, `ai/execution/store.py`
- Create: `ai/sql/execution.sql`, `ai/config/execution.yaml`
- Test: `ai/tests/test_execution_config.py`, `ai/tests/test_execution_store.py`

**Interfaces:**
- Consumes: `collector.db.Database`, `collector.models.to_utc_iso`, `parse_utc`.
- Produces:
  - `execution.config.ExecutionConfig` (Felder siehe unten), `RoomConfig(area, key, heating_automation)`, `MqttConfig(host, port, user, password)`, `ExecutionConfigError`, `load_execution_config(path, env=None) -> ExecutionConfig`, `ExecutionConfig.require_token() -> str`.
  - `execution.models.Deployment`, `RuleEvent`, `Stats` (Felder siehe unten).
  - `execution.store.ExecStore(db)` mit: `add_deployment(d)`, `get_deployment(rule_id) -> Deployment | None`, `list_deployments(include_removed=False) -> list[Deployment]`, `set_deployment_state(rule_id, state, now)`, `add_event(event) -> bool`, `pending_events() -> list[RuleEvent]`, `save_outcome(event_id, outcome, window_start, window_end, lage=None, abstand_min=None, deviation=None)`, `stats_rows(rule_id) -> list[sqlite3.Row]`, `start_job(name, params, now) -> int`, `finish_job(job_id, state, message, now)`, `recent_jobs(limit=10)`, `count_jobs_since(name, since: datetime) -> int`.

- [ ] **Step 1: Failing tests schreiben**

`ai/tests/test_execution_config.py`:

```python
from pathlib import Path

import pytest

from execution.config import ExecutionConfigError, load_execution_config

ROOT = Path(__file__).resolve().parent.parent
ENV = {"HA_URL": "ws://ha.test:8123/api/websocket", "HA_WRITE_TOKEN": "tok", "MQTT_HOST": "broker.test",
       "MQTT_USER": "u", "MQTT_PASSWORD": "p"}


def test_shipped_config_loads_with_defaults():
    cfg = load_execution_config(ROOT / "config" / "execution.yaml", ENV)
    assert cfg.ha_url == "ws://ha.test:8123/api/websocket" and cfg.require_token() == "tok"
    assert cfg.mqtt.host == "broker.test" and cfg.mqtt.port == 1883 and cfg.mqtt.user == "u"
    assert cfg.min_distance_min == 60 and cfg.blocked_states == ("Vacation",)
    assert (cfg.setpoint_min, cfg.setpoint_max) == (15.0, 25.0)
    assert (cfg.eval_before_min, cfg.eval_after_min, cfg.target_tolerance) == (60, 90, 0.5)
    assert (cfg.min_scored, cfg.max_fail_rate, cfg.llm_runs_per_day) == (10, 0.2, 3)
    assert cfg.blueprint_path == "homeai/ki_regel.yaml" and cfg.dashboard_path == "ki-regeln"
    rooms = {room.key: room for room in cfg.rooms.values()}
    assert set(rooms) == {"bad", "schlafzimmer", "wohnzimmer", "bastelzimmer"}
    assert rooms["bad"].heating_automation == "automation.heating_control_bathroom"
    assert cfg.rooms["office"].key == "bastelzimmer"


def test_mqtt_is_optional_and_token_required_on_demand():
    cfg = load_execution_config(ROOT / "config" / "execution.yaml", {"HA_URL": "ws://x/api/websocket"})
    assert cfg.mqtt is None
    with pytest.raises(ExecutionConfigError, match="HA_WRITE_TOKEN"):
        cfg.require_token()


def test_missing_url_and_bad_rooms_are_rejected(tmp_path):
    with pytest.raises(ExecutionConfigError, match="HA_URL"):
        load_execution_config(ROOT / "config" / "execution.yaml", {})
    broken = tmp_path / "x.yaml"
    broken.write_text("rooms:\n  a: {key: bad}\n", encoding="utf-8")
    with pytest.raises(ExecutionConfigError, match="heating_automation"):
        load_execution_config(broken, ENV)
    twice = tmp_path / "y.yaml"
    twice.write_text("rooms:\n  a: {key: bad, heating_automation: automation.a}\n"
                     "  b: {key: bad, heating_automation: automation.b}\n", encoding="utf-8")
    with pytest.raises(ExecutionConfigError, match="doppelt"):
        load_execution_config(twice, ENV)
```

`ai/tests/test_execution_store.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from execution.models import Deployment, RuleEvent
from execution.store import ExecStore

T0 = datetime(2026, 10, 15, 6, 30, tzinfo=timezone.utc)


@pytest.fixture
def store(db):
    return ExecStore(db)


def deployment(rule_id="r5", state="schatten"):
    return Deployment(rule_id, 5, "bad", "automation.ki_regel_r5", "homeai_rule_r5", "climate.bt", state,
                      "1.0", "Mo-Fr 06:30 Komfort", "2026-10-15T06:00:00.000000+00:00")


def event(ts="2026-10-15T06:30:00.000000+00:00", rule_id="r5", reason=""):
    return RuleEvent(rule_id, ts, "schatten", "nicht_ausgefuehrt", reason, {"kind": "preset", "value": "comfort"},
                     {"preset": "eco"}, "{}")


def test_deployment_roundtrip_and_state(store):
    store.add_deployment(deployment())
    assert store.get_deployment("r5").description == "Mo-Fr 06:30 Komfort"
    store.set_deployment_state("r5", "aktiv", T0)
    assert store.get_deployment("r5").state == "aktiv"
    store.set_deployment_state("r5", "entfernt", T0)
    assert store.get_deployment("r5").removed_at is not None
    assert store.list_deployments() == []
    assert [d.rule_id for d in store.list_deployments(include_removed=True)] == ["r5"]
    assert store.get_deployment("zz") is None


def test_events_are_deduplicated_and_pending_until_an_outcome_exists(store):
    assert store.add_event(event()) is True
    assert store.add_event(event()) is False
    (pending,) = store.pending_events()
    assert pending.target == {"kind": "preset", "value": "comfort"} and pending.current_state == {"preset": "eco"}
    store.save_outcome(pending.id, "neutral", T0 - timedelta(hours=1), T0 + timedelta(hours=2))
    assert store.pending_events() == []
    (row,) = store.stats_rows("r5")
    assert (row["outcome"], row["reason"], row["result"]) == ("neutral", "", "nicht_ausgefuehrt")


def test_jobs_are_counted_per_day(store):
    first = store.start_job("llm_run", {"mode": "scan"}, T0)
    store.finish_job(first, "ok", "fertig", T0)
    store.start_job("scan", {}, T0)
    assert store.count_jobs_since("llm_run", T0 - timedelta(hours=1)) == 1
    assert store.count_jobs_since("llm_run", T0 + timedelta(hours=1)) == 0
    assert [job["name"] for job in store.recent_jobs()] == ["scan", "llm_run"]
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_execution_config.py tests/test_execution_store.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.config'`.

- [ ] **Step 3: Implementieren**

`ai/config/execution.yaml`:

```yaml
# Freigabe und Ausführung (Teilprojekt 3). Keine Zugangsdaten:
# HA_WRITE_TOKEN und MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD kommen aus der Umgebung.

# area_id (wie in room_map) -> Schlüssel für die Helfer und Automation von heating_control des Raums.
# Alles Weitere (Heizkörper, Wohnungszustand, Zeitplan, KI-Schalter) liest "execution deploy" aus dieser Automation.
rooms:
  a05f1cd02cf34142af915331271a6986: {key: bad, heating_automation: automation.heating_control_bathroom}
  ee532a5799b5440691fdcc87e3354f9b: {key: schlafzimmer, heating_automation: automation.heating_control_bedroom}
  25752d3793e84768941596a2c9579f36: {key: wohnzimmer, heating_automation: automation.heating_control_living_room}
  office: {key: bastelzimmer, heating_automation: automation.heating_control_office}

blueprint_path: homeai/ki_regel.yaml     # Pfad unter /config/blueprints/automation/
dashboard_path: ki-regeln

rule:
  min_distance_min: 60                   # Mindestabstand nach einem Eingriff
  blocked_states: [Vacation]             # Wohnungszustände, in denen eine Regel nichts setzt
  setpoint_min: 15
  setpoint_max: 25

evaluation:
  before_min: 60                         # Auswertungsfenster vor der Auslösung
  after_min: 90                          # und danach
  target_tolerance: 0.5                  # Sollwerte gelten bis zu diesem Abstand (°C) als gleich
  min_scored: 10                         # gewertete Auslösungen für das Signal "grün"
  max_fail_rate: 0.2                     # Fehlauslösungs- bzw. Korrekturquote für "grün"

jobs:
  llm_runs_per_day: 3
  arm_seconds: 60                        # Dauer der Kosten-Freigabe
  output_limit: 12000                    # Zeichen je Ergebnistext im Dashboard
```

`ai/execution/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ExecutionConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RoomConfig:
    area: str
    key: str
    heating_automation: str


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int = 1883
    user: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class ExecutionConfig:
    ha_url: str
    ha_token: str | None
    rooms: dict[str, RoomConfig]
    mqtt: MqttConfig | None = None
    timezone: str = "Europe/Berlin"
    blueprint_path: str = "homeai/ki_regel.yaml"
    dashboard_path: str = "ki-regeln"
    min_distance_min: int = 60
    blocked_states: tuple[str, ...] = ("Vacation",)
    setpoint_min: float = 15.0
    setpoint_max: float = 25.0
    eval_before_min: int = 60
    eval_after_min: int = 90
    target_tolerance: float = 0.5
    min_scored: int = 10
    max_fail_rate: float = 0.2
    llm_runs_per_day: int = 3
    arm_seconds: int = 60
    job_output_limit: int = 12000

    def require_token(self) -> str:
        if not self.ha_token:
            raise ExecutionConfigError("Umgebungsvariable HA_WRITE_TOKEN fehlt (Long-Lived Access Token mit Schreibrechten)")
        return self.ha_token

    def room_by_key(self, key: str) -> RoomConfig:
        for room in self.rooms.values():
            if room.key == key:
                return room
        raise ExecutionConfigError(f"Unbekannter Raum {key!r} (bekannt: {', '.join(r.key for r in self.rooms.values())})")


def load_execution_config(path: str | Path, env: dict[str, str] | None = None) -> ExecutionConfig:
    env = os.environ if env is None else env
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    url = env.get("HA_URL") or (raw.get("home_assistant") or {}).get("url")
    if not url:
        raise ExecutionConfigError("HA_URL fehlt (Umgebungsvariable oder home_assistant.url)")
    rooms: dict[str, RoomConfig] = {}
    for area, item in (raw.get("rooms") or {}).items():
        if not isinstance(item, dict) or "key" not in item or "heating_automation" not in item:
            raise ExecutionConfigError(f"rooms.{area}: key und heating_automation sind nötig")
        rooms[str(area)] = RoomConfig(str(area), str(item["key"]), str(item["heating_automation"]))
    if not rooms:
        raise ExecutionConfigError("rooms fehlt")
    keys = [room.key for room in rooms.values()]
    if len(set(keys)) != len(keys):
        raise ExecutionConfigError("rooms: Schlüssel doppelt vergeben")
    host = env.get("MQTT_HOST")
    mqtt = MqttConfig(host, int(env.get("MQTT_PORT") or 1883), env.get("MQTT_USER") or None,
                      env.get("MQTT_PASSWORD") or None) if host else None
    rule, evaluation, jobs = raw.get("rule") or {}, raw.get("evaluation") or {}, raw.get("jobs") or {}
    return ExecutionConfig(
        ha_url=url, ha_token=env.get("HA_WRITE_TOKEN") or None, rooms=rooms, mqtt=mqtt,
        timezone=str(raw.get("timezone", "Europe/Berlin")),
        blueprint_path=str(raw.get("blueprint_path", "homeai/ki_regel.yaml")),
        dashboard_path=str(raw.get("dashboard_path", "ki-regeln")),
        min_distance_min=int(rule.get("min_distance_min", 60)),
        blocked_states=tuple(rule.get("blocked_states", ["Vacation"])),
        setpoint_min=float(rule.get("setpoint_min", 15)), setpoint_max=float(rule.get("setpoint_max", 25)),
        eval_before_min=int(evaluation.get("before_min", 60)), eval_after_min=int(evaluation.get("after_min", 90)),
        target_tolerance=float(evaluation.get("target_tolerance", 0.5)),
        min_scored=int(evaluation.get("min_scored", 10)), max_fail_rate=float(evaluation.get("max_fail_rate", 0.2)),
        llm_runs_per_day=int(jobs.get("llm_runs_per_day", 3)), arm_seconds=int(jobs.get("arm_seconds", 60)),
        job_output_limit=int(jobs.get("output_limit", 12000)),
    )
```

`ai/execution/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Deployment:
    rule_id: str
    candidate_id: int
    room: str                       # area_id
    ha_automation: str              # automation.<...>
    config_id: str
    climate_entity: str
    state: str                      # schatten | aktiv | entfernt
    blueprint_version: str
    description: str
    created_at: str
    removed_at: str | None = None


@dataclass(frozen=True)
class RuleEvent:
    rule_id: str
    ts_utc: str
    mode: str                       # aktiv | schatten
    result: str                     # ausgefuehrt | nicht_ausgefuehrt
    reason: str
    target: dict
    current_state: dict
    payload: str
    id: int | None = None


@dataclass(frozen=True)
class Stats:
    events: int
    scored: int
    good: int
    treffer: int
    fehlausloesung: int
    neutral: int
    bestaetigt: int
    korrigiert: int
    unterbrochen: int
    ohne_wirkung: int
    ausgebremst: int
    reasons: dict = field(default_factory=dict)
    trefferquote: float | None = None
    korrekturquote: float | None = None
    median_abstand_min: float | None = None
    anteil_vorher: float | None = None
    abweichung: str | None = None
    reife: str = "zu_wenig_daten"       # gruen | rot | zu_wenig_daten
    text: str = ""

    def to_payload(self) -> dict:
        def pct(value):
            return None if value is None else round(100 * value, 1)
        return {
            "events": self.events, "scored": self.scored, "good": self.good, "treffer": self.treffer,
            "fehlausloesung": self.fehlausloesung, "neutral": self.neutral, "bestaetigt": self.bestaetigt,
            "korrigiert": self.korrigiert, "unterbrochen": self.unterbrochen, "ohne_wirkung": self.ohne_wirkung,
            "ausgebremst": self.ausgebremst, "reasons": self.reasons,
            "trefferquote_pct": pct(self.trefferquote), "korrekturquote_pct": pct(self.korrekturquote),
            "median_abstand_min": self.median_abstand_min, "anteil_vorher_pct": pct(self.anteil_vorher),
            "abweichung": self.abweichung, "reife": self.reife, "text": self.text,
        }
```

`ai/sql/execution.sql`:

```sql
CREATE TABLE IF NOT EXISTS deployments(
  rule_id           TEXT PRIMARY KEY,
  candidate_id      INTEGER NOT NULL,
  room              TEXT NOT NULL,                -- area_id
  ha_automation     TEXT NOT NULL,
  config_id         TEXT NOT NULL,
  climate_entity    TEXT NOT NULL,
  state             TEXT NOT NULL,                -- schatten|aktiv|entfernt
  blueprint_version TEXT NOT NULL,
  description       TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  removed_at        TEXT
);

CREATE TABLE IF NOT EXISTS rule_events(
  id            INTEGER PRIMARY KEY,
  rule_id       TEXT NOT NULL,
  ts_utc        TEXT NOT NULL,
  mode          TEXT NOT NULL,                    -- aktiv|schatten
  result        TEXT NOT NULL,                    -- ausgefuehrt|nicht_ausgefuehrt
  reason        TEXT NOT NULL DEFAULT '',
  target        TEXT NOT NULL,                    -- JSON
  current_state TEXT NOT NULL,                    -- JSON
  payload       TEXT NOT NULL,
  UNIQUE(rule_id, ts_utc)
);
CREATE INDEX IF NOT EXISTS idx_rule_events_rule ON rule_events(rule_id, ts_utc);

CREATE TABLE IF NOT EXISTS rule_outcomes(
  rule_event_id INTEGER PRIMARY KEY,
  outcome       TEXT NOT NULL,                    -- treffer|fehlausloesung|neutral|bestaetigt|korrigiert|unterbrochen|ohne_wirkung|nicht_gewertet
  lage          TEXT,                             -- vorher|nachher
  abstand_min   REAL,
  window_start  TEXT NOT NULL,
  window_end    TEXT NOT NULL,
  deviation     TEXT
);

CREATE TABLE IF NOT EXISTS jobs(
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  params      TEXT NOT NULL DEFAULT '{}',
  state       TEXT NOT NULL,                      -- laeuft|ok|fehler
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  message     TEXT
);
```

`ai/execution/store.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from collector.db import Database
from collector.models import to_utc_iso

from .models import Deployment, RuleEvent

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "execution.sql"


def _deployment(row) -> Deployment:
    return Deployment(row["rule_id"], row["candidate_id"], row["room"], row["ha_automation"], row["config_id"],
                      row["climate_entity"], row["state"], row["blueprint_version"], row["description"],
                      row["created_at"], row["removed_at"])


def _event(row) -> RuleEvent:
    return RuleEvent(row["rule_id"], row["ts_utc"], row["mode"], row["result"], row["reason"],
                     json.loads(row["target"]), json.loads(row["current_state"]), row["payload"], row["id"])


class ExecStore:
    def __init__(self, db: Database):
        self.conn = db.conn
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    # --- Regeln -------------------------------------------------------------------

    def add_deployment(self, d: Deployment) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO deployments(rule_id, candidate_id, room, ha_automation, config_id, climate_entity, "
            "state, blueprint_version, description, created_at, removed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.rule_id, d.candidate_id, d.room, d.ha_automation, d.config_id, d.climate_entity, d.state,
             d.blueprint_version, d.description, d.created_at, d.removed_at))
        self.conn.commit()

    def get_deployment(self, rule_id: str) -> Deployment | None:
        row = self.conn.execute("SELECT * FROM deployments WHERE rule_id = ?", (rule_id,)).fetchone()
        return None if row is None else _deployment(row)

    def list_deployments(self, *, include_removed: bool = False) -> list[Deployment]:
        sql = "SELECT * FROM deployments" + ("" if include_removed else " WHERE state <> 'entfernt'") + " ORDER BY rule_id"
        return [_deployment(row) for row in self.conn.execute(sql)]

    def set_deployment_state(self, rule_id: str, state: str, now: datetime) -> None:
        self.conn.execute(
            "UPDATE deployments SET state = ?, removed_at = ? WHERE rule_id = ?",
            (state, to_utc_iso(now) if state == "entfernt" else None, rule_id))
        self.conn.commit()

    # --- Ereignisse und Ergebnisse -------------------------------------------------

    def add_event(self, event: RuleEvent) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO rule_events(rule_id, ts_utc, mode, result, reason, target, current_state, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event.rule_id, event.ts_utc, event.mode, event.result, event.reason,
             json.dumps(event.target, ensure_ascii=False), json.dumps(event.current_state, ensure_ascii=False),
             event.payload))
        self.conn.commit()
        return cursor.rowcount > 0

    def pending_events(self) -> list[RuleEvent]:
        rows = self.conn.execute(
            "SELECT e.* FROM rule_events e LEFT JOIN rule_outcomes o ON o.rule_event_id = e.id "
            "WHERE o.rule_event_id IS NULL ORDER BY e.ts_utc")
        return [_event(row) for row in rows]

    def save_outcome(self, event_id: int, outcome: str, window_start: datetime, window_end: datetime, *,
                     lage: str | None = None, abstand_min: float | None = None, deviation: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO rule_outcomes(rule_event_id, outcome, lage, abstand_min, window_start, window_end, deviation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, outcome, lage, abstand_min, to_utc_iso(window_start), to_utc_iso(window_end), deviation))
        self.conn.commit()

    def recent_events(self, rule_id: str, limit: int = 10) -> list[RuleEvent]:
        rows = self.conn.execute(
            "SELECT * FROM rule_events WHERE rule_id = ? ORDER BY ts_utc DESC LIMIT ?", (rule_id, limit))
        return [_event(row) for row in rows]

    def stats_rows(self, rule_id: str) -> list:
        return self.conn.execute(
            "SELECT e.id, e.mode, e.result, e.reason, e.target, o.outcome, o.lage, o.abstand_min, o.deviation "
            "FROM rule_events e LEFT JOIN rule_outcomes o ON o.rule_event_id = e.id WHERE e.rule_id = ? "
            "ORDER BY e.ts_utc", (rule_id,)).fetchall()

    # --- Aufträge ------------------------------------------------------------------

    def start_job(self, name: str, params: dict, now: datetime) -> int:
        cursor = self.conn.execute(
            "INSERT INTO jobs(name, params, state, started_at) VALUES (?, ?, 'laeuft', ?)",
            (name, json.dumps(params, ensure_ascii=False), to_utc_iso(now)))
        self.conn.commit()
        return cursor.lastrowid

    def finish_job(self, job_id: int, state: str, message: str, now: datetime) -> None:
        self.conn.execute("UPDATE jobs SET state = ?, finished_at = ?, message = ? WHERE id = ?",
                          (state, to_utc_iso(now), message, job_id))
        self.conn.commit()

    def recent_jobs(self, limit: int = 10) -> list:
        return self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def count_jobs_since(self, name: str, since: datetime) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE name = ? AND started_at >= ?", (name, to_utc_iso(since))).fetchone()[0]
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_execution_config.py tests/test_execution_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/config.py ai/execution/models.py ai/execution/store.py ai/sql/execution.sql ai/config/execution.yaml ai/tests/test_execution_config.py ai/tests/test_execution_store.py
git commit -m "feat(ai): Konfiguration, Tabellen und Speicher der Ausfuehrung" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Kandidat in Blueprint-Eingaben übersetzen

**Files:**
- Create: `ai/execution/translate.py`
- Test: `ai/tests/test_translate.py`

**Interfaces:**
- Consumes: `analysis.models.Candidate` (Felder `room`, `trigger`, `conditions`, `action`), Tabellen `signals(handle, entity_id, kind, value_kind)` und `entities(entity_id, kind, room, active)`.
- Produces:
  - `execution.translate.TranslateError(ValueError)`
  - `execution.translate.SignalRef(handle: str, entity_id: str, kind: str, value_kind: str)`
  - `execution.translate.RuleInputs(triggers: list[dict], conditions: list[dict], delay_min: int, action_kind: str, action_preset: str, action_setpoint: float)`
  - `execution.translate.translate(candidate, signals: dict[str, SignalRef], *, setpoint_min: float, setpoint_max: float) -> RuleInputs` (`conditions` ist nie leer)
  - `execution.translate.load_signal_refs(db) -> dict[str, SignalRef]`
  - `execution.translate.climate_for_room(db, area: str) -> str` (die eine aktive `climate`-Entität des Raums)

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_translate.py`:

```python
import pytest

from analysis.models import Candidate
from collector.models import EntityRule
from execution.translate import (RuleInputs, SignalRef, TranslateError, climate_for_room, load_signal_refs,
                                 translate)
from tests.ha_template import utc

SIGNALS = {
    "sig_01": SignalRef("sig_01", "person.fabian", "person", "categorical"),
    "sig_02": SignalRef("sig_02", "sensor.pixel_9a_next_alarm", "sensor", "timestamp"),
    "sig_03": SignalRef("sig_03", "sensor.outside_temp", "sensor", "numeric"),
    "sig_04": SignalRef("sig_04", "input_select.flat_occupation_state", "flat", "categorical"),
}
LIMITS = dict(setpoint_min=15.0, setpoint_max=25.0)
PRESET = {"type": "preset", "value": "comfort"}


def rule(trigger, conditions=None, action=PRESET):
    return Candidate(room="bad", trigger=trigger, conditions=conditions or [], action=action)


def run(trigger, conditions=None, action=PRESET) -> RuleInputs:
    return translate(rule(trigger, conditions, action), SIGNALS, **LIMITS)


TRUE = {"condition": "template", "value_template": "{{ true }}"}


def test_time_trigger_with_weekdays():
    result = run({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "06:30"})
    assert result.triggers == [{"trigger": "time", "at": "06:30:00"}]
    assert result.conditions == [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}]
    assert (result.action_kind, result.action_preset, result.delay_min) == ("preset", "comfort", 0)


def test_daily_time_trigger_needs_no_weekday_condition_but_never_has_empty_conditions():
    result = run({"type": "time", "weekdays": [0, 1, 2, 3, 4, 5, 6], "time": "17:00"})
    assert result.conditions == [TRUE]


def test_person_arrival_after_a_long_absence():
    result = run({"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home",
                  "min_prev_min": 180, "offset_min": 0})
    assert result.triggers == [{"trigger": "state", "entity_id": "person.fabian", "to": "home",
                                "not_from": ["unavailable", "unknown"]}]
    (condition,) = result.conditions
    assert condition["condition"] == "template"
    assert "trigger.from_state is not none" in condition["value_template"]
    assert ">= 180" in condition["value_template"] and "< " not in condition["value_template"]


def test_person_leaving_means_from_home():
    result = run({"type": "signal", "signal": "sig_01", "event": "to_state", "value": "away", "offset_min": 0})
    assert result.triggers == [{"trigger": "state", "entity_id": "person.fabian", "from": "home",
                                "not_to": ["unavailable", "unknown"]}]


def test_duration_range_and_positive_offset_become_condition_and_delay():
    result = run({"type": "signal", "signal": "sig_04", "event": "to_state", "value": "Home",
                  "min_prev_min": 45, "max_prev_min": 180, "offset_min": 15})
    assert result.delay_min == 15
    template = result.conditions[0]["value_template"]
    assert ">= 45" in template and "< 180" in template


def test_alarm_time_uses_the_timestamp_sensor_with_a_signed_offset():
    result = run({"type": "signal", "signal": "sig_02", "event": "alarm_time", "offset_min": -30})
    assert result.triggers == [{"trigger": "time", "at": "sensor.pixel_9a_next_alarm", "offset": "-00:30:00"}]
    assert result.delay_min == 0
    later = run({"type": "signal", "signal": "sig_02", "event": "alarm_time", "offset_min": 90})
    assert later.triggers[0]["offset"] == "01:30:00"
    plain = run({"type": "signal", "signal": "sig_02", "event": "alarm_time", "offset_min": 0})
    assert "offset" not in plain.triggers[0]


def test_numeric_thresholds():
    up = run({"type": "signal", "signal": "sig_03", "event": "rises_above", "value": 18.5, "offset_min": 10})
    assert up.triggers == [{"trigger": "numeric_state", "entity_id": "sensor.outside_temp", "above": 18.5}]
    assert up.delay_min == 10
    down = run({"type": "signal", "signal": "sig_03", "event": "falls_below", "value": 8, "offset_min": 0})
    assert down.triggers[0]["below"] == 8.0


def test_conditions_are_translated_in_order():
    result = run(
        {"type": "time", "weekdays": [5, 6], "time": "09:00"},
        [{"type": "weekdays", "weekdays": [5]},
         {"type": "time_window", "from": "08:00", "to": "12:00"},
         {"type": "signal_state", "signal": "sig_04", "op": "is_not", "value": "Away"},
         {"type": "signal_range", "signal": "sig_03", "op": "below", "value": 8}])
    assert result.conditions == [
        {"condition": "time", "weekday": ["sat", "sun"]},
        {"condition": "time", "weekday": ["sat"]},
        {"condition": "time", "after": "08:00:00", "before": "12:00:00"},
        {"condition": "not", "conditions": [
            {"condition": "state", "entity_id": "input_select.flat_occupation_state", "state": "Away"}]},
        {"condition": "numeric_state", "entity_id": "sensor.outside_temp", "below": 8.0},
    ]


def test_person_state_conditions():
    away = run({"type": "time", "weekdays": [0], "time": "07:00"},
               [{"type": "signal_state", "signal": "sig_01", "op": "is", "value": "away"}])
    assert away.conditions[-1] == {"condition": "not", "conditions": [
        {"condition": "state", "entity_id": "person.fabian", "state": "home"}]}
    home = run({"type": "time", "weekdays": [0], "time": "07:00"},
               [{"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"}])
    assert home.conditions[-1] == {"condition": "state", "entity_id": "person.fabian", "state": "home"}


def test_setpoint_action():
    result = run({"type": "time", "weekdays": [0], "time": "07:00"}, action={"type": "setpoint", "value": 21.5})
    assert (result.action_kind, result.action_setpoint) == ("setpoint", 21.5)


@pytest.mark.parametrize("trigger,match", [
    ({"type": "event", "event": "person_arrives", "person": "person.fabian"}, "alte"),
    ({"type": "signal", "signal": "sig_99", "event": "to_state", "value": "x"}, "sig_99"),
    ({"type": "nonsense"}, "Auslöser"),
])
def test_unsupported_triggers_are_rejected_with_a_clear_message(trigger, match):
    with pytest.raises(TranslateError, match=match):
        run(trigger)


def test_legacy_conditions_and_bad_actions_are_rejected():
    time = {"type": "time", "weekdays": [0], "time": "07:00"}
    with pytest.raises(TranslateError, match="alte"):
        run(time, [{"type": "outside_temp", "op": "below", "value": 8}])
    with pytest.raises(TranslateError, match="Sollwert"):
        run(time, action={"type": "setpoint", "value": 30.0})
    with pytest.raises(TranslateError, match="Preset"):
        run(time, action={"type": "preset", "value": ""})
    with pytest.raises(TranslateError, match="Aktion"):
        run(time, action={"type": "dance"})


def test_signal_refs_and_climate_lookup_come_from_the_database(db, store):
    db.upsert_entity(EntityRule("climate.better_thermostat_bathroom", "climate", "heating"), "bad", utc(2026, 1, 1))
    db.upsert_entity(EntityRule("climate.better_thermostat_office", "climate", "heating"), "office", utc(2026, 1, 1))
    handles = store.assign_handles(["person.fabian"])
    store.update_signal(handles["person.fabian"], "person", "categorical", "Anwesenheit person_1")
    refs = load_signal_refs(db)
    assert refs["sig_01"] == SignalRef("sig_01", "person.fabian", "person", "categorical")
    assert climate_for_room(db, "bad") == "climate.better_thermostat_bathroom"
    with pytest.raises(TranslateError, match="Heizkörper"):
        climate_for_room(db, "keller")
    db.upsert_entity(EntityRule("climate.second", "climate", "heating"), "bad", utc(2026, 1, 1))
    with pytest.raises(TranslateError, match="mehrere"):
        climate_for_room(db, "bad")
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_translate.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.translate'`.

- [ ] **Step 3: Implementieren**

`ai/execution/translate.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from analysis.models import Candidate
from collector.db import Database

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
UNUSABLE = ["unavailable", "unknown"]
LEGACY = ("Der Kandidat nutzt das alte Vokabular ({what}); angelegt werden nur Zeit-Auslöser und das allgemeine "
          "Vokabular (Modus scan). Bitte mit `analysis run --mode scan` neu rechnen.")


class TranslateError(ValueError):
    pass


@dataclass(frozen=True)
class SignalRef:
    handle: str
    entity_id: str
    kind: str          # entities.kind, zum Beispiel person, sensor, flat
    value_kind: str    # categorical | numeric | timestamp


@dataclass(frozen=True)
class RuleInputs:
    triggers: list[dict]
    conditions: list[dict]
    delay_min: int
    action_kind: str
    action_preset: str
    action_setpoint: float


def _ref(signals: dict[str, SignalRef], handle: Any) -> SignalRef:
    ref = signals.get(handle) if isinstance(handle, str) else None
    if ref is None:
        raise TranslateError(f"Signal {handle!r} ist unbekannt")
    return ref


def _weekday_condition(days: list[int]) -> dict:
    return {"condition": "time", "weekday": [WEEKDAYS[day] for day in sorted(days)]}


def _offset_text(minutes: int) -> str:
    sign = "-" if minutes < 0 else ""
    hours, rest = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}:{rest:02d}:00"


def _state_trigger(ref: SignalRef, value: str) -> dict:
    if ref.kind == "person" and value == "away":
        return {"trigger": "state", "entity_id": ref.entity_id, "from": "home", "not_to": list(UNUSABLE)}
    return {"trigger": "state", "entity_id": ref.entity_id, "to": value, "not_from": list(UNUSABLE)}


def _state_condition(ref: SignalRef, value: str, op: str) -> dict:
    if ref.kind == "person" and value == "away":
        home = {"condition": "state", "entity_id": ref.entity_id, "state": "home"}
        return {"condition": "not", "conditions": [home]} if op == "is" else home
    base = {"condition": "state", "entity_id": ref.entity_id, "state": value}
    return base if op == "is" else {"condition": "not", "conditions": [base]}


def _trigger(raw: dict, signals: dict[str, SignalRef]) -> tuple[list[dict], list[dict], int]:
    kind = raw.get("type")
    if kind == "time":
        days = sorted(raw["weekdays"])
        extra = [] if days == list(range(7)) else [_weekday_condition(days)]
        return [{"trigger": "time", "at": f"{raw['time']}:00"}], extra, 0
    if kind == "event":
        raise TranslateError(LEGACY.format(what=f"Ereignis {raw.get('event')}"))
    if kind != "signal":
        raise TranslateError(f"Unbekannter Auslöser {kind!r}")
    ref = _ref(signals, raw.get("signal"))
    event = raw.get("event")
    offset = int(raw.get("offset_min", 0))
    if event == "alarm_time":
        trigger = {"trigger": "time", "at": ref.entity_id}
        if offset:
            trigger["offset"] = _offset_text(offset)
        return [trigger], [], 0
    if offset < 0:
        raise TranslateError("Ein negativer Versatz ist nur bei alarm_time möglich")
    if event == "to_state":
        extra: list[dict] = []
        low, high = raw.get("min_prev_min"), raw.get("max_prev_min")
        if low is not None or high is not None:
            duration = "((trigger.to_state.last_changed - trigger.from_state.last_changed).total_seconds() / 60)"
            parts = ["trigger.from_state is not none"]
            if low is not None:
                parts.append(f"{duration} >= {int(low)}")
            if high is not None:
                parts.append(f"{duration} < {int(high)}")
            extra.append({"condition": "template", "value_template": "{{ " + " and ".join(parts) + " }}"})
        return [_state_trigger(ref, raw["value"])], extra, offset
    if event in ("rises_above", "falls_below"):
        key = "above" if event == "rises_above" else "below"
        return [{"trigger": "numeric_state", "entity_id": ref.entity_id, key: float(raw["value"])}], [], offset
    raise TranslateError(f"Unbekanntes Signal-Ereignis {event!r}")


def _condition(raw: dict, signals: dict[str, SignalRef]) -> dict:
    kind = raw.get("type")
    if kind == "weekdays":
        return _weekday_condition(raw["weekdays"])
    if kind == "time_window":
        return {"condition": "time", "after": f"{raw['from']}:00", "before": f"{raw['to']}:00"}
    if kind == "signal_state":
        return _state_condition(_ref(signals, raw.get("signal")), raw["value"], raw["op"])
    if kind == "signal_range":
        ref = _ref(signals, raw.get("signal"))
        return {"condition": "numeric_state", "entity_id": ref.entity_id, raw["op"]: float(raw["value"])}
    if kind in ("outside_temp", "person_home"):
        raise TranslateError(LEGACY.format(what=f"Bedingung {kind}"))
    raise TranslateError(f"Unbekannte Bedingung {kind!r}")


def translate(candidate: Candidate, signals: dict[str, SignalRef], *, setpoint_min: float,
              setpoint_max: float) -> RuleInputs:
    triggers, conditions, delay = _trigger(candidate.trigger, signals)
    conditions = list(conditions) + [_condition(item, signals) for item in candidate.conditions]
    if not conditions:
        conditions = [{"condition": "template", "value_template": "{{ true }}"}]
    action = candidate.action
    if action.get("type") == "preset":
        preset = str(action.get("value") or "").strip()
        if not preset:
            raise TranslateError("Das Preset der Aktion ist leer")
        return RuleInputs(triggers, conditions, delay, "preset", preset, 21.0)
    if action.get("type") == "setpoint":
        value = float(action["value"])
        if not setpoint_min <= value <= setpoint_max:
            raise TranslateError(f"Der Sollwert {value:g} °C liegt außerhalb der erlaubten Grenzen "
                                 f"{setpoint_min:g} bis {setpoint_max:g} °C")
        return RuleInputs(triggers, conditions, delay, "setpoint", "", value)
    raise TranslateError(f"Unbekannte Aktion {action.get('type')!r}")


def load_signal_refs(db: Database) -> dict[str, SignalRef]:
    rows = db.conn.execute("SELECT handle, entity_id, kind, value_kind FROM signals ORDER BY handle")
    return {row["handle"]: SignalRef(row["handle"], row["entity_id"], row["kind"], row["value_kind"]) for row in rows}


def climate_for_room(db: Database, area: str) -> str:
    rows = db.conn.execute(
        "SELECT entity_id FROM entities WHERE kind = 'climate' AND active = 1 AND room = ? ORDER BY entity_id",
        (area,)).fetchall()
    if not rows:
        raise TranslateError(f"Für den Raum {area} ist kein Heizkörper aufgezeichnet")
    if len(rows) > 1:
        raise TranslateError(f"Für den Raum {area} gibt es mehrere Heizkörper: "
                             f"{', '.join(row['entity_id'] for row in rows)}")
    return rows[0]["entity_id"]
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd ai && python -m pytest tests/test_translate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/translate.py ai/tests/test_translate.py
git commit -m "feat(ai): Kandidaten in Blueprint-Eingaben uebersetzen" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: HA-Schnittstelle (REST und WebSocket)

**Files:**
- Create: `ai/execution/ha_api.py`
- Create: `ai/tests/fake_ha_rest.py`
- Modify: `ai/tests/conftest.py` (Fixtures `fake_rest`, `ecfg`)
- Test: `ai/tests/test_ha_api.py`

**Interfaces:**
- Consumes: `collector.hass_ws.HassClient.command(type_, **payload)`, `execution.helpers.slug`, `execution.config.load_execution_config`.
- Produces:
  - `execution.ha_api.HaError(RuntimeError)`, `rest_base(ws_url) -> str`
  - `execution.ha_api.HaRest(base_url, token)` mit `async get_state(entity_id) -> dict | None`, `list_states() -> list[dict]`, `automation_config(config_id) -> dict | None`, `save_automation(config_id, config) -> None`, `delete_automation(config_id) -> None`, `find_automation_entity(config_id, *, retries=10, delay=0.5) -> str | None`, `close()`.
  - `execution.ha_api.HaWs(client)` mit `async ensure_helper(domain, name, **options) -> str` (Entity-ID), `delete_helper(domain, name) -> bool`, `blueprint_paths() -> set[str]`, `schedule_flanks(schedule_id) -> set[str]` (Zeiten `HH:MM`), `ensure_dashboard(url_path, title, icon) -> None`, `save_dashboard(url_path, config) -> None`.
  - `execution.ha_api.ha_connection(url, token)` (async Kontextmanager, liefert `(HaRest, HaWs)`).
  - Test-Fixtures `fake_rest` (`FakeHaRest` mit `.base`, `.automations`, `.states`, `.fail_save`) und `ecfg` (`ExecutionConfig` aus `config/execution.yaml` mit `HA_URL`, `HA_WRITE_TOKEN=secret`, `MQTT_HOST`).

- [ ] **Step 1: Failing tests schreiben**

`ai/tests/fake_ha_rest.py`:

```python
from __future__ import annotations

from aiohttp import web

from execution.helpers import slug


class FakeHaRest:
    """Kleine Attrappe der Home-Assistant-REST-API (Zustände und Automationen)."""

    def __init__(self, token: str = "secret"):
        self.token = token
        self.base = ""
        self.automations: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.fail_save = False
        self.requests: list[tuple[str, str]] = []

    def add_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/states", self.list_states)
        app.router.add_get("/api/states/{entity_id}", self.get_state)
        app.router.add_get("/api/config/automation/config/{config_id}", self.get_config)
        app.router.add_post("/api/config/automation/config/{config_id}", self.save_config)
        app.router.add_delete("/api/config/automation/config/{config_id}", self.delete_config)

    def app(self) -> web.Application:
        app = web.Application()
        self.add_routes(app)
        return app

    def _check(self, request: web.Request) -> web.Response | None:
        self.requests.append((request.method, request.path))
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return web.Response(status=401, text="unauthorized")
        return None

    def set_state(self, entity_id: str, state: str, **attributes) -> None:
        self.states[entity_id] = {"entity_id": entity_id, "state": state, "attributes": attributes}

    async def list_states(self, request: web.Request) -> web.Response:
        return self._check(request) or web.json_response(list(self.states.values()))

    async def get_state(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied:
            return denied
        state = self.states.get(request.match_info["entity_id"])
        return web.json_response(state) if state else web.Response(status=404, text="not found")

    async def get_config(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied:
            return denied
        config = self.automations.get(request.match_info["config_id"])
        return web.json_response(config) if config is not None else web.Response(status=404, text="not found")

    async def save_config(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied:
            return denied
        if self.fail_save:
            return web.Response(status=500, text="boom")
        config_id = request.match_info["config_id"]
        config = await request.json()
        self.automations[config_id] = config
        entity_id = f"automation.{slug(config.get('alias', config_id))}"
        self.set_state(entity_id, "on", id=config_id, friendly_name=config.get("alias"))
        return web.json_response({"result": "ok"})

    async def delete_config(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied:
            return denied
        config_id = request.match_info["config_id"]
        if config_id not in self.automations:
            return web.Response(status=404, text="not found")
        del self.automations[config_id]
        for entity_id in [key for key, state in self.states.items() if state["attributes"].get("id") == config_id]:
            del self.states[entity_id]
        return web.json_response({"result": "ok"})
```

`ai/tests/conftest.py` am Ende anhängen:

```python

from execution.config import load_execution_config  # noqa: E402
from tests.fake_ha_rest import FakeHaRest  # noqa: E402


@pytest.fixture
def ecfg():
    return load_execution_config(
        ROOT / "config" / "execution.yaml",
        {"HA_URL": "ws://ha.test/api/websocket", "HA_WRITE_TOKEN": "secret", "MQTT_HOST": "broker.test"},
    )


@pytest_asyncio.fixture
async def fake_rest():
    fake = FakeHaRest()
    server = TestServer(fake.app())
    await server.start_server()
    fake.base = f"http://127.0.0.1:{server.port}"
    try:
        yield fake
    finally:
        await server.close()
```

`ai/tests/test_ha_api.py`:

```python
import pytest

from collector.hass_ws import HassClient
from execution.ha_api import HaError, HaRest, HaWs, rest_base


def test_rest_base_follows_the_websocket_url():
    assert rest_base("ws://192.168.1.100:8123/api/websocket") == "http://192.168.1.100:8123"
    assert rest_base("wss://ha.example.org/api/websocket") == "https://ha.example.org"


async def test_rest_state_and_automation_roundtrip(fake_rest):
    rest = HaRest(fake_rest.base, "secret")
    try:
        assert await rest.get_state("climate.nope") is None
        fake_rest.set_state("climate.bt", "heat", temperature=21)
        assert (await rest.get_state("climate.bt"))["attributes"]["temperature"] == 21
        assert await rest.automation_config("homeai_rule_r5") is None
        await rest.save_automation("homeai_rule_r5", {"alias": "KI-Regel r5", "use_blueprint": {"path": "x"}})
        assert (await rest.automation_config("homeai_rule_r5"))["alias"] == "KI-Regel r5"
        assert await rest.find_automation_entity("homeai_rule_r5") == "automation.ki_regel_r5"
        await rest.delete_automation("homeai_rule_r5")
        await rest.delete_automation("homeai_rule_r5")           # zweites Löschen ist kein Fehler
        assert await rest.automation_config("homeai_rule_r5") is None
        assert await rest.find_automation_entity("homeai_rule_r5", retries=1, delay=0) is None
    finally:
        await rest.close()


async def test_rest_errors_carry_the_status(fake_rest):
    fake_rest.fail_save = True
    rest = HaRest(fake_rest.base, "secret")
    try:
        with pytest.raises(HaError, match="500"):
            await rest.save_automation("x", {"alias": "a"})
    finally:
        await rest.close()
    wrong = HaRest(fake_rest.base, "falsch")
    try:
        with pytest.raises(HaError, match="401"):
            await wrong.get_state("climate.bt")
    finally:
        await wrong.close()


async def test_ensure_helper_creates_once_and_returns_the_entity_id(fake_hass):
    created = []
    fake_hass.responses["input_boolean/list"] = lambda msg: [{"id": "ki_regeln_bad", "name": "ki_regeln_bad"}] if created else []

    def create(msg):
        created.append(msg)
        return {"id": "ki_regeln_bad", "name": msg["name"]}

    fake_hass.responses["input_boolean/create"] = create
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        ws = HaWs(client)
        assert await ws.ensure_helper("input_boolean", "ki_regeln_bad", icon="mdi:robot") == "input_boolean.ki_regeln_bad"
        assert await ws.ensure_helper("input_boolean", "ki_regeln_bad", icon="mdi:robot") == "input_boolean.ki_regeln_bad"
    finally:
        await client.close()
    assert len(created) == 1 and created[0]["icon"] == "mdi:robot"


async def test_delete_helper_by_name(fake_hass):
    deleted = []
    fake_hass.responses["input_boolean/list"] = [{"id": "abc", "name": "ki_regeln_bad"}]
    fake_hass.responses["input_boolean/delete"] = lambda msg: deleted.append(msg) or None
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        ws = HaWs(client)
        assert await ws.delete_helper("input_boolean", "ki_regeln_bad") is True
        assert await ws.delete_helper("input_boolean", "gibt_es_nicht") is False
    finally:
        await client.close()
    assert deleted[0]["input_boolean_id"] == "abc"


async def test_blueprints_schedules_and_dashboard(fake_hass):
    fake_hass.responses["blueprint/list"] = {"homeai/ki_regel.yaml": {"metadata": {"name": "KI-Regel (V1.0)"}}}
    fake_hass.responses["schedule/list"] = [
        {"id": "heating_schedule_office", "name": "x",
         "monday": [{"from": "06:30:00", "to": "18:30:00"}], "tuesday": [{"from": "07:00:00", "to": "12:00:00"}]},
        {"id": "other", "name": "y", "monday": [{"from": "01:00:00", "to": "02:00:00"}]},
    ]
    saved = []
    fake_hass.responses["lovelace/dashboards/list"] = []
    fake_hass.responses["lovelace/dashboards/create"] = lambda msg: saved.append(("create", msg)) or {}
    fake_hass.responses["lovelace/config/save"] = lambda msg: saved.append(("save", msg)) or None
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        ws = HaWs(client)
        assert await ws.blueprint_paths() == {"homeai/ki_regel.yaml"}
        assert await ws.schedule_flanks("heating_schedule_office") == {"06:30", "18:30", "07:00", "12:00"}
        assert await ws.schedule_flanks("gibt_es_nicht") == set()
        await ws.ensure_dashboard("ki-regeln", "KI-Regeln", "mdi:robot")
        await ws.save_dashboard("ki-regeln", {"views": []})
    finally:
        await client.close()
    kinds = [kind for kind, _ in saved]
    assert kinds == ["create", "save"]
    assert saved[0][1]["url_path"] == "ki-regeln" and saved[1][1]["config"] == {"views": []}


async def test_existing_dashboard_is_not_created_again(fake_hass):
    created = []
    fake_hass.responses["lovelace/dashboards/list"] = [{"id": "ki_regeln", "url_path": "ki-regeln"}]
    fake_hass.responses["lovelace/dashboards/create"] = lambda msg: created.append(msg) or {}
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        await HaWs(client).ensure_dashboard("ki-regeln", "KI-Regeln", "mdi:robot")
    finally:
        await client.close()
    assert created == []
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_ha_api.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.ha_api'`.

- [ ] **Step 3: Implementieren**

`ai/execution/ha_api.py`:

```python
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import aiohttp

from collector.hass_ws import HassClient

from .helpers import slug


class HaError(RuntimeError):
    pass


def rest_base(ws_url: str) -> str:
    parsed = urlparse(ws_url)
    return f"{'https' if parsed.scheme == 'wss' else 'http'}://{parsed.netloc}"


class HaRest:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.token}"})
        try:
            async with self._session.request(method, self.base_url + path, **kwargs) as response:
                text = await response.text()
                if response.status == 404:
                    return None
                if response.status >= 400:
                    raise HaError(f"{method} {path}: {response.status} {text[:200]}")
                try:
                    return json.loads(text) if text else {}
                except ValueError:
                    return {}
        except aiohttp.ClientError as exc:
            raise HaError(f"{method} {path}: {exc}") from exc

    async def get_state(self, entity_id: str) -> dict | None:
        return await self._request("GET", f"/api/states/{entity_id}")

    async def list_states(self) -> list[dict]:
        return await self._request("GET", "/api/states") or []

    async def automation_config(self, config_id: str) -> dict | None:
        return await self._request("GET", f"/api/config/automation/config/{config_id}")

    async def save_automation(self, config_id: str, config: dict) -> None:
        await self._request("POST", f"/api/config/automation/config/{config_id}", json=config)

    async def delete_automation(self, config_id: str) -> None:
        await self._request("DELETE", f"/api/config/automation/config/{config_id}")

    async def find_automation_entity(self, config_id: str, *, retries: int = 10, delay: float = 0.5) -> str | None:
        for attempt in range(retries):
            for state in await self.list_states():
                if state["entity_id"].startswith("automation.") and state["attributes"].get("id") == config_id:
                    return state["entity_id"]
            if attempt + 1 < retries:
                await asyncio.sleep(delay)
        return None


class HaWs:
    def __init__(self, client: HassClient):
        self.client = client

    async def _helpers(self, domain: str) -> dict[str, dict]:
        items = await self.client.command(f"{domain}/list")
        return {item["name"]: item for item in items or []}

    async def ensure_helper(self, domain: str, name: str, **options: Any) -> str:
        if name not in await self._helpers(domain):
            await self.client.command(f"{domain}/create", name=name, **options)
        return f"{domain}.{slug(name)}"

    async def delete_helper(self, domain: str, name: str) -> bool:
        item = (await self._helpers(domain)).get(name)
        if item is None:
            return False
        await self.client.command(f"{domain}/delete", **{f"{domain}_id": item["id"]})
        return True

    async def blueprint_paths(self) -> set[str]:
        return set(await self.client.command("blueprint/list", domain="automation") or {})

    async def schedule_flanks(self, schedule_id: str) -> set[str]:
        flanks: set[str] = set()
        for item in await self.client.command("schedule/list") or []:
            if item.get("id") != schedule_id:
                continue
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
                for block in item.get(day) or []:
                    flanks.update(str(block[key])[:5] for key in ("from", "to") if block.get(key))
        return flanks

    async def ensure_dashboard(self, url_path: str, title: str, icon: str) -> None:
        existing = await self.client.command("lovelace/dashboards/list") or []
        if any(item.get("url_path") == url_path for item in existing):
            return
        await self.client.command("lovelace/dashboards/create", url_path=url_path, title=title, icon=icon,
                                  show_in_sidebar=True, require_admin=False)

    async def save_dashboard(self, url_path: str, config: dict) -> None:
        await self.client.command("lovelace/config/save", url_path=url_path, config=config)


@asynccontextmanager
async def ha_connection(url: str, token: str):
    rest = HaRest(rest_base(url), token)
    client = HassClient(url, token)
    await client.connect()
    try:
        yield rest, HaWs(client)
    finally:
        await client.close()
        await rest.close()
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_ha_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/ha_api.py ai/tests/fake_ha_rest.py ai/tests/conftest.py ai/tests/test_ha_api.py
git commit -m "feat(ai): HA-Schnittstelle fuer Automationen, Helfer, Blueprints und Dashboard" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: MQTT-Bus und Discovery der Regeln

**Files:**
- Create: `ai/execution/mqtt.py`
- Modify: `ai/requirements.txt` (+ `aiomqtt>=2,<3`)
- Test: `ai/tests/test_mqtt.py`

**Interfaces:**
- Consumes: `execution.config.MqttConfig`.
- Produces:
  - `execution.mqtt.Message(topic: str, payload: str, retain: bool = False)`, `Handler = Callable[[Message], Awaitable[None]]`, `topic_matches(topic_filter, topic) -> bool`
  - `execution.mqtt.STATUS_TOPIC = "homeai/status"`
  - `execution.mqtt.InMemoryBus` (für Tests) und `AioMqttBus(cfg: MqttConfig)`; beide mit `on(topic_filter, handler)`, `on_connect(callback)`, `async publish(topic, payload, *, retain=False, qos=1)`, `async run(stop: asyncio.Event)`; `InMemoryBus` zusätzlich `.published: list[Message]`, `.retained: dict[str, str]`, `async deliver(topic, payload, retain=False)`, `async start()`, `last(topic) -> Message | None`.
  - `execution.mqtt.rule_entities(rule_id, label) -> list[tuple[str, dict]]` (Discovery-Topic, Payload): Schalter `switch.homeai_<id>_aktiv` (ohne Verfügbarkeitsthema, optimistisch), Sensoren `sensor.homeai_<id>_kennzahlen`, `…_trefferquote`, `…_abweichung`.
  - `execution.mqtt.rule_discovery_topics(rule_id) -> list[str]`, `async publish_rule_entities(bus, rule_id, label)`, `async clear_rule_entities(bus, rule_id)`.
  - `execution.mqtt.rule_label(rule_id: str, description: str) -> str` (`"Regel <id> <Ort>"`, der Ort ist der Text der Beschreibung vor dem ersten Doppelpunkt; ein Label, das beim Anlegen und beim erneuten Veröffentlichen gleich bleibt).

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_mqtt.py`:

```python
import json

import pytest

from execution.mqtt import (InMemoryBus, Message, clear_rule_entities, publish_rule_entities, rule_discovery_topics,
                            rule_entities, rule_label, topic_matches)


@pytest.mark.parametrize("flt,topic,expected", [
    ("homeai/rule/+/fired", "homeai/rule/r5/fired", True),
    ("homeai/rule/+/fired", "homeai/rule/r5/stats", False),
    ("homeai/rule/+/active/set", "homeai/rule/r5/active/set", True),
    ("homeai/cmd/+", "homeai/cmd/scan", True),
    ("homeai/cmd/+", "homeai/cmd/scan/extra", False),
    ("homeai/#", "homeai/a/b/c", True),
    ("homeai/rule/+", "homeai/rule", False),
])
def test_topic_matching(flt, topic, expected):
    assert topic_matches(flt, topic) is expected


async def test_in_memory_bus_records_retains_and_delivers():
    bus = InMemoryBus()
    seen = []

    async def handler(message: Message):
        seen.append(message)

    bus.on("homeai/cmd/+", handler)
    await bus.publish("homeai/status", "online", retain=True)
    await bus.publish("homeai/status", "", retain=True)
    await bus.publish("homeai/x", "1")
    assert bus.retained == {} and [m.topic for m in bus.published] == ["homeai/status", "homeai/status", "homeai/x"]
    assert bus.last("homeai/x").payload == "1" and bus.last("homeai/none") is None
    await bus.deliver("homeai/cmd/scan", "PRESS")
    await bus.deliver("homeai/other", "x")
    assert [(m.topic, m.payload) for m in seen] == [("homeai/cmd/scan", "PRESS")]


async def test_connect_callbacks_run_on_start():
    bus = InMemoryBus()
    calls = []

    async def callback():
        calls.append("connected")

    bus.on_connect(callback)
    await bus.start()
    assert calls == ["connected"]


def test_rule_entities_follow_the_spec():
    entities = dict(rule_entities("r5", "Bad Mo-Fr 06:30"))
    switch = entities["homeassistant/switch/homeai_r5/aktiv/config"]
    assert switch["default_entity_id"] == "switch.homeai_r5_aktiv"
    assert switch["command_topic"] == "homeai/rule/r5/active/set"
    assert switch["optimistic"] is True and switch["payload_on"] == "ON"
    assert "availability_topic" not in switch and "state_topic" not in switch     # gilt auch ohne Container
    sensors = {payload["default_entity_id"]: payload for topic, payload in entities.items() if "/sensor/" in topic}
    assert set(sensors) == {"sensor.homeai_r5_kennzahlen", "sensor.homeai_r5_trefferquote", "sensor.homeai_r5_abweichung"}
    for payload in sensors.values():
        assert payload["state_topic"] == "homeai/rule/r5/stats"
        assert payload["unique_id"].startswith("homeai_r5_")
    assert sensors["sensor.homeai_r5_kennzahlen"]["json_attributes_topic"] == "homeai/rule/r5/stats"
    assert sensors["sensor.homeai_r5_trefferquote"]["unit_of_measurement"] == "%"


def test_rule_label_uses_the_place_before_the_first_colon():
    assert rule_label("r5", "Bad: Mo-Fr 06:30 -> Preset comfort") == "Regel r5 Bad"


async def test_publish_and_clear_rule_entities():
    bus = InMemoryBus()
    await publish_rule_entities(bus, "r5", "Bad")
    assert set(bus.retained) == set(rule_discovery_topics("r5"))
    assert all(json.loads(payload)["unique_id"] for payload in bus.retained.values())
    await clear_rule_entities(bus, "r5")
    assert bus.retained == {}
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_mqtt.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.mqtt'`.

- [ ] **Step 3: Implementieren**

`ai/requirements.txt` um die Zeile `aiomqtt>=2,<3` ergänzen.

`ai/execution/mqtt.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from .config import MqttConfig

log = logging.getLogger("execution")

STATUS_TOPIC = "homeai/status"
DISCOVERY = "homeassistant"
DEVICE = {"identifiers": ["homeai"], "name": "KI-Regeln", "manufacturer": "home-ai"}


@dataclass(frozen=True)
class Message:
    topic: str
    payload: str
    retain: bool = False


Handler = Callable[[Message], Awaitable[None]]
Callback = Callable[[], Awaitable[None]]


def topic_matches(topic_filter: str, topic: str) -> bool:
    filter_parts, topic_parts = topic_filter.split("/"), topic.split("/")
    for index, part in enumerate(filter_parts):
        if part == "#":
            return True
        if index >= len(topic_parts):
            return False
        if part != "+" and part != topic_parts[index]:
            return False
    return len(filter_parts) == len(topic_parts)


class InMemoryBus:
    """Bus für Tests: zeichnet Veröffentlichungen auf, Eingänge kommen über `deliver`."""

    def __init__(self) -> None:
        self.published: list[Message] = []
        self.retained: dict[str, str] = {}
        self._handlers: list[tuple[str, Handler]] = []
        self._connected: list[Callback] = []

    def on(self, topic_filter: str, handler: Handler) -> None:
        self._handlers.append((topic_filter, handler))

    def on_connect(self, callback: Callback) -> None:
        self._connected.append(callback)

    async def publish(self, topic: str, payload: str, *, retain: bool = False, qos: int = 1) -> None:
        self.published.append(Message(topic, payload, retain))
        if retain:
            if payload == "":
                self.retained.pop(topic, None)
            else:
                self.retained[topic] = payload

    def last(self, topic: str) -> Message | None:
        for message in reversed(self.published):
            if message.topic == topic:
                return message
        return None

    async def deliver(self, topic: str, payload: str, retain: bool = False) -> None:
        for topic_filter, handler in self._handlers:
            if topic_matches(topic_filter, topic):
                await handler(Message(topic, payload, retain))

    async def start(self) -> None:
        for callback in self._connected:
            await callback()

    async def run(self, stop: asyncio.Event) -> None:
        await self.start()
        await stop.wait()


class AioMqttBus:
    """Verbindung zum MQTT-Broker (aiomqtt). Wird nur im Dauerbetrieb und für Kommandos gebraucht."""

    def __init__(self, cfg: MqttConfig, identifier: str = "homeai-execution") -> None:
        self.cfg = cfg
        self.identifier = identifier
        self._handlers: list[tuple[str, Handler]] = []
        self._connected: list[Callback] = []
        self._client = None

    def on(self, topic_filter: str, handler: Handler) -> None:
        self._handlers.append((topic_filter, handler))

    def on_connect(self, callback: Callback) -> None:
        self._connected.append(callback)

    def _arguments(self, **extra) -> dict:
        return dict(hostname=self.cfg.host, port=self.cfg.port, username=self.cfg.user,
                    password=self.cfg.password, **extra)

    async def publish(self, topic: str, payload: str, *, retain: bool = False, qos: int = 1) -> None:
        if self._client is not None:
            await self._client.publish(topic, payload=payload, qos=qos, retain=retain)
            return
        import aiomqtt
        async with aiomqtt.Client(**self._arguments()) as client:      # kurze Verbindung für einzelne Kommandos
            await client.publish(topic, payload=payload, qos=qos, retain=retain)

    async def run(self, stop: asyncio.Event) -> None:
        import aiomqtt
        backoff = 1.0
        while not stop.is_set():
            try:
                will = aiomqtt.Will(topic=STATUS_TOPIC, payload="offline", qos=1, retain=True)
                async with aiomqtt.Client(**self._arguments(identifier=self.identifier, will=will)) as client:
                    self._client = client
                    backoff = 1.0
                    for topic_filter, _ in self._handlers:
                        await client.subscribe(topic_filter, qos=1)
                    await client.publish(STATUS_TOPIC, payload="online", qos=1, retain=True)
                    for callback in self._connected:
                        await callback()
                    reader = asyncio.create_task(self._read(client))
                    waiter = asyncio.create_task(stop.wait())
                    done, pending = await asyncio.wait({reader, waiter}, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    if reader in done:
                        reader.result()
                    else:
                        await client.publish(STATUS_TOPIC, payload="offline", qos=1, retain=True)
            except aiomqtt.MqttError as exc:
                log.warning("MQTT-Verbindung verloren: %s", exc)
            finally:
                self._client = None
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60.0)

    async def _read(self, client) -> None:
        async for message in client.messages:
            payload = message.payload
            text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
            incoming = Message(str(message.topic), text, bool(message.retain))
            for topic_filter, handler in self._handlers:
                if topic_matches(topic_filter, incoming.topic):
                    try:
                        await handler(incoming)
                    except Exception:
                        log.exception("Fehler bei der Verarbeitung von %s", incoming.topic)


def _topic(component: str, rule_id: str, object_id: str) -> str:
    return f"{DISCOVERY}/{component}/homeai_{rule_id}/{object_id}/config"


def rule_discovery_topics(rule_id: str) -> list[str]:
    return [_topic("switch", rule_id, "aktiv"), _topic("sensor", rule_id, "kennzahlen"),
            _topic("sensor", rule_id, "trefferquote"), _topic("sensor", rule_id, "abweichung")]


def rule_entities(rule_id: str, label: str) -> list[tuple[str, dict]]:
    base = f"homeai/rule/{rule_id}"
    stats = f"{base}/stats"

    def sensor(object_id: str, name: str, template: str, **extra) -> tuple[str, dict]:
        return _topic("sensor", rule_id, object_id), {
            "name": f"{label} {name}", "unique_id": f"homeai_{rule_id}_{object_id}",
            "default_entity_id": f"sensor.homeai_{rule_id}_{object_id}", "state_topic": stats,
            "value_template": template, "device": DEVICE, **extra}

    return [
        # Der Schalter liegt in HA, merkt sich seinen Zustand selbst und braucht den Container nicht.
        (_topic("switch", rule_id, "aktiv"), {
            "name": f"{label} aktiv", "unique_id": f"homeai_{rule_id}_aktiv",
            "default_entity_id": f"switch.homeai_{rule_id}_aktiv", "command_topic": f"{base}/active/set",
            "payload_on": "ON", "payload_off": "OFF", "optimistic": True, "device": DEVICE}),
        sensor("kennzahlen", "Kennzahlen", "{{ value_json.reife }}", json_attributes_topic=stats),
        sensor("trefferquote", "Trefferquote",
               "{{ value_json.trefferquote_pct if value_json.trefferquote_pct is not none else 'unknown' }}",
               unit_of_measurement="%"),
        sensor("abweichung", "letzte Abweichung",
               "{{ value_json.abweichung if value_json.abweichung else 'keine' }}"),
    ]


def rule_label(rule_id: str, description: str) -> str:
    return f"Regel {rule_id} {description.split(':', 1)[0]}"


async def publish_rule_entities(bus, rule_id: str, label: str) -> None:
    for topic, payload in rule_entities(rule_id, label):
        await bus.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)


async def clear_rule_entities(bus, rule_id: str) -> None:
    for topic in rule_discovery_topics(rule_id):
        await bus.publish(topic, "", retain=True)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_mqtt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/mqtt.py ai/requirements.txt ai/tests/test_mqtt.py
git commit -m "feat(ai): MQTT-Bus und Discovery der Regel-Entitaeten" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Auswertung der Auslösungen und Kennzahlen

**Files:**
- Create: `ai/execution/outcomes.py`
- Test: `ai/tests/test_outcomes.py`

**Interfaces:**
- Consumes: View `manual_actions` (Spalten `ts_utc`, `entity_id`, `actor_class`, `changed_attrs`, `new`, `origin_automation`; Aufgabe 4), `execution.store.ExecStore` (`stats_rows`, `save_outcome`; Aufgabe 8), `execution.models.Deployment/RuleEvent/Stats`, `execution.config.ExecutionConfig`.
- Produces:
  - `execution.outcomes.Verdict(outcome: str, window_start: datetime, window_end: datetime, lage: str | None = None, abstand_min: float | None = None, deviation: str | None = None)`
  - `execution.outcomes.same_target(target: dict, new: dict, tolerance: float) -> bool`
  - `execution.outcomes.evaluate(db, event: RuleEvent, deployment: Deployment, cfg) -> Verdict | None` (`None` = nicht werten, bei Grund `wohnungszustand` oder `nicht_erreichbar`)
  - `execution.outcomes.rule_stats(store: ExecStore, rule_id: str, cfg) -> Stats`

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_outcomes.py`:

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from collector.models import Change, EntityRule, to_utc_iso
from execution.models import Deployment, RuleEvent
from execution.outcomes import evaluate, rule_stats, same_target
from execution.store import ExecStore

T0 = datetime(2026, 10, 15, 4, 30, tzinfo=timezone.utc)        # Auslösung um 06:30 Ortszeit
CLIMATE = EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode"))
OWN = "automation.ki_regel_r5"
COMFORT = {"kind": "preset", "value": "comfort"}


@pytest.fixture
def world(db):
    db.upsert_entity(CLIMATE, "bad", T0)
    add(db, -300, 17, "eco")                                   # Ausgangszustand
    return db


def add(db, minutes, temperature, preset, origin=None, automation=None, state="heat"):
    ts = T0 + timedelta(minutes=minutes)
    db.insert_changes([Change(ts, "climate.bt", state, {"temperature": temperature, "preset_mode": preset},
                              f"c{minutes}")], "live")
    if origin is not None:
        sid = db.conn.execute("SELECT id FROM state_changes WHERE ts_utc = ?", (to_utc_iso(ts),)).fetchone()["id"]
        db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via, actor, automation) "
                        "VALUES (?, ?, 'kontext', 'u1', ?)", (sid, origin, automation))
        db.conn.commit()


def deployment():
    return Deployment("r5", 5, "bad", OWN, "homeai_rule_r5", "climate.bt", "schatten", "1.0", "x",
                      "2026-10-15T00:00:00.000000+00:00")


def event(result="nicht_ausgefuehrt", reason="schatten", target=COMFORT):
    return RuleEvent("r5", to_utc_iso(T0), "schatten", result, reason, target, {}, "{}", 1)


def verdict(db, ecfg, **kwargs):
    return evaluate(db, event(**kwargs), deployment(), ecfg)


def test_same_target_for_presets_and_setpoints():
    assert same_target(COMFORT, {"preset_mode": "comfort", "temperature": 21.0}, 0.5)
    assert not same_target(COMFORT, {"preset_mode": "eco", "temperature": 21.0}, 0.5)
    setpoint = {"kind": "setpoint", "value": 21.0}
    assert same_target(setpoint, {"temperature": 21.4}, 0.5) and not same_target(setpoint, {"temperature": 21.6}, 0.5)
    assert not same_target(setpoint, {"temperature": None}, 0.5)


def test_shadow_without_any_intervention_is_neutral(world, ecfg):
    assert verdict(world, ecfg).outcome == "neutral"


def test_user_does_the_same_after_the_rule(world, ecfg):
    add(world, 10, 21, "comfort", origin="benutzer")
    result = verdict(world, ecfg)
    assert (result.outcome, result.lage, result.abstand_min) == ("treffer", "nachher", 10.0)


def test_user_did_it_before_the_rule(world, ecfg):
    add(world, -20, 21, "comfort", origin="benutzer")
    result = verdict(world, ecfg)
    assert (result.outcome, result.lage, result.abstand_min) == ("treffer", "vorher", -20.0)


def test_device_interventions_count_like_users(world, ecfg):
    add(world, 5, 21, "comfort", origin="geraet_unklar")
    assert verdict(world, ecfg).outcome == "treffer"


def test_different_target_is_a_false_trigger_with_the_deviation(world, ecfg):
    add(world, 5, 19, "eco", origin="benutzer")
    result = verdict(world, ecfg)
    assert (result.outcome, result.deviation) == ("fehlausloesung", "preset:eco")


def test_the_last_intervention_in_the_window_counts(world, ecfg):
    add(world, 5, 21, "comfort", origin="benutzer")
    add(world, 30, 17, "eco", origin="benutzer")
    assert verdict(world, ecfg).outcome == "fehlausloesung"


def test_an_automation_in_between_interrupts(world, ecfg):
    add(world, 20, 18, "eco", origin="automation", automation="automation.heating_control_bathroom")
    add(world, 50, 21, "comfort", origin="benutzer")
    assert verdict(world, ecfg).outcome == "unterbrochen"


def test_an_automation_between_a_prior_intervention_and_the_trigger_interrupts(world, ecfg):
    add(world, -30, 21, "comfort", origin="benutzer")
    add(world, -10, 17, "eco", origin="automation", automation="automation.heating_control_bathroom")
    assert verdict(world, ecfg).outcome == "unterbrochen"


def test_an_automation_after_the_last_intervention_does_not_matter(world, ecfg):
    add(world, 10, 21, "comfort", origin="benutzer")
    add(world, 50, 17, "eco", origin="automation", automation="automation.heating_control_bathroom")
    assert verdict(world, ecfg).outcome == "treffer"


def test_interventions_outside_the_window_are_ignored(world, ecfg):
    add(world, 91, 21, "comfort", origin="benutzer")
    assert verdict(world, ecfg).outcome == "neutral"
    other = ecfg
    add(world, -61, 19, "eco", origin="benutzer")
    assert verdict(world, other).outcome == "neutral"


def test_mode_only_changes_are_not_interventions(world, ecfg):
    add(world, 5, 17, "eco", origin="benutzer", state="off")
    assert verdict(world, ecfg).outcome == "neutral"


def test_setpoint_target_uses_the_tolerance(world, ecfg):
    add(world, 5, 21.4, "eco", origin="benutzer")
    target = {"kind": "setpoint", "value": 21.0}
    assert verdict(world, ecfg, target=target).outcome == "treffer"


def test_active_rule_confirmed_without_intervention_and_own_change_is_ignored(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=OWN)
    assert verdict(world, ecfg, result="ausgefuehrt", reason="").outcome == "bestaetigt"


def test_own_change_without_a_known_automation_is_tolerated_for_a_few_seconds(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=None)
    assert verdict(world, ecfg, result="ausgefuehrt", reason="").outcome == "bestaetigt"


def test_active_rule_corrected_by_the_user(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=OWN)
    add(world, 20, 18, "eco", origin="benutzer")
    result = verdict(world, ecfg, result="ausgefuehrt", reason="")
    assert (result.outcome, result.deviation) == ("korrigiert", "preset:eco")


def test_active_rule_interrupted_by_another_automation(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=OWN)
    add(world, 30, 17, "eco", origin="automation", automation="automation.heating_control_bathroom")
    assert verdict(world, ecfg, result="ausgefuehrt", reason="").outcome == "unterbrochen"


def test_active_rule_held_back_by_an_earlier_intervention_is_judged_like_shadow(world, ecfg):
    add(world, -20, 21, "comfort", origin="benutzer")
    result = verdict(world, ecfg, reason="handeingriff")
    assert (result.outcome, result.lage) == ("treffer", "vorher")


def test_reasons_that_are_not_scored(world, ecfg):
    assert verdict(world, ecfg, reason="wohnungszustand") is None
    assert verdict(world, ecfg, reason="nicht_erreichbar") is None
    assert verdict(world, ecfg, reason="bereits_erreicht").outcome == "ohne_wirkung"


# --- Kennzahlen ----------------------------------------------------------------------

def fill(store: ExecStore, outcomes: list[tuple[str, str, str, float | None, str | None]]):
    """(outcome, reason, lage, abstand, deviation) je Ereignis."""
    for index, (outcome, reason, lage, distance, deviation) in enumerate(outcomes):
        ts = to_utc_iso(T0 + timedelta(days=index))
        store.add_event(RuleEvent("r5", ts, "schatten", "nicht_ausgefuehrt", reason, COMFORT, {}, "{}"))
    for event_row, (outcome, reason, lage, distance, deviation) in zip(store.pending_events(), outcomes):
        store.save_outcome(event_row.id, outcome, T0, T0, lage=lage, abstand_min=distance, deviation=deviation)


@pytest.fixture
def store(db):
    return ExecStore(db)


def test_stats_with_too_little_data(store, ecfg):
    fill(store, [("treffer", "", "nachher", 10.0, None)] * 6
         + [("fehlausloesung", "", "nachher", 5.0, "preset:eco")] * 2
         + [("neutral", "schatten", None, None, None)] * 3
         + [("ohne_wirkung", "bereits_erreicht", None, None, None)])
    stats = rule_stats(store, "r5", ecfg)
    assert (stats.events, stats.scored, stats.good, stats.treffer, stats.fehlausloesung) == (12, 8, 6, 6, 2)
    assert stats.trefferquote == 0.75 and stats.korrekturquote is None
    assert stats.reife == "zu_wenig_daten"
    assert stats.text == "6 von 8 gewertet richtig, 3 ohne Reaktion, 1 ohne Wirkung"
    assert stats.abweichung == "preset:eco" and stats.median_abstand_min == 10.0
    assert stats.to_payload()["trefferquote_pct"] == 75.0


def test_stats_turn_green_or_red_with_enough_scored_cases(store, ecfg):
    fill(store, [("treffer", "", "vorher", -20.0, None)] * 9 + [("fehlausloesung", "", "nachher", 4.0, "19.0")])
    stats = rule_stats(store, "r5", ecfg)
    assert (stats.scored, stats.reife, stats.anteil_vorher) == (10, "gruen", 0.9)
    assert rule_stats(store, "r5", replace(ecfg, max_fail_rate=0.05)).reife == "rot"


def test_stats_for_active_rules_and_blocked_events(store, ecfg):
    fill(store, [("bestaetigt", "", None, None, None)] * 7 + [("korrigiert", "", "nachher", 12.0, "18.0")] * 2
         + [("neutral", "handeingriff", None, None, None), ("unterbrochen", "", None, None, None)])
    stats = rule_stats(store, "r5", replace(ecfg, min_scored=9))
    assert (stats.korrigiert, stats.bestaetigt, stats.ausgebremst, stats.unterbrochen) == (2, 7, 1, 1)
    assert stats.korrekturquote == pytest.approx(2 / 9) and stats.reife == "rot"
    assert stats.reasons == {"handeingriff": 1}


def test_stats_of_a_rule_without_events(store, ecfg):
    stats = rule_stats(store, "r5", ecfg)
    assert (stats.events, stats.scored, stats.reife, stats.trefferquote) == (0, 0, "zu_wenig_daten", None)
    assert stats.text == "0 von 0 gewertet richtig, 0 ohne Reaktion, 0 ohne Wirkung"
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_outcomes.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.outcomes'`.

- [ ] **Step 3: Implementieren**

`ai/execution/outcomes.py`:

```python
from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from collector.db import Database
from collector.models import parse_utc, to_utc_iso

from .config import ExecutionConfig
from .models import Deployment, RuleEvent, Stats
from .store import ExecStore

RELEVANT = {"temperature", "preset_mode"}
UNSCORED_REASONS = ("wohnungszustand", "nicht_erreichbar")
OWN_CHANGE_WINDOW_S = 15.0         # eine eigene Schaltung (auch das kurze Einschalten) liegt so nah an der Auslösung


@dataclass(frozen=True)
class Verdict:
    outcome: str
    window_start: datetime
    window_end: datetime
    lage: str | None = None
    abstand_min: float | None = None
    deviation: str | None = None


@dataclass(frozen=True)
class _Row:
    ts: datetime
    actor: str
    changed: frozenset
    new: dict
    automation: str | None


def same_target(target: dict, new: dict, tolerance: float) -> bool:
    if target.get("kind") == "preset":
        return new.get("preset_mode") == target.get("value")
    temperature = new.get("temperature")
    if temperature is None:
        return False
    try:
        return abs(float(temperature) - float(target["value"])) <= tolerance
    except (TypeError, ValueError, KeyError):
        return False


def _deviation(target: dict, new: dict) -> str:
    if target.get("kind") == "preset":
        return f"preset:{new.get('preset_mode')}"
    temperature = new.get("temperature")
    return f"{float(temperature):g}" if temperature is not None else "?"


def _load(db: Database, entity_id: str, start: datetime, end: datetime) -> list[_Row]:
    rows = db.conn.execute(
        "SELECT ts_utc, actor_class, changed_attrs, new, origin_automation FROM manual_actions "
        "WHERE entity_id = ? AND ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc",
        (entity_id, to_utc_iso(start), to_utc_iso(end))).fetchall()
    return [_Row(parse_utc(row["ts_utc"]), row["actor_class"], frozenset(row["changed_attrs"].split(",")),
                 json.loads(row["new"]), row["origin_automation"]) for row in rows]


def evaluate(db: Database, event: RuleEvent, deployment: Deployment, cfg: ExecutionConfig) -> Verdict | None:
    ts = parse_utc(event.ts_utc)
    start = ts - timedelta(minutes=cfg.eval_before_min)
    end = ts + timedelta(minutes=cfg.eval_after_min)
    if event.reason in UNSCORED_REASONS:
        return None
    if event.reason == "bereits_erreicht":
        return Verdict("ohne_wirkung", start, end)
    rows = _load(db, deployment.climate_entity, start, end)
    humans = [row for row in rows if row.actor in ("manual", "device") and row.changed & RELEVANT]
    own_change = event.result == "ausgefuehrt"
    others = [row for row in rows if row.actor == "automation" and row.automation != deployment.ha_automation
              and not (own_change and 0 <= (row.ts - ts).total_seconds() <= OWN_CHANGE_WINDOW_S)]
    target = event.target

    if own_change:
        after = [row for row in humans if row.ts > ts]
        if after:
            last = after[-1]
            if any(ts < other.ts < last.ts for other in others):
                return Verdict("unterbrochen", start, end)
            distance = (last.ts - ts).total_seconds() / 60
            if same_target(target, last.new, cfg.target_tolerance):
                return Verdict("bestaetigt", start, end, "nachher", distance)
            return Verdict("korrigiert", start, end, "nachher", distance, _deviation(target, last.new))
        if any(other.ts > ts for other in others):
            return Verdict("unterbrochen", start, end)
        return Verdict("bestaetigt", start, end)

    if not humans:
        if any(other.ts > ts for other in others):
            return Verdict("unterbrochen", start, end)
        return Verdict("neutral", start, end)
    last = humans[-1]
    lage = "vorher" if last.ts < ts else "nachher"
    distance = (last.ts - ts).total_seconds() / 60
    if lage == "nachher":
        interrupted = any(ts < other.ts < last.ts for other in others)
    else:
        interrupted = any(last.ts < other.ts < ts for other in others)
    if interrupted:
        return Verdict("unterbrochen", start, end, lage, distance)
    if same_target(target, last.new, cfg.target_tolerance):
        return Verdict("treffer", start, end, lage, distance)
    return Verdict("fehlausloesung", start, end, lage, distance, _deviation(target, last.new))


def rule_stats(store: ExecStore, rule_id: str, cfg: ExecutionConfig) -> Stats:
    rows = store.stats_rows(rule_id)
    outcomes = Counter(row["outcome"] for row in rows if row["outcome"])
    reasons = Counter(row["reason"] for row in rows if row["result"] == "nicht_ausgefuehrt" and row["reason"])
    good = outcomes["treffer"] + outcomes["bestaetigt"]
    bad = outcomes["fehlausloesung"] + outcomes["korrigiert"]
    scored = good + bad
    shadow_scored = outcomes["treffer"] + outcomes["fehlausloesung"]
    active_scored = outcomes["bestaetigt"] + outcomes["korrigiert"]
    timed = [row for row in rows if row["outcome"] in ("treffer", "fehlausloesung", "korrigiert", "bestaetigt")
             and row["abstand_min"] is not None]
    distances = [row["abstand_min"] for row in timed]
    deviations = Counter(row["deviation"] for row in rows
                         if row["deviation"] and row["outcome"] in ("fehlausloesung", "korrigiert"))
    if scored < cfg.min_scored:
        maturity = "zu_wenig_daten"
    else:
        maturity = "gruen" if bad / scored < cfg.max_fail_rate else "rot"
    return Stats(
        events=len(rows), scored=scored, good=good, treffer=outcomes["treffer"],
        fehlausloesung=outcomes["fehlausloesung"], neutral=outcomes["neutral"], bestaetigt=outcomes["bestaetigt"],
        korrigiert=outcomes["korrigiert"], unterbrochen=outcomes["unterbrochen"],
        ohne_wirkung=outcomes["ohne_wirkung"], ausgebremst=reasons["handeingriff"], reasons=dict(reasons),
        trefferquote=outcomes["treffer"] / shadow_scored if shadow_scored else None,
        korrekturquote=outcomes["korrigiert"] / active_scored if active_scored else None,
        median_abstand_min=statistics.median(distances) if distances else None,
        anteil_vorher=(sum(1 for row in timed if row["lage"] == "vorher") / len(timed)) if timed else None,
        abweichung=deviations.most_common(1)[0][0] if deviations else None,
        reife=maturity,
        text=f"{good} von {scored} gewertet richtig, {outcomes['neutral']} ohne Reaktion, "
             f"{outcomes['ohne_wirkung']} ohne Wirkung",
    )
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_outcomes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/outcomes.py ai/tests/test_outcomes.py
git commit -m "feat(ai): Auswertung der Regel-Ausloesungen und Kennzahlen" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 13: Bedienelemente (Discovery) und Dashboard

**Files:**
- Create: `ai/execution/controls.py`
- Test: `ai/tests/test_controls.py`

**Interfaces:**
- Consumes: `execution.mqtt` (`DEVICE`, `DISCOVERY`, `STATUS_TOPIC`, `rule_entities`), `execution.helpers.STOP_NAME`.
- Produces:
  - Konstanten `MODES = ("weekly", "clusters", "scan")`, `NO_SELECTION = "–"`, `JOB_LABELS: dict[str, str]` (Schlüssel: `status`, `signals`, `scan`, `llm_dry`, `llm_run`, `candidates`, `compare`, `deploy`, `undeploy`, `check`).
  - Entity-IDs: `button_entity(name) -> "button.homeai_job_<name>"`, `SELECT_MODE = "select.homeai_mode"`, `SELECT_CANDIDATE = "select.homeai_candidate"`, `SELECT_RULE = "select.homeai_rule"`, `SWITCH_ARM = "switch.homeai_llm_arm"`, `SENSOR_JOB = "sensor.homeai_job"`, `SENSOR_RESULT = "sensor.homeai_result"`, `BINARY_CONTAINER = "binary_sensor.homeai_container"`.
  - MQTT-Themen der Bedienung: Befehl `homeai/cmd/<name>` (Knopf: Nutzlast `PRESS`; `llm_arm`: `ON`/`OFF`), Auswahl `homeai/sel/<mode|candidate|rule>/set` und `…/state`, Freigabe-Zustand `homeai/state/llm_arm`, Auftragszustand `homeai/job/state`, Ergebnis `homeai/job/result`.
  - `control_entities(candidate_options: list[str], rule_options: list[str]) -> list[tuple[str, dict]]`, `async publish_controls(bus, candidate_options, rule_options)`
  - `RuleCard(rule_id: str, label: str)`, `build_dashboard(rules: list[RuleCard], room_keys: list[str]) -> dict`

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_controls.py`:

```python
import json

from execution.controls import (BINARY_CONTAINER, JOB_LABELS, MODES, NO_SELECTION, SELECT_CANDIDATE, SELECT_MODE,
                                SELECT_RULE, SENSOR_JOB, SENSOR_RESULT, SWITCH_ARM, RuleCard, build_dashboard,
                                button_entity, control_entities, publish_controls)
from execution.helpers import STOP_NAME, helper_names
from execution.mqtt import InMemoryBus, rule_entities


def entities(candidates=("7: Bad Mo-Fr 06:30 -> Preset comfort",), rules=("r7: Bad",)):
    return control_entities(list(candidates), list(rules))


def by_id():
    return {payload["default_entity_id"]: payload for _, payload in entities()}


def test_every_job_has_a_button_with_its_command_topic():
    found = by_id()
    for name in JOB_LABELS:
        payload = found[button_entity(name)]
        assert payload["command_topic"] == f"homeai/cmd/{name}" and payload["payload_press"] == "PRESS"
    assert set(JOB_LABELS) == {"status", "signals", "scan", "llm_dry", "llm_run", "candidates", "compare", "deploy",
                               "undeploy", "check"}


def test_selects_carry_their_options_and_fall_back_to_a_placeholder():
    found = by_id()
    assert found[SELECT_MODE]["options"] == list(MODES) and found[SELECT_MODE]["command_topic"] == "homeai/sel/mode/set"
    assert found[SELECT_CANDIDATE]["options"] == ["7: Bad Mo-Fr 06:30 -> Preset comfort"]
    assert found[SELECT_RULE]["state_topic"] == "homeai/sel/rule/state"
    empty = {payload["default_entity_id"]: payload for _, payload in control_entities([], [])}
    assert empty[SELECT_CANDIDATE]["options"] == [NO_SELECTION] and empty[SELECT_RULE]["options"] == [NO_SELECTION]


def test_cost_switch_and_status_entities():
    found = by_id()
    arm = found[SWITCH_ARM]
    assert (arm["command_topic"], arm["state_topic"]) == ("homeai/cmd/llm_arm", "homeai/state/llm_arm")
    assert found[SENSOR_JOB]["state_topic"] == "homeai/job/state" and found[SENSOR_JOB]["json_attributes_topic"] == "homeai/job/state"
    assert found[SENSOR_RESULT]["state_topic"] == "homeai/job/result"
    assert found[BINARY_CONTAINER]["state_topic"] == "homeai/status" and found[BINARY_CONTAINER]["payload_off"] == "offline"
    assert "availability_topic" not in found[BINARY_CONTAINER] and "availability_topic" not in found[SENSOR_RESULT]


def test_controls_become_unavailable_with_the_container_but_rule_switches_do_not():
    found = by_id()
    for entity in (button_entity("scan"), SELECT_MODE, SWITCH_ARM, SENSOR_JOB):
        assert found[entity]["availability_topic"] == "homeai/status"
    for _, payload in rule_entities("r7", "Bad"):
        if payload["default_entity_id"].startswith("switch."):
            assert "availability_topic" not in payload


def test_unique_ids_and_topics_are_unique():
    pairs = entities()
    assert len({payload["unique_id"] for _, payload in pairs}) == len(pairs)
    assert len({topic for topic, _ in pairs}) == len(pairs)


async def test_publish_controls_retains_the_discovery():
    bus = InMemoryBus()
    await publish_controls(bus, ["7: x"], ["r7: y"])
    assert len(bus.retained) == len(entities()) and all(message.retain for message in bus.published)
    assert json.loads(bus.retained["homeassistant/select/homeai/candidate/config"])["options"] == ["7: x"]


def referenced(dashboard) -> set[str]:
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "entities":
                    found.update(item if isinstance(item, str) else item["entity"] for item in value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(dashboard)
    return found


def test_dashboard_references_only_existing_entities():
    dashboard = build_dashboard([RuleCard("r7", "Bad: Mo-Fr 06:30 -> Preset comfort")], ["bad", "wohnzimmer"])
    known = set(by_id()) | {payload["default_entity_id"] for _, payload in rule_entities("r7", "x")}
    known |= {f"input_boolean.{STOP_NAME}"} | {f"input_boolean.{helper_names(key).toggle}" for key in ("bad", "wohnzimmer")}
    assert referenced(dashboard) <= known
    assert {view["title"] for view in dashboard["views"]} == {"Übersicht", "Regeln", "Kandidaten", "Analyse", "System"}
    rules_view = next(view for view in dashboard["views"] if view["title"] == "Regeln")
    assert "switch.homeai_r7_aktiv" in referenced(rules_view) and "Mo-Fr 06:30" in json.dumps(rules_view)


def test_dashboard_without_rules_says_so():
    dashboard = build_dashboard([], ["bad"])
    rules_view = next(view for view in dashboard["views"] if view["title"] == "Regeln")
    assert "Noch keine Regel" in json.dumps(rules_view, ensure_ascii=False)
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_controls.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.controls'`.

- [ ] **Step 3: Implementieren**

`ai/execution/controls.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from .helpers import STOP_NAME, helper_names
from .mqtt import DEVICE, DISCOVERY, STATUS_TOPIC

MODES = ("weekly", "clusters", "scan")
NO_SELECTION = "–"
JOB_LABELS = {
    "status": "Status aktualisieren",
    "signals": "Signale anzeigen",
    "scan": "Scan (ohne Modell)",
    "llm_dry": "Analyse: Trockenlauf",
    "llm_run": "Analyse mit Modell starten",
    "candidates": "Bestandene Kandidaten anzeigen",
    "compare": "Läufe vergleichen",
    "deploy": "Kandidat im Schatten anlegen",
    "undeploy": "Regel entfernen",
    "check": "Systemprüfung",
}

SELECT_MODE = "select.homeai_mode"
SELECT_CANDIDATE = "select.homeai_candidate"
SELECT_RULE = "select.homeai_rule"
SWITCH_ARM = "switch.homeai_llm_arm"
SENSOR_JOB = "sensor.homeai_job"
SENSOR_RESULT = "sensor.homeai_result"
BINARY_CONTAINER = "binary_sensor.homeai_container"

AVAILABILITY = {"availability_topic": STATUS_TOPIC, "payload_available": "online", "payload_not_available": "offline"}


def button_entity(name: str) -> str:
    return f"button.homeai_job_{name}"


def _entry(component: str, object_id: str, name: str, **extra) -> tuple[str, dict]:
    topic = f"{DISCOVERY}/{component}/homeai/{object_id}/config"
    return topic, {"name": name, "unique_id": f"homeai_{object_id}",
                   "default_entity_id": f"{component}.homeai_{object_id}", "device": DEVICE, **extra}


def _select(object_id: str, what: str, name: str, options: list[str]) -> tuple[str, dict]:
    return _entry("select", object_id, name, options=options or [NO_SELECTION],
                  command_topic=f"homeai/sel/{what}/set", state_topic=f"homeai/sel/{what}/state", **AVAILABILITY)


def control_entities(candidate_options: list[str], rule_options: list[str]) -> list[tuple[str, dict]]:
    entries = [_entry("button", f"job_{name}", label, command_topic=f"homeai/cmd/{name}", payload_press="PRESS",
                      **AVAILABILITY) for name, label in JOB_LABELS.items()]
    entries += [
        _select("mode", "mode", "Modus der Analyse", list(MODES)),
        _select("candidate", "candidate", "Kandidat", candidate_options),
        _select("rule", "rule", "Regel", rule_options),
        _entry("switch", "llm_arm", "Kosten freigeben", command_topic="homeai/cmd/llm_arm",
               state_topic="homeai/state/llm_arm", payload_on="ON", payload_off="OFF", **AVAILABILITY),
        _entry("sensor", "job", "Auftrag", state_topic="homeai/job/state", value_template="{{ value_json.state }}",
               json_attributes_topic="homeai/job/state", **AVAILABILITY),
        _entry("sensor", "result", "Ergebnis", state_topic="homeai/job/result",
               value_template="{{ value_json.title }}", json_attributes_topic="homeai/job/result"),
        _entry("binary_sensor", "container", "Container", state_topic=STATUS_TOPIC, payload_on="online",
               payload_off="offline", device_class="connectivity"),
    ]
    return entries


async def publish_controls(bus, candidate_options: list[str], rule_options: list[str]) -> None:
    for topic, payload in control_entities(candidate_options, rule_options):
        await bus.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)


@dataclass(frozen=True)
class RuleCard:
    rule_id: str
    label: str


RESULT_CARD = {"type": "markdown", "title": "Letztes Ergebnis",
               "content": "{{ state_attr('" + SENSOR_RESULT + "', 'markdown') or 'Noch kein Ergebnis.' }}"}


def _rule_card(rule: RuleCard) -> dict:
    base = f"homeai_{rule.rule_id}"
    return {"type": "vertical-stack", "cards": [
        {"type": "entities", "title": f"Regel {rule.rule_id}: {rule.label}", "entities": [
            f"switch.{base}_aktiv", f"sensor.{base}_kennzahlen", f"sensor.{base}_trefferquote",
            f"sensor.{base}_abweichung"]},
        {"type": "markdown", "content": "{{ state_attr('sensor." + base + "_kennzahlen', 'text') or 'Noch keine Auslösung.' }}"},
    ]}


def build_dashboard(rules: list[RuleCard], room_keys: list[str]) -> dict:
    rooms = [f"input_boolean.{helper_names(key).toggle}" for key in room_keys]
    overview = {"title": "Übersicht", "path": "uebersicht", "icon": "mdi:view-dashboard", "cards": [
        {"type": "entities", "title": "Betrieb", "entities": [
            BINARY_CONTAINER, SENSOR_JOB, f"input_boolean.{STOP_NAME}", *rooms]},
        {"type": "entities", "title": "Aufträge", "entities": [button_entity("status"), button_entity("check")]},
        RESULT_CARD]}
    rule_cards = [_rule_card(rule) for rule in rules] or [
        {"type": "markdown", "content": "Noch keine Regel angelegt. Unter „Kandidaten“ eine bestandene Regel wählen."}]
    return {"title": "KI-Regeln", "views": [
        overview,
        {"title": "Regeln", "path": "regeln", "icon": "mdi:robot", "cards": rule_cards},
        {"title": "Kandidaten", "path": "kandidaten", "icon": "mdi:format-list-checks", "cards": [
            {"type": "entities", "title": "Anlegen und Entfernen", "entities": [
                button_entity("candidates"), SELECT_CANDIDATE, button_entity("deploy"),
                SELECT_RULE, button_entity("undeploy")]},
            RESULT_CARD]},
        {"title": "Analyse", "path": "analyse", "icon": "mdi:chart-line", "cards": [
            {"type": "entities", "title": "Auswertung der Daten", "entities": [
                button_entity("signals"), button_entity("scan"), SELECT_MODE, button_entity("llm_dry"), SWITCH_ARM,
                button_entity("llm_run"), button_entity("compare")]},
            RESULT_CARD]},
        {"title": "System", "path": "system", "icon": "mdi:cog", "cards": [
            {"type": "entities", "title": "System", "entities": [
                SENSOR_JOB, button_entity("status"), button_entity("check")]},
            RESULT_CARD]},
    ]}
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_controls.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/controls.py ai/tests/test_controls.py
git commit -m "feat(ai): Bedienelemente per Discovery und Dashboard ki-regeln" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Regeln anlegen, entfernen und prüfen

**Files:**
- Create: `ai/execution/deploy.py`
- Create: `ai/tests/execution_fixture.py`
- Test: `ai/tests/test_deploy.py`

**Interfaces:**
- Consumes: `translate`, `climate_for_room`, `load_signal_refs` (Aufgabe 9); `HaRest`, `HaWs` (Aufgabe 10); `InMemoryBus`/Bus mit `publish`, `publish_rule_entities`, `clear_rule_entities` (Aufgabe 11); `ExecStore`, `Deployment` (Aufgabe 8); `eingriff_automations`, `helper_names`, `helper_specs`, `STOP_NAME` (Aufgabe 7); `RuleCard`, `build_dashboard` (Aufgabe 13); `analysis.candidates.describe`, `analysis.pseudonym.build_pseudonyms`, `analysis.store.Store`, `analysis.models.STATUS_PASSED`.
- Produces:
  - `execution.deploy.DeployError(RuntimeError)`, `BLUEPRINT_VERSION = "1.0"`
  - `execution.deploy.HeatingRoom(automation_entity, heating_entity, flat_state, ki_toggle, winter_toggle, schedule_entity, nightly_time)`
  - `execution.deploy.DeployResult(rule_id: str, automation_entity: str, hints: list[str])`
  - `execution.deploy.time_hints(rule_times: list[str], flanks: set[str], nightly: str | None, window_min: int = 10) -> list[str]`
  - `execution.deploy.Deployer(cfg, db, store, astore, acfg, rest, ws, bus, now=utcnow)` mit `find_retries = 10`, `find_delay = 0.5` und den Methoden `async heating_room(room) -> HeatingRoom`, `async init(key: str | None = None) -> list[str]`, `async deploy(candidate_id: int) -> DeployResult`, `async undeploy(rule_id: str) -> None`, `async check() -> list[str]` (Zeilen beginnen mit `OK    ` oder `FEHLT `), `async refresh_dashboard() -> None`.
  - Regel-ID `r<candidate_id>`, Konfigurations-ID der Automation `homeai_rule_<rule_id>`, Schalter `switch.homeai_<rule_id>_aktiv`.

- [ ] **Step 1: Failing tests schreiben**

`ai/tests/execution_fixture.py`:

```python
from __future__ import annotations

from analysis.models import STATUS_PASSED, Candidate
from collector.models import EntityRule
from execution.helpers import slug
from tests.ha_template import utc

AREA = "a05f1cd02cf34142af915331271a6986"            # Bad
CLIMATE = "climate.better_thermostat_bathroom"
HEATING_AUTOMATION = "automation.heating_control_bathroom"
TOGGLE = "input_boolean.ki_regeln_bad"
ARRIVAL = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180,
           "offset_min": 0}


class Helpers:
    """Merkt die per WebSocket angelegten Helfer (input_boolean, input_datetime)."""

    def __init__(self, fake_hass):
        self.items: dict[str, list[dict]] = {"input_boolean": [], "input_datetime": []}
        self.created: list[tuple[str, str]] = []
        for domain in self.items:
            fake_hass.responses[f"{domain}/list"] = self._lister(domain)
            fake_hass.responses[f"{domain}/create"] = self._creator(domain)

    def _lister(self, domain):
        return lambda message: list(self.items[domain])

    def _creator(self, domain):
        def create(message):
            item = {"id": slug(message["name"]), "name": message["name"]}
            self.items[domain].append(item)
            self.created.append((domain, message["name"]))
            return item
        return create


class Dashboards:
    def __init__(self, fake_hass):
        self.items: list[dict] = []
        self.saved: list[dict] = []
        fake_hass.responses["lovelace/dashboards/list"] = lambda message: list(self.items)
        fake_hass.responses["lovelace/dashboards/create"] = lambda message: self.items.append(
            {"url_path": message["url_path"]}) or {}
        fake_hass.responses["lovelace/config/save"] = lambda message: self.saved.append(message) or None


def seed_database(db, astore, *, trigger=None, room=AREA, status=STATUS_PASSED) -> int:
    now = utc(2026, 1, 1)
    if db.conn.execute("SELECT 1 FROM entities WHERE entity_id = ?", (CLIMATE,)).fetchone() is None:
        db.upsert_entity(EntityRule(CLIMATE, "climate", "heating", attrs=("temperature", "preset_mode")), AREA, now)
        db.upsert_entity(EntityRule("person.fabian", "person", "shared"), None, now)
        handles = astore.assign_handles(["person.fabian"])
        astore.update_signal(handles["person.fabian"], "person", "categorical", "Anwesenheit person_1")
    run_id = astore.start_run("scan", "m", {}, now)
    candidate = Candidate(room=room, trigger=trigger or ARRIVAL, action={"type": "preset", "value": "comfort"},
                          status=status)
    candidate_id = astore.add_candidate(run_id, candidate)
    astore.commit()
    return candidate_id


def seed_home_assistant(fake_rest, fake_hass, *, toggle=TOGGLE):
    helpers, dashboards = Helpers(fake_hass), Dashboards(fake_hass)
    inputs = {"heating_entities": CLIMATE, "flat_occupation_state": "input_select.flat_occupation_state",
              "heating_on_off": "input_boolean.heating_season", "schedule_entity": "schedule.heating_schedule_bathroom"}
    if toggle:
        inputs["ki_rules_toggle"] = toggle
    fake_rest.set_state(CLIMATE, "heat", preset_modes=["none", "eco", "comfort"], temperature=17.0, preset_mode="eco")
    fake_rest.set_state(HEATING_AUTOMATION, "on", id="hc_bad")
    fake_rest.automations["hc_bad"] = {"alias": "Heating Control - Bathroom",
                                       "use_blueprint": {"path": "heating_control.yaml", "input": inputs}}
    fake_hass.responses["blueprint/list"] = {"homeai/ki_regel.yaml": {}}
    fake_hass.responses["schedule/list"] = [
        {"id": "heating_schedule_bathroom", "monday": [{"from": "06:30:00", "to": "08:00:00"}]}]
    return helpers, dashboards
```

`ai/tests/test_deploy.py`:

```python
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from analysis.store import Store as AnalysisStore
from collector.hass_ws import HassClient
from execution.deploy import DeployError, Deployer, time_hints
from execution.ha_api import HaError, HaRest, HaWs
from execution.helpers import STOP_NAME
from execution.mqtt import InMemoryBus, rule_discovery_topics
from execution.store import ExecStore
from tests.execution_fixture import (ARRIVAL, CLIMATE, HEATING_AUTOMATION, TOGGLE, seed_database,
                                     seed_home_assistant)
from tests.ha_template import utc

NOW = utc(2026, 10, 15, 12, 0)


@pytest_asyncio.fixture
async def world(db, acfg, ecfg, fake_rest, fake_hass):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    helpers, dashboards = seed_home_assistant(fake_rest, fake_hass)
    rest = HaRest(fake_rest.base, "secret")
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    bus = InMemoryBus()
    deployer = Deployer(ecfg, db, store, astore, acfg, rest, HaWs(client), bus, now=lambda: NOW)
    deployer.find_delay = 0
    try:
        yield SimpleNamespace(deployer=deployer, store=store, astore=astore, bus=bus, fake_rest=fake_rest,
                              helpers=helpers, dashboards=dashboards, candidate_id=candidate_id, db=db)
    finally:
        await client.close()
        await rest.close()


# --- init ----------------------------------------------------------------------------

async def test_init_creates_helpers_and_automations_once(world):
    lines = await world.deployer.init("bad")
    assert sorted(name for _, name in world.helpers.created) == sorted(
        [STOP_NAME, "ki_regeln_bad", "ki_eingriff_bad", "ki_aufruf_bad"])
    assert {"homeai_aufrufe_bad", "homeai_beobachter_bad"} <= set(world.fake_rest.automations)
    assert any("input_boolean.ki_regeln_bad" in line and HEATING_AUTOMATION in line for line in lines)
    await world.deployer.init("bad")
    assert len(world.helpers.created) == 4


async def test_init_without_a_key_reports_rooms_without_a_thermostat(world):
    lines = await world.deployer.init()
    assert any(line.startswith("schlafzimmer: FEHLER") for line in lines)
    assert "homeai_aufrufe_bad" in world.fake_rest.automations


# --- deploy ----------------------------------------------------------------------------

async def test_deploy_creates_the_rule_in_shadow_mode(world):
    result = await world.deployer.deploy(world.candidate_id)
    assert result.rule_id == "r1" and result.automation_entity.startswith("automation.ki_regel_r1")
    blueprint = world.fake_rest.automations["homeai_rule_r1"]["use_blueprint"]
    assert blueprint["path"] == "homeai/ki_regel.yaml"
    inputs = blueprint["input"]
    assert inputs["rule_id"] == "r1" and inputs["heating_entity"] == CLIMATE
    assert inputs["rule_toggle"] == "switch.homeai_r1_aktiv" and inputs["room_toggle"] == TOGGLE
    assert inputs["stop_toggle"] == f"input_boolean.{STOP_NAME}"
    assert inputs["last_intervention"] == "input_datetime.ki_eingriff_bad"
    assert inputs["heating_automation"] == HEATING_AUTOMATION
    assert inputs["flat_state"] == "input_select.flat_occupation_state"
    assert inputs["winter_toggle"] == "input_boolean.heating_season"
    assert inputs["blocked_states"] == ["Vacation"] and inputs["min_distance_min"] == 60
    assert (inputs["setpoint_min"], inputs["setpoint_max"]) == (15.0, 25.0)
    assert (inputs["action_kind"], inputs["action_preset"]) == ("preset", "comfort")
    assert inputs["rule_triggers"][0]["entity_id"] == "person.fabian"
    deployment = world.store.get_deployment("r1")
    assert (deployment.state, deployment.climate_entity, deployment.config_id) == ("schatten", CLIMATE, "homeai_rule_r1")
    assert deployment.ha_automation == result.automation_entity and "Bad" in deployment.description
    assert set(world.bus.retained) == set(rule_discovery_topics("r1"))
    assert "switch.homeai_r1_aktiv" in json.dumps(world.dashboards.saved[-1]["config"])


async def test_deploy_without_a_winter_switch_omits_the_input(world):
    del world.fake_rest.automations["hc_bad"]["use_blueprint"]["input"]["heating_on_off"]
    await world.deployer.deploy(world.candidate_id)
    assert "winter_toggle" not in world.fake_rest.automations["homeai_rule_r1"]["use_blueprint"]["input"]


async def test_deploy_refuses_candidates_that_did_not_pass(world):
    world.astore.set_candidate_status(world.candidate_id, "abgelehnt")
    world.astore.commit()
    with pytest.raises(DeployError, match="bestanden"):
        await world.deployer.deploy(world.candidate_id)


async def test_deploy_needs_the_toggle_in_heating_control(world):
    del world.fake_rest.automations["hc_bad"]["use_blueprint"]["input"]["ki_rules_toggle"]
    with pytest.raises(DeployError, match="ki_rules_toggle"):
        await world.deployer.deploy(world.candidate_id)
    assert world.store.list_deployments() == [] and world.bus.retained == {}


async def test_deploy_refuses_unknown_rooms_legacy_candidates_and_unknown_ids(world):
    other = seed_database(world.db, world.astore, room="keller")
    with pytest.raises(DeployError, match="Raum"):
        await world.deployer.deploy(other)
    legacy = seed_database(world.db, world.astore, trigger={"type": "event", "event": "person_arrives",
                                                            "person": "person.fabian"})
    with pytest.raises(DeployError, match="alte"):
        await world.deployer.deploy(legacy)
    with pytest.raises(DeployError, match="existiert nicht"):
        await world.deployer.deploy(999)


async def test_deploy_refuses_a_thermostat_that_differs_from_heating_control(world):
    world.fake_rest.automations["hc_bad"]["use_blueprint"]["input"]["heating_entities"] = "climate.other"
    with pytest.raises(DeployError, match="stimmt nicht überein"):
        await world.deployer.deploy(world.candidate_id)


async def test_deploy_refuses_a_preset_the_thermostat_does_not_offer(world):
    world.fake_rest.set_state(CLIMATE, "heat", preset_modes=["none", "eco"])
    with pytest.raises(DeployError, match="bietet das Preset"):
        await world.deployer.deploy(world.candidate_id)
    assert world.store.list_deployments() == [] and world.bus.retained == {}


async def test_deploy_twice_is_refused(world):
    await world.deployer.deploy(world.candidate_id)
    with pytest.raises(DeployError, match="bereits"):
        await world.deployer.deploy(world.candidate_id)


async def test_failed_save_leaves_nothing_behind(world):
    world.fake_rest.fail_save = True
    with pytest.raises(HaError):
        await world.deployer.deploy(world.candidate_id)
    assert world.store.list_deployments() == [] and world.bus.retained == {}
    assert "homeai_rule_r1" not in world.fake_rest.automations


async def test_deploy_reports_times_next_to_flanks(world):
    candidate = seed_database(world.db, world.astore, trigger={"type": "time", "weekdays": [0], "time": "06:35"})
    result = await world.deployer.deploy(candidate)
    assert len(result.hints) == 1 and "06:35" in result.hints[0] and "06:30" in result.hints[0]


def test_time_hints_flag_rules_next_to_flanks():
    hints = time_hints(["06:35", "12:00"], {"06:30", "18:30"}, "01:00:00")
    assert len(hints) == 1 and "06:35" in hints[0] and "06:30" in hints[0]
    assert time_hints(["01:05"], set(), "01:00:00")
    assert time_hints(["23:55"], {"00:02"}, None)
    assert time_hints([], {"06:30"}, None) == []


# --- undeploy und check ------------------------------------------------------------------

async def test_undeploy_removes_everything_but_the_history(world):
    await world.deployer.deploy(world.candidate_id)
    await world.deployer.undeploy("r1")
    assert "homeai_rule_r1" not in world.fake_rest.automations and world.bus.retained == {}
    assert world.store.get_deployment("r1").state == "entfernt"
    assert "r1" not in json.dumps(world.dashboards.saved[-1]["config"])
    with pytest.raises(DeployError, match="bereits entfernt"):
        await world.deployer.undeploy("r1")
    with pytest.raises(DeployError, match="unbekannt"):
        await world.deployer.undeploy("zz")


async def test_check_reports_what_is_missing_and_what_is_fine(world):
    lines = await world.deployer.check()
    assert any(line.startswith("FEHLT") and "Sammelschalter" in line for line in lines)
    assert any(line.startswith("OK    ") and "Blueprint homeai/ki_regel.yaml" in line for line in lines)
    await world.deployer.init("bad")
    for entity in (f"input_boolean.{STOP_NAME}", TOGGLE, "input_datetime.ki_eingriff_bad", "input_datetime.ki_aufruf_bad"):
        world.fake_rest.set_state(entity, "on")
    lines = await world.deployer.check()
    assert [line for line in lines if line.startswith("FEHLT bad:")] == []
    await world.deployer.deploy(world.candidate_id)
    del world.fake_rest.automations["homeai_rule_r1"]
    lines = await world.deployer.check()
    assert any(line.startswith("FEHLT") and "Regel r1" in line for line in lines)
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_deploy.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.deploy'`.

- [ ] **Step 3: Implementieren**

`ai/execution/deploy.py`:

```python
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from analysis.candidates import describe
from analysis.config import AnalysisConfig
from analysis.models import STATUS_PASSED
from analysis.pseudonym import build_pseudonyms
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import to_utc_iso, utcnow

from .config import ExecutionConfig, RoomConfig
from .controls import RuleCard, build_dashboard
from .ha_api import HaError, HaRest, HaWs
from .helpers import STOP_NAME, eingriff_automations, helper_names, helper_specs
from .models import Deployment
from .mqtt import clear_rule_entities, publish_rule_entities, rule_label
from .store import ExecStore
from .translate import TranslateError, climate_for_room, load_signal_refs, translate

log = logging.getLogger("execution")

BLUEPRINT_VERSION = "1.0"
TIME_RE = re.compile(r"^\d\d:\d\d")


class DeployError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeatingRoom:
    automation_entity: str
    heating_entity: str
    flat_state: str
    ki_toggle: str | None
    winter_toggle: str | None
    schedule_entity: str | None
    nightly_time: str | None


@dataclass(frozen=True)
class DeployResult:
    rule_id: str
    automation_entity: str
    hints: list[str]


def _minutes(text: str) -> int:
    hours, minutes = text[:5].split(":")
    return int(hours) * 60 + int(minutes)


def time_hints(rule_times: list[str], flanks: set[str], nightly: str | None, window_min: int = 10) -> list[str]:
    marks = sorted({*flanks, *([nightly[:5]] if nightly else [])})
    hints = []
    for rule_time in rule_times:
        for mark in marks:
            distance = abs(_minutes(rule_time) - _minutes(mark))
            distance = min(distance, 1440 - distance)
            if distance <= window_min:
                hints.append(f"Die Regel löst um {rule_time} aus, {distance} Minuten neben der Flanke {mark} von "
                             "heating_control; bei gleichzeitigem Schalten gilt der später gesetzte Wert.")
    return hints


def _entity_input(inputs: dict, key: str) -> str | None:
    value = inputs.get(key)
    return value if isinstance(value, str) and value else None


class Deployer:
    def __init__(self, cfg: ExecutionConfig, db: Database, store: ExecStore, astore: AnalysisStore,
                 acfg: AnalysisConfig, rest: HaRest, ws: HaWs, bus, now: Callable[[], datetime] = utcnow):
        self.cfg, self.db, self.store, self.astore, self.acfg = cfg, db, store, astore, acfg
        self.rest, self.ws, self.bus, self.now = rest, ws, bus, now
        self.find_retries = 10
        self.find_delay = 0.5

    # --- Lesen der Raum-Konfiguration -----------------------------------------------------

    async def heating_room(self, room: RoomConfig) -> HeatingRoom:
        state = await self.rest.get_state(room.heating_automation)
        if state is None:
            raise DeployError(f"Die Automation {room.heating_automation} existiert nicht in Home Assistant")
        config_id = state["attributes"].get("id")
        config = await self.rest.automation_config(config_id) if config_id else None
        inputs = ((config or {}).get("use_blueprint") or {}).get("input")
        if not inputs:
            raise DeployError(f"{room.heating_automation} ist keine lesbare Blueprint-Automation")
        heating, flat = _entity_input(inputs, "heating_entities"), _entity_input(inputs, "flat_occupation_state")
        if not heating or not flat:
            raise DeployError(f"{room.heating_automation} enthält keinen Heizkörper oder Wohnungszustand")
        return HeatingRoom(room.heating_automation, heating, flat, _entity_input(inputs, "ki_rules_toggle"),
                           _entity_input(inputs, "heating_on_off"), _entity_input(inputs, "schedule_entity"),
                           str(inputs.get("nightly_eco_time") or "01:00:00"))

    # --- init ------------------------------------------------------------------------------------

    async def init(self, key: str | None = None) -> list[str]:
        lines = [f"Sammelschalter: {await self.ws.ensure_helper('input_boolean', STOP_NAME, icon='mdi:robot-off')}"]
        rooms = [self.cfg.room_by_key(key)] if key else list(self.cfg.rooms.values())
        for room in rooms:
            try:
                climate = climate_for_room(self.db, room.area)
                for domain, name, options in helper_specs(room.key):
                    await self.ws.ensure_helper(domain, name, **options)
                for config_id, config in eingriff_automations(room.key, climate):
                    await self.rest.save_automation(config_id, config)
            except (TranslateError, HaError) as exc:
                lines.append(f"{room.key}: FEHLER {exc}")
                continue
            lines.append(f"{room.key}: Setze in {room.heating_automation} die Eingabe "
                         f"'KI-Regeln erlaubt (Schalter)' auf input_boolean.{helper_names(room.key).toggle}")
        return lines

    # --- deploy ------------------------------------------------------------------------------------

    async def deploy(self, candidate_id: int) -> DeployResult:
        try:
            candidate = self.astore.get_candidate(candidate_id)
        except KeyError as exc:
            raise DeployError(f"Kandidat {candidate_id} existiert nicht") from exc
        if candidate.status != STATUS_PASSED:
            raise DeployError(f"Kandidat {candidate_id} hat den Status {candidate.status}, nötig ist bestanden")
        rule_id = f"r{candidate_id}"
        existing = self.store.get_deployment(rule_id)
        if existing is not None and existing.state != "entfernt":
            raise DeployError(f"Die Regel {rule_id} ist bereits angelegt")
        room = self.cfg.rooms.get(candidate.room)
        if room is None:
            raise DeployError(f"Der Raum {candidate.room} ist in execution.yaml nicht eingetragen")
        info = await self.heating_room(room)
        if not info.ki_toggle:
            raise DeployError(f"In {room.heating_automation} fehlt die Eingabe ki_rules_toggle. "
                              f"Zuerst `execution init --room {room.key}` ausführen und die Eingabe setzen.")
        try:
            climate = climate_for_room(self.db, room.area)
            rule = translate(candidate, load_signal_refs(self.db), setpoint_min=self.cfg.setpoint_min,
                             setpoint_max=self.cfg.setpoint_max)
        except TranslateError as exc:
            raise DeployError(str(exc)) from exc
        if info.heating_entity != climate:
            raise DeployError(f"Der Heizkörper {info.heating_entity} in {room.heating_automation} stimmt nicht überein "
                              f"mit dem aufgezeichneten {climate}")
        if rule.action_kind == "preset":
            climate_state = await self.rest.get_state(info.heating_entity)
            offered = ((climate_state or {}).get("attributes") or {}).get("preset_modes")
            if offered and rule.action_preset not in offered:
                raise DeployError(f"Der Heizkörper {info.heating_entity} bietet das Preset {rule.action_preset!r} nicht "
                                  f"an (angeboten: {', '.join(offered)})")
        pseudo = build_pseudonyms(self.db)
        place, when, action = describe(candidate, self.acfg, pseudo, self.astore.signal_descriptions())
        description = f"{place}: {when} -> {action}"
        names = helper_names(room.key)
        config_id = f"homeai_rule_{rule_id}"
        inputs = {
            "rule_id": rule_id, "heating_entity": info.heating_entity, "room_toggle": info.ki_toggle,
            "rule_toggle": f"switch.homeai_{rule_id}_aktiv", "stop_toggle": f"input_boolean.{STOP_NAME}",
            "flat_state": info.flat_state, "blocked_states": list(self.cfg.blocked_states),
            "heating_automation": info.automation_entity, "last_intervention": f"input_datetime.{names.last_intervention}",
            "min_distance_min": self.cfg.min_distance_min, "setpoint_min": self.cfg.setpoint_min,
            "setpoint_max": self.cfg.setpoint_max, "delay_min": rule.delay_min, "action_kind": rule.action_kind,
            "action_preset": rule.action_preset, "action_setpoint": rule.action_setpoint,
            "rule_triggers": rule.triggers, "rule_conditions": rule.conditions,
        }
        if info.winter_toggle:
            inputs["winter_toggle"] = info.winter_toggle
        automation = {"alias": f"KI-Regel {rule_id}: {description}", "description": candidate.hypothesis or "",
                      "use_blueprint": {"path": self.cfg.blueprint_path, "input": inputs}}
        hints = await self._hints(rule, info)
        label = rule_label(rule_id, description)
        saved = published = False
        try:
            await publish_rule_entities(self.bus, rule_id, label)
            published = True
            await self.rest.save_automation(config_id, automation)
            saved = True
            if await self.rest.automation_config(config_id) is None:
                raise DeployError("Home Assistant meldet die angelegte Automation nicht zurück")
            entity = await self.rest.find_automation_entity(config_id, retries=self.find_retries,
                                                            delay=self.find_delay)
            if entity is None:
                raise DeployError("Die angelegte Automation erscheint nicht in Home Assistant")
            self.store.add_deployment(Deployment(
                rule_id, candidate_id, candidate.room, entity, config_id, climate, "schatten", BLUEPRINT_VERSION,
                description, to_utc_iso(self.now())))
        except Exception:
            if saved:
                try:
                    await self.rest.delete_automation(config_id)
                except HaError:
                    log.exception("Die halb angelegte Automation %s konnte nicht entfernt werden", config_id)
            if published:
                await clear_rule_entities(self.bus, rule_id)
            raise
        await self.refresh_dashboard()
        return DeployResult(rule_id, entity, hints)

    async def _hints(self, rule, info: HeatingRoom) -> list[str]:
        times = [trigger["at"][:5] for trigger in rule.triggers
                 if trigger.get("trigger") == "time" and TIME_RE.match(str(trigger.get("at", "")))]
        if not times:
            return []
        flanks: set[str] = set()
        if info.schedule_entity:
            flanks = await self.ws.schedule_flanks(info.schedule_entity.split(".", 1)[1])
        return time_hints(times, flanks, info.nightly_time)

    # --- undeploy ----------------------------------------------------------------------------------

    async def undeploy(self, rule_id: str) -> None:
        deployment = self.store.get_deployment(rule_id)
        if deployment is None:
            raise DeployError(f"Die Regel {rule_id} ist unbekannt")
        if deployment.state == "entfernt":
            raise DeployError(f"Die Regel {rule_id} ist bereits entfernt")
        await self.rest.delete_automation(deployment.config_id)
        await clear_rule_entities(self.bus, rule_id)
        self.store.set_deployment_state(rule_id, "entfernt", self.now())
        await self.refresh_dashboard()

    # --- Dashboard und Prüfung -------------------------------------------------------------------

    async def refresh_dashboard(self) -> None:
        cards = [RuleCard(item.rule_id, item.description) for item in self.store.list_deployments()]
        await self.ws.ensure_dashboard(self.cfg.dashboard_path, "KI-Regeln", "mdi:robot")
        await self.ws.save_dashboard(self.cfg.dashboard_path,
                                     build_dashboard(cards, [room.key for room in self.cfg.rooms.values()]))

    async def check(self) -> list[str]:
        lines: list[str] = []

        def report(good: bool, text: str) -> None:
            lines.append(("OK    " if good else "FEHLT ") + text)

        report(self.cfg.blueprint_path in await self.ws.blueprint_paths(), f"Blueprint {self.cfg.blueprint_path} in Home Assistant")
        report(await self.rest.get_state(f"input_boolean.{STOP_NAME}") is not None, f"Sammelschalter input_boolean.{STOP_NAME}")
        for room in self.cfg.rooms.values():
            try:
                info = await self.heating_room(room)
            except DeployError as exc:
                report(False, f"{room.key}: {exc}")
                continue
            report(bool(info.ki_toggle), f"{room.key}: ki_rules_toggle in {room.heating_automation}")
            if info.ki_toggle:
                report(await self.rest.get_state(info.ki_toggle) is not None, f"{room.key}: Raumschalter {info.ki_toggle}")
            names = helper_names(room.key)
            for entity in (f"input_datetime.{names.last_intervention}", f"input_datetime.{names.last_auto}"):
                report(await self.rest.get_state(entity) is not None, f"{room.key}: Helfer {entity}")
            for config_id in (f"homeai_aufrufe_{room.key}", f"homeai_beobachter_{room.key}"):
                report(await self.rest.automation_config(config_id) is not None, f"{room.key}: Automation {config_id}")
        for deployment in self.store.list_deployments():
            report(await self.rest.automation_config(deployment.config_id) is not None,
                   f"Regel {deployment.rule_id}: Automation {deployment.config_id}")
        return lines
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_deploy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/deploy.py ai/tests/execution_fixture.py ai/tests/test_deploy.py
git commit -m "feat(ai): Regeln anlegen, entfernen und pruefen (init, deploy, undeploy, check)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Dauerbetrieb: Meldungen auswerten und Kennzahlen veröffentlichen

**Files:**
- Create: `ai/execution/service.py`
- Test: `ai/tests/test_service.py`

**Interfaces:**
- Consumes: `ExecStore` (`get_deployment`, `add_event`, `pending_events`, `save_outcome`, `set_deployment_state`, `list_deployments`), `evaluate`, `rule_stats` (Aufgabe 12), Bus mit `on`, `publish` (Aufgabe 11).
- Produces: `execution.service.Service(cfg, db, store, bus, now=utcnow)` mit `register()` (abonniert `homeai/rule/+/fired` und `homeai/rule/+/active/set`), `async on_fired(message)`, `async on_active_set(message)`, `async evaluate_pending() -> int`, `async publish_stats(rule_id)`, `async publish_all_stats()`, `async run_loop(stop: asyncio.Event, interval: float = 60.0)`. Kennzahlen gehen retained an `homeai/rule/<id>/stats` (JSON aus `Stats.to_payload()`).

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_service.py`:

```python
import asyncio
import json
from datetime import timedelta

import pytest

from collector.models import Change, EntityRule, to_utc_iso
from execution.models import Deployment
from execution.mqtt import InMemoryBus
from execution.service import Service
from execution.store import ExecStore
from tests.ha_template import utc

T0 = utc(2026, 10, 15, 4, 30)
NOW = T0 + timedelta(hours=3)


def fired(ts=T0, **overrides):
    data = {"ts": ts.isoformat(), "rule_id": "r5", "room": "climate.bt", "mode": "schatten",
            "result": "nicht_ausgefuehrt", "reason": "schatten",
            "target": {"kind": "preset", "value": "comfort"},
            "current": {"preset": "eco", "temperature": 17.0, "hvac_mode": "heat"}}
    data.update(overrides)
    return json.dumps(data)


@pytest.fixture
def setup(db, ecfg):
    store = ExecStore(db)
    store.add_deployment(Deployment("r5", 5, "bad", "automation.ki_regel_r5", "homeai_rule_r5", "climate.bt",
                                    "schatten", "1.0", "Bad: x", "2026-10-15T00:00:00.000000+00:00"))
    bus = InMemoryBus()
    service = Service(ecfg, db, store, bus, now=lambda: NOW)
    service.register()
    return service, store, bus


async def test_fired_message_is_stored_once_and_publishes_stats(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired())
    await bus.deliver("homeai/rule/r5/fired", fired())
    (event,) = store.pending_events()
    assert (event.rule_id, event.mode, event.result, event.reason) == ("r5", "schatten", "nicht_ausgefuehrt", "schatten")
    assert event.ts_utc == to_utc_iso(T0) and event.target == {"kind": "preset", "value": "comfort"}
    stats = json.loads(bus.retained["homeai/rule/r5/stats"])
    assert stats["events"] == 1 and stats["reife"] == "zu_wenig_daten"


async def test_offset_timestamps_are_normalised_to_utc(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired(ts="2026-10-15T06:30:00+02:00"))
    assert store.pending_events()[0].ts_utc == to_utc_iso(T0)


@pytest.mark.parametrize("topic,payload", [
    ("homeai/rule/zz/fired", None),                       # unbekannte Regel
    ("homeai/rule/r5/fired", "kein json"),
    ("homeai/rule/r5/fired", json.dumps({"mode": "aktiv"})),      # ohne Zeitstempel
])
async def test_unusable_messages_are_ignored(setup, topic, payload):
    service, store, bus = setup
    await bus.deliver(topic, payload or fired())
    assert store.pending_events() == []


async def test_removed_rules_are_ignored(setup):
    service, store, bus = setup
    store.set_deployment_state("r5", "entfernt", NOW)
    await bus.deliver("homeai/rule/r5/fired", fired())
    assert store.pending_events() == []


async def test_switch_commands_are_recorded(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/active/set", "ON")
    assert store.get_deployment("r5").state == "aktiv"
    await bus.deliver("homeai/rule/r5/active/set", "OFF")
    assert store.get_deployment("r5").state == "schatten"
    await bus.deliver("homeai/rule/r5/active/set", "kaputt")
    assert store.get_deployment("r5").state == "schatten"
    await bus.deliver("homeai/rule/zz/active/set", "ON")


def add_change(db, minutes, temperature, preset, origin):
    ts = T0 + timedelta(minutes=minutes)
    db.insert_changes([Change(ts, "climate.bt", "heat", {"temperature": temperature, "preset_mode": preset})], "live")
    sid = db.conn.execute("SELECT id FROM state_changes WHERE ts_utc = ?", (to_utc_iso(ts),)).fetchone()["id"]
    db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via) VALUES (?, ?, 'kontext')", (sid, origin))
    db.conn.commit()


async def test_pending_events_are_evaluated_after_their_window(setup, db):
    service, store, bus = setup
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode")), "bad", T0)
    add_change(db, -300, 17, "eco", "benutzer")
    add_change(db, 10, 21, "comfort", "benutzer")
    await bus.deliver("homeai/rule/r5/fired", fired())
    await bus.deliver("homeai/rule/r5/fired", fired(ts=(NOW - timedelta(minutes=30)).isoformat()))
    assert await service.evaluate_pending() == 1                       # der zweite Eintrag wartet noch
    assert len(store.pending_events()) == 1
    (first, _) = store.stats_rows("r5")
    assert (first["outcome"], first["lage"], first["abstand_min"]) == ("treffer", "nachher", 10.0)
    stats = json.loads(bus.retained["homeai/rule/r5/stats"])
    assert stats["treffer"] == 1 and stats["scored"] == 1


async def test_unscored_reasons_get_a_marker_outcome(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired(reason="wohnungszustand"))
    assert await service.evaluate_pending() == 1
    assert store.pending_events() == []
    assert store.stats_rows("r5")[0]["outcome"] == "nicht_gewertet"


async def test_publish_all_stats_covers_every_active_rule(setup):
    service, store, bus = setup
    await service.publish_all_stats()
    assert "homeai/rule/r5/stats" in bus.retained


async def test_run_loop_evaluates_and_stops(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired(reason="wohnungszustand"))
    stop = asyncio.Event()
    task = asyncio.create_task(service.run_loop(stop, interval=0.05))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if store.pending_events() == []:
            break
    stop.set()
    await asyncio.wait_for(task, 2)
    assert store.pending_events() == []
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_service.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.service'`.

- [ ] **Step 3: Implementieren**

`ai/execution/service.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Callable

from collector.db import Database
from collector.models import parse_utc, to_utc_iso, utcnow

from .config import ExecutionConfig
from .models import RuleEvent
from .mqtt import Message
from .outcomes import evaluate, rule_stats
from .store import ExecStore

log = logging.getLogger("execution")


class Service:
    """Dauerbetrieb: nimmt Meldungen der Regeln entgegen, wertet sie aus und veröffentlicht Kennzahlen."""

    def __init__(self, cfg: ExecutionConfig, db: Database, store: ExecStore, bus, now: Callable = utcnow):
        self.cfg, self.db, self.store, self.bus, self.now = cfg, db, store, bus, now

    def register(self) -> None:
        self.bus.on("homeai/rule/+/fired", self.on_fired)
        self.bus.on("homeai/rule/+/active/set", self.on_active_set)

    async def on_fired(self, message: Message) -> None:
        rule_id = message.topic.split("/")[2]
        deployment = self.store.get_deployment(rule_id)
        if deployment is None or deployment.state == "entfernt":
            return
        try:
            data = json.loads(message.payload)
            stamp = to_utc_iso(parse_utc(data["ts"]))
        except (ValueError, KeyError, TypeError):
            log.warning("Unbrauchbare Meldung auf %s: %s", message.topic, message.payload[:200])
            return
        event = RuleEvent(rule_id, stamp, str(data.get("mode") or "schatten"),
                          str(data.get("result") or "nicht_ausgefuehrt"), str(data.get("reason") or ""),
                          data.get("target") or {}, data.get("current") or {}, message.payload)
        if self.store.add_event(event):
            await self.publish_stats(rule_id)

    async def on_active_set(self, message: Message) -> None:
        rule_id = message.topic.split("/")[2]
        deployment = self.store.get_deployment(rule_id)
        if deployment is None or deployment.state == "entfernt":
            return
        state = {"ON": "aktiv", "OFF": "schatten"}.get(message.payload.strip().upper())
        if state is not None:
            self.store.set_deployment_state(rule_id, state, self.now())

    async def evaluate_pending(self) -> int:
        now = self.now()
        done: set[str] = set()
        count = 0
        for event in self.store.pending_events():
            deployment = self.store.get_deployment(event.rule_id)
            if deployment is None:
                continue
            ts = parse_utc(event.ts_utc)
            if now < ts + timedelta(minutes=self.cfg.eval_after_min):
                continue
            verdict = evaluate(self.db, event, deployment, self.cfg)
            if verdict is None:
                start, end = ts - timedelta(minutes=self.cfg.eval_before_min), ts + timedelta(minutes=self.cfg.eval_after_min)
                self.store.save_outcome(event.id, "nicht_gewertet", start, end)
            else:
                self.store.save_outcome(event.id, verdict.outcome, verdict.window_start, verdict.window_end,
                                        lage=verdict.lage, abstand_min=verdict.abstand_min, deviation=verdict.deviation)
            done.add(event.rule_id)
            count += 1
        for rule_id in done:
            await self.publish_stats(rule_id)
        return count

    async def publish_stats(self, rule_id: str) -> None:
        payload = rule_stats(self.store, rule_id, self.cfg).to_payload()
        await self.bus.publish(f"homeai/rule/{rule_id}/stats", json.dumps(payload, ensure_ascii=False), retain=True)

    async def publish_all_stats(self) -> None:
        for deployment in self.store.list_deployments():
            await self.publish_stats(deployment.rule_id)

    async def run_loop(self, stop: asyncio.Event, interval: float = 60.0) -> None:
        while not stop.is_set():
            try:
                await self.evaluate_pending()
            except Exception:
                log.exception("Auswertung fehlgeschlagen")
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/service.py ai/tests/test_service.py
git commit -m "feat(ai): Dauerbetrieb wertet Regel-Meldungen aus und veroeffentlicht Kennzahlen" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 16: Auftragsläufer für das Dashboard

**Files:**
- Create: `ai/execution/jobs.py`
- Create: `ai/tests/job_fakes.py`
- Test: `ai/tests/test_jobs.py`

**Interfaces:**
- Consumes: `JOB_LABELS`, `MODES`, `NO_SELECTION` (Aufgabe 13), `Message` (Aufgabe 11), `ExecStore` (`start_job`, `finish_job`, `count_jobs_since`, `recent_jobs`, `list_deployments`; Aufgabe 8), `ExecutionConfig` (`arm_seconds`, `llm_runs_per_day`, `job_output_limit`, `timezone`).
- Produces:
  - `execution.jobs.JobActions` (Protocol): `async status() -> str`, `deploy(candidate_id: int) -> str`, `undeploy(rule_id: str) -> str`, `check() -> str`, `refresh_dashboard() -> None`.
  - `execution.jobs.SubprocessExecutor(python=sys.executable, module="analysis", timeout_s=1800)`; Aufruf `await executor(argv: list[str]) -> tuple[int, str]` (Rückgabecode, Ausgabe aus stdout und stderr).
  - `execution.jobs.JobRunner(cfg, store, bus, executor, actions, *, candidates: Callable[[], list[str]], rules: Callable[[], list[str]], refresh: Callable[[], Awaitable[None]], now=utcnow)` mit `register()`, `async start()`, `async on_command(message)`, `async on_select(message)`, `async tick()`, `async arm()`, `async disarm()`, `async wait()`; Attribute `selection: dict[str, str]`, `running: str | None`, `armed_until: datetime | None`.
  - `execution.jobs.candidate_options(db, astore, acfg, store) -> list[str]` (Format `"<id>: <Raum> <Auslöser> -> <Aktion>"`, nur bestandene und nicht angelegte Kandidaten), `rule_options(store) -> list[str]` (`"<rule_id>: <Beschreibung>"`), `status_text(db, store) -> str`.
  - Themen: `homeai/job/state` (JSON `{state: idle|laeuft, job, ts, message}`), `homeai/job/result` (JSON `{title, markdown, ts}`), `homeai/state/llm_arm` (`ON`/`OFF`), `homeai/sel/<what>/state`.
  - `tests.job_fakes`: `Clock`, `FakeExecutor` (`calls`, `result`, `gate`), `FakeActions` (`deployed`, `undeployed`, `refreshed`).

- [ ] **Step 1: Failing tests schreiben**

`ai/tests/job_fakes.py`:

```python
from __future__ import annotations

import asyncio

from tests.ha_template import utc


class Clock:
    def __init__(self):
        self.value = utc(2026, 10, 15, 12, 0)

    def __call__(self):
        return self.value


class FakeExecutor:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.result: tuple[int, str] = (0, "ausgabe")
        self.gate: asyncio.Event | None = None

    async def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        if self.gate is not None:
            await self.gate.wait()
        return self.result


class FakeActions:
    def __init__(self):
        self.deployed: list[int] = []
        self.undeployed: list[str] = []
        self.refreshed = 0

    async def status(self) -> str:
        return "Status: 2 Regeln"

    async def deploy(self, candidate_id: int) -> str:
        self.deployed.append(candidate_id)
        return f"Regel r{candidate_id} angelegt"

    async def undeploy(self, rule_id: str) -> str:
        self.undeployed.append(rule_id)
        return f"Regel {rule_id} entfernt"

    async def check(self) -> str:
        return "OK    alles"

    async def refresh_dashboard(self) -> None:
        self.refreshed += 1
```

`ai/tests/test_jobs.py`:

```python
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from analysis.store import Store as AnalysisStore
from collector.models import Change
from execution.jobs import (FENCE, JobRunner, SubprocessExecutor, candidate_options, rule_options, status_text)
from execution.models import Deployment
from execution.mqtt import InMemoryBus
from execution.store import ExecStore
from tests.execution_fixture import AREA, CLIMATE, seed_database
from tests.ha_template import utc
from tests.job_fakes import Clock, FakeActions, FakeExecutor


@pytest.fixture
def w(db, ecfg):
    store, bus, executor, actions, clock = ExecStore(db), InMemoryBus(), FakeExecutor(), FakeActions(), Clock()
    refreshed = []

    async def refresh():
        refreshed.append(1)

    runner = JobRunner(ecfg, store, bus, executor, actions, candidates=lambda: ["7: Bad", "9: Wohnzimmer"],
                       rules=lambda: ["r7: Bad"], refresh=refresh, now=clock)
    runner.register()
    return SimpleNamespace(runner=runner, store=store, bus=bus, executor=executor, actions=actions, clock=clock,
                           refreshed=refreshed)


async def press(w, name):
    await w.bus.deliver(f"homeai/cmd/{name}", "PRESS")
    await w.runner.wait()


def states(w):
    return [json.loads(m.payload)["state"] for m in w.bus.published if m.topic == "homeai/job/state"]


def result(w):
    return json.loads(w.bus.retained["homeai/job/result"])


async def test_start_publishes_idle_and_the_selection(w):
    await w.runner.start()
    assert json.loads(w.bus.retained["homeai/job/state"])["state"] == "idle"
    assert w.bus.retained["homeai/state/llm_arm"] == "OFF"
    assert w.bus.retained["homeai/sel/mode/state"] == "scan"


async def test_scan_runs_the_cli_and_reports_the_output(w):
    await press(w, "scan")
    assert w.executor.calls == [["scan"]]
    assert states(w) == ["laeuft", "idle"]
    assert result(w)["title"] == "Scan (ohne Modell): ok"
    assert result(w)["markdown"].startswith(FENCE) and "ausgabe" in result(w)["markdown"]
    assert w.store.recent_jobs()[0]["state"] == "ok"


async def test_failed_commands_are_reported_as_errors(w):
    w.executor.result = (2, "Fehler: kaputt")
    await press(w, "signals")
    assert result(w)["title"] == "Signale anzeigen: fehler" and "kaputt" in result(w)["markdown"]
    assert w.store.recent_jobs()[0]["state"] == "fehler"


async def test_unknown_commands_and_payloads_are_ignored(w):
    await w.bus.deliver("homeai/cmd/nonsense", "PRESS")
    await w.bus.deliver("homeai/cmd/scan", "foo")
    await w.runner.wait()
    assert w.executor.calls == [] and "homeai/job/state" not in w.bus.retained


async def test_a_second_job_is_rejected_while_one_runs(w):
    w.executor.gate = asyncio.Event()
    await w.bus.deliver("homeai/cmd/scan", "PRESS")
    await w.bus.deliver("homeai/cmd/signals", "PRESS")
    assert "läuft bereits" in result(w)["markdown"] and result(w)["title"].endswith("abgelehnt")
    for _ in range(3):
        await asyncio.sleep(0)
    assert w.executor.calls == [["scan"]]
    w.executor.gate.set()
    await w.runner.wait()
    assert w.executor.calls == [["scan"]]


async def test_the_model_run_needs_the_cost_release(w):
    await press(w, "llm_run")
    assert w.executor.calls == [] and "Kosten" in result(w)["markdown"]
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    assert w.bus.retained["homeai/state/llm_arm"] == "ON"
    await press(w, "llm_run")
    assert w.executor.calls == [["run", "--mode", "scan"]]
    assert w.bus.retained["homeai/state/llm_arm"] == "OFF"          # die Freigabe gilt nur für einen Lauf
    await press(w, "llm_run")
    assert len(w.executor.calls) == 1 and "Kosten" in result(w)["markdown"]


async def test_the_release_expires_by_itself(w):
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    w.clock.value += timedelta(seconds=30)
    await w.runner.tick()
    assert w.bus.retained["homeai/state/llm_arm"] == "ON"
    w.clock.value += timedelta(seconds=31)
    await w.runner.tick()
    assert w.bus.retained["homeai/state/llm_arm"] == "OFF"
    await press(w, "llm_run")
    assert w.executor.calls == []


async def test_the_release_can_be_withdrawn(w):
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    await w.bus.deliver("homeai/cmd/llm_arm", "OFF")
    await press(w, "llm_run")
    assert w.executor.calls == []


async def test_daily_limit_for_model_runs(w):
    for _ in range(3):
        w.store.start_job("llm_run", {}, w.clock.value)
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    await press(w, "llm_run")
    assert w.executor.calls == [] and "Tageslimit" in result(w)["markdown"]


async def test_yesterdays_runs_do_not_count(w):
    for _ in range(3):
        w.store.start_job("llm_run", {}, w.clock.value - timedelta(days=1))
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    await press(w, "llm_run")
    assert w.executor.calls == [["run", "--mode", "scan"]]


async def test_mode_selection_is_validated_and_used(w):
    await w.bus.deliver("homeai/sel/mode/set", "weekly")
    assert w.bus.retained["homeai/sel/mode/state"] == "weekly"
    await w.bus.deliver("homeai/sel/mode/set", "nonsense")
    assert w.runner.selection["mode"] == "weekly"
    await press(w, "llm_dry")
    assert w.executor.calls == [["run", "--mode", "weekly", "--dry-run"]]


async def test_deploy_uses_the_selected_candidate_and_refreshes_the_controls(w):
    await w.bus.deliver("homeai/sel/candidate/set", "7: Bad")
    await press(w, "deploy")
    assert w.actions.deployed == [7] and "r7 angelegt" in result(w)["markdown"]
    assert w.refreshed == [1]


async def test_deploy_without_a_selection_is_an_error(w):
    await press(w, "deploy")
    assert w.actions.deployed == [] and result(w)["title"].endswith("fehler") and "Kein Kandidat" in result(w)["markdown"]


async def test_selection_of_unknown_options_is_ignored(w):
    await w.bus.deliver("homeai/sel/candidate/set", "99: Küche")
    await w.bus.deliver("homeai/sel/other/set", "x")
    assert w.runner.selection["candidate"] == "–"


async def test_undeploy_uses_the_selected_rule(w):
    await w.bus.deliver("homeai/sel/rule/set", "r7: Bad")
    await press(w, "undeploy")
    assert w.actions.undeployed == ["r7"] and w.refreshed == [1]


async def test_status_and_check_use_the_actions(w):
    await press(w, "status")
    assert "Status: 2 Regeln" in result(w)["markdown"]
    await press(w, "check")
    assert "OK    alles" in result(w)["markdown"]


async def test_long_output_is_shortened(w):
    w.executor.result = (0, "x" * 20000)
    await press(w, "scan")
    markdown = result(w)["markdown"]
    assert len(markdown) < 12100 and "gekürzt" in markdown


async def test_subprocess_executor_runs_a_module():
    code, output = await SubprocessExecutor(module="platform")([])
    assert code == 0 and output.strip()


def test_candidate_and_rule_options(db, acfg):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    options = candidate_options(db, astore, acfg, store)
    assert len(options) == 1 and options[0].startswith(f"{candidate_id}: Bad ")
    store.add_deployment(Deployment("r1", candidate_id, AREA, "automation.x", "homeai_rule_r1", CLIMATE, "schatten",
                                    "1.0", "Bad: Test", "2026-10-15T00:00:00.000000+00:00"))
    assert candidate_options(db, astore, acfg, store) == []
    assert rule_options(store) == ["r1: Bad: Test"]


def test_status_text(db):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    db.insert_changes([Change(utc(2026, 10, 15, 10, 0), "person.fabian", "home")], "live")
    store.add_deployment(Deployment("r1", candidate_id, AREA, "automation.x", "homeai_rule_r1", CLIMATE, "schatten",
                                    "1.0", "Bad: Test", "2026-10-15T00:00:00.000000+00:00"))
    store.finish_job(store.start_job("scan", {}, utc(2026, 10, 15, 11, 0)), "ok", "x", utc(2026, 10, 15, 11, 1))
    text = status_text(db, store)
    assert "Regeln: 1 angelegt, 0 aktiv, 1 im Schatten" in text
    assert "Aufgezeichnete Entitäten: 2" in text
    assert "Letztes aufgezeichnetes Ereignis: 2026-10-15T10:00:00.000000+00:00" in text
    assert "scan: ok" in text
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_jobs.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.jobs'`.

- [ ] **Step 3: Implementieren**

`ai/execution/jobs.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo

from analysis.candidates import describe
from analysis.config import AnalysisConfig
from analysis.pseudonym import build_pseudonyms
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import to_utc_iso, utcnow

from .config import ExecutionConfig
from .controls import JOB_LABELS, MODES, NO_SELECTION
from .mqtt import Message
from .store import ExecStore

log = logging.getLogger("execution")

FENCE = "`" * 3
REFRESH_AFTER = ("llm_run", "deploy", "undeploy")
CLI_ARGS: dict[str, Callable[[dict[str, str]], list[str]]] = {
    "signals": lambda sel: ["signals"],
    "scan": lambda sel: ["scan"],
    "llm_dry": lambda sel: ["run", "--mode", sel["mode"], "--dry-run"],
    "llm_run": lambda sel: ["run", "--mode", sel["mode"]],
    "candidates": lambda sel: ["list", "--status", "bestanden"],
    "compare": lambda sel: ["compare"],
}


class JobActions(Protocol):
    async def status(self) -> str: ...
    async def deploy(self, candidate_id: int) -> str: ...
    async def undeploy(self, rule_id: str) -> str: ...
    async def check(self) -> str: ...
    async def refresh_dashboard(self) -> None: ...


class SubprocessExecutor:
    """Führt `python -m analysis …` als eigenen Prozess aus (eigene Umgebung, kein gemeinsamer Zustand)."""

    def __init__(self, python: str = sys.executable, module: str = "analysis", timeout_s: float = 1800.0):
        self.python, self.module, self.timeout_s = python, module, timeout_s

    async def __call__(self, argv: list[str]) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            self.python, "-m", self.module, *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.timeout_s)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return 124, "Zeitüberschreitung: der Auftrag wurde abgebrochen."
        return process.returncode or 0, output.decode("utf-8", "replace")


class JobRunner:
    def __init__(self, cfg: ExecutionConfig, store: ExecStore, bus, executor, actions: JobActions, *,
                 candidates: Callable[[], list[str]], rules: Callable[[], list[str]],
                 refresh: Callable[[], Awaitable[None]], now: Callable[[], datetime] = utcnow):
        self.cfg, self.store, self.bus, self.executor, self.actions = cfg, store, bus, executor, actions
        self.candidates, self.rules, self.refresh, self.now = candidates, rules, refresh, now
        self.selection = {"mode": "scan", "candidate": NO_SELECTION, "rule": NO_SELECTION}
        self.running: str | None = None
        self.armed_until: datetime | None = None
        self._tasks: set[asyncio.Task] = set()

    def register(self) -> None:
        self.bus.on("homeai/cmd/+", self.on_command)
        self.bus.on("homeai/sel/+/set", self.on_select)

    async def start(self) -> None:
        await self._publish_state("idle", "")
        await self._publish_arm(False)
        for what, value in self.selection.items():
            await self.bus.publish(f"homeai/sel/{what}/state", value, retain=True)

    async def wait(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0)

    # --- Eingänge ----------------------------------------------------------------------------

    async def on_command(self, message: Message) -> None:
        name, payload = message.topic.split("/")[2], message.payload.strip()
        if name == "llm_arm":
            await (self.arm() if payload.upper() == "ON" else self.disarm())
            return
        if name not in JOB_LABELS or payload != "PRESS":
            return
        problem = self._rejection(name)
        if problem:
            await self._publish_result(name, "abgelehnt", problem)
            return
        self.running = name
        task = asyncio.create_task(self._run(name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_select(self, message: Message) -> None:
        what = message.topic.split("/")[2]
        allowed = {"mode": list(MODES), "candidate": [*self.candidates(), NO_SELECTION],
                   "rule": [*self.rules(), NO_SELECTION]}.get(what)
        if allowed is None or message.payload not in allowed:
            log.warning("Ungültige Auswahl %r für %s", message.payload, what)
            return
        self.selection[what] = message.payload
        await self.bus.publish(f"homeai/sel/{what}/state", message.payload, retain=True)

    async def tick(self) -> None:
        if self.armed_until is not None and self.now() >= self.armed_until:
            await self.disarm()

    async def arm(self) -> None:
        self.armed_until = self.now() + timedelta(seconds=self.cfg.arm_seconds)
        await self._publish_arm(True)

    async def disarm(self) -> None:
        self.armed_until = None
        await self._publish_arm(False)

    # --- Ausführung -----------------------------------------------------------------------------

    def _runs_today(self) -> int:
        local = self.now().astimezone(ZoneInfo(self.cfg.timezone))
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        return self.store.count_jobs_since("llm_run", start)

    def _rejection(self, name: str) -> str | None:
        if self.running:
            return f"Es läuft bereits ein Auftrag ({self.running}); bitte warten."
        if name == "llm_run":
            if self.armed_until is None or self.now() >= self.armed_until:
                return (f"Kosten nicht freigegeben: zuerst „Kosten freigeben“ einschalten "
                        f"(gilt {self.cfg.arm_seconds} Sekunden).")
            if self._runs_today() >= self.cfg.llm_runs_per_day:
                return f"Tageslimit erreicht: höchstens {self.cfg.llm_runs_per_day} Läufe mit dem Modell je Tag."
        return None

    def _selected(self, what: str, label: str) -> str:
        value = self.selection[what]
        if value == NO_SELECTION:
            raise ValueError(f"Kein {label} gewählt")
        return value.split(":", 1)[0].strip()

    async def _run(self, name: str) -> None:
        try:
            job_id = self.store.start_job(name, dict(self.selection), self.now())
            await self._publish_state("laeuft", name)
            try:
                state, text = await self._execute(name)
            except Exception as exc:                     # Fehler eines Auftrags dürfen den Dienst nicht beenden
                log.exception("Auftrag %s fehlgeschlagen", name)
                state, text = "fehler", f"{type(exc).__name__}: {exc}"
            text = self._shorten(text)
            self.store.finish_job(job_id, state, text[:2000], self.now())
            if name == "llm_run":
                await self.disarm()
            await self._publish_result(name, state, text)
            await self._publish_state("idle", name, state)
            if name in REFRESH_AFTER:
                await self.refresh()
        finally:
            self.running = None

    async def _execute(self, name: str) -> tuple[str, str]:
        if name == "status":
            return "ok", await self.actions.status()
        if name == "check":
            return "ok", await self.actions.check()
        if name == "deploy":
            return "ok", await self.actions.deploy(int(self._selected("candidate", "Kandidat")))
        if name == "undeploy":
            return "ok", await self.actions.undeploy(self._selected("rule", "Regel"))
        code, output = await self.executor(CLI_ARGS[name](self.selection))
        return ("ok" if code == 0 else "fehler"), output

    def _shorten(self, text: str) -> str:
        limit = self.cfg.job_output_limit
        return text if len(text) <= limit else text[:limit] + "\n… (gekürzt)"

    # --- Ausgänge ---------------------------------------------------------------------------------

    async def _publish_state(self, state: str, job: str, message: str = "") -> None:
        payload = {"state": state, "job": job, "ts": to_utc_iso(self.now()), "message": message}
        await self.bus.publish("homeai/job/state", json.dumps(payload, ensure_ascii=False), retain=True)

    async def _publish_result(self, name: str, state: str, text: str) -> None:
        payload = {"title": f"{JOB_LABELS[name]}: {state}", "markdown": f"{FENCE}\n{text}\n{FENCE}",
                   "ts": to_utc_iso(self.now())}
        await self.bus.publish("homeai/job/result", json.dumps(payload, ensure_ascii=False), retain=True)

    async def _publish_arm(self, on: bool) -> None:
        await self.bus.publish("homeai/state/llm_arm", "ON" if on else "OFF", retain=True)


def candidate_options(db: Database, astore: AnalysisStore, acfg: AnalysisConfig, store: ExecStore) -> list[str]:
    deployed = {item.candidate_id for item in store.list_deployments()}
    pseudo, signals = build_pseudonyms(db), astore.signal_descriptions()
    options = []
    for candidate in astore.list_candidates(status="bestanden"):
        if candidate.id in deployed:
            continue
        room, when, action = describe(candidate, acfg, pseudo, signals)
        options.append(f"{candidate.id}: {room} {when} -> {action}"[:200])
    return options


def rule_options(store: ExecStore) -> list[str]:
    return [f"{item.rule_id}: {item.description}"[:200] for item in store.list_deployments()]


def status_text(db: Database, store: ExecStore) -> str:
    deployments = store.list_deployments()
    active = sum(1 for item in deployments if item.state == "aktiv")
    entities = db.conn.execute("SELECT COUNT(*) FROM entities WHERE active = 1").fetchone()[0]
    last = db.conn.execute("SELECT MAX(ts_utc) FROM state_changes WHERE source = 'live'").fetchone()[0]
    lines = [f"Regeln: {len(deployments)} angelegt, {active} aktiv, {len(deployments) - active} im Schatten",
             f"Aufgezeichnete Entitäten: {entities}",
             f"Letztes aufgezeichnetes Ereignis: {last or 'noch keines'}"]
    jobs = store.recent_jobs(5)
    if jobs:
        lines.append("Letzte Aufträge:")
        lines += [f"  {job['started_at'][:19]}Z {job['name']}: {job['state']}" for job in jobs]
    return "\n".join(lines)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/execution/jobs.py ai/tests/job_fakes.py ai/tests/test_jobs.py
git commit -m "feat(ai): Auftragslaeufer fuer das Dashboard (feste Liste, Kosten-Freigabe, Tageslimit)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Aktionen gegen die echte HA, Dauerbetrieb und Kommandozeile

**Files:**
- Create: `ai/execution/actions.py`, `ai/execution/serve.py`
- Modify (ersetzen): `ai/execution/cli.py`
- Test: `ai/tests/test_actions.py`, `ai/tests/test_serve.py`, `ai/tests/test_execution_cli.py`

**Interfaces:**
- Consumes: `Deployer`, `DeployResult` (Aufgabe 14), `Service` (Aufgabe 15), `JobRunner`, `SubprocessExecutor`, `candidate_options`, `rule_options`, `status_text` (Aufgabe 16), `publish_controls` (Aufgabe 13), `ha_connection` (Aufgabe 10), `AioMqttBus`, `publish_rule_entities`, `rule_label` (Aufgabe 11), `rule_stats` (Aufgabe 12).
- Produces:
  - `execution.actions.LiveActions(cfg, db, store, astore, acfg, bus, now=utcnow)` (erfüllt `JobActions`; öffnet je Aktion eine eigene Verbindung zu HA).
  - `execution.serve.serve(cfg, acfg, db, bus, actions, executor, stop: asyncio.Event, *, interval: float = 60.0, now=utcnow) -> None`; beim (Wieder-)Verbinden werden Bedienelemente, Zustände, Kennzahlen, die Discovery der angelegten Regeln und das Dashboard veröffentlicht.
  - `execution.cli.main(argv=None) -> int` mit den Kommandos `init [--room KEY]`, `deploy <candidate_id>`, `undeploy <rule_id>`, `rules`, `check`, `serve`; globale Optionen `--config`, `--analysis-config`, `--db` (Umgebungsvariablen `EXECUTION_CONFIG`, `ANALYSIS_CONFIG`, `DB_PATH`). Fehler enden mit Code 2 und `Fehler: …` auf stderr; `check` endet mit 1, wenn eine Zeile mit `FEHLT` beginnt.

- [ ] **Step 1: Failing tests schreiben**

`ai/tests/test_actions.py`:

```python
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from analysis.store import Store as AnalysisStore
from execution.actions import LiveActions
from execution.mqtt import InMemoryBus
from execution.store import ExecStore
from tests.execution_fixture import seed_database, seed_home_assistant
from tests.fake_ha_rest import FakeHaRest
from tests.fake_hass import FakeHass
from tests.ha_template import utc


@pytest_asyncio.fixture
async def ha():
    hass, rest = FakeHass(), FakeHaRest()
    app = web.Application()
    app.router.add_get("/api/websocket", hass.handler)
    rest.add_routes(app)
    server = TestServer(app)
    await server.start_server()
    hass.url = f"ws://127.0.0.1:{server.port}/api/websocket"
    rest.base = f"http://127.0.0.1:{server.port}"
    try:
        yield SimpleNamespace(hass=hass, rest=rest)
    finally:
        await server.close()


async def test_live_actions_talk_to_home_assistant(db, acfg, ecfg, ha):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    helpers, dashboards = seed_home_assistant(ha.rest, ha.hass)
    actions = LiveActions(replace(ecfg, ha_url=ha.hass.url), db, store, astore, acfg, InMemoryBus(),
                          now=lambda: utc(2026, 10, 15, 12, 0))
    text = await actions.deploy(candidate_id)
    assert "r1" in text and "Schattenmodus" in text and "homeai_rule_r1" in ha.rest.automations
    assert "Regeln: 1 angelegt" in await actions.status()
    assert "Blueprint homeai/ki_regel.yaml" in await actions.check()
    await actions.refresh_dashboard()
    assert dashboards.saved
    assert "entfernt" in await actions.undeploy("r1")
    assert "homeai_rule_r1" not in ha.rest.automations


async def test_errors_of_the_deployer_reach_the_caller(db, acfg, ecfg, ha):
    astore, store = AnalysisStore(db), ExecStore(db)
    seed_home_assistant(ha.rest, ha.hass)
    actions = LiveActions(replace(ecfg, ha_url=ha.hass.url), db, store, astore, acfg, InMemoryBus())
    with pytest.raises(Exception, match="existiert nicht"):
        await actions.deploy(42)
```

`ai/tests/test_serve.py`:

```python
import asyncio
import json

from analysis.store import Store as AnalysisStore
from execution.models import Deployment
from execution.mqtt import InMemoryBus, rule_discovery_topics
from execution.serve import serve
from execution.store import ExecStore
from tests.execution_fixture import AREA, CLIMATE, seed_database
from tests.job_fakes import FakeActions, FakeExecutor


async def until(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Bedingung wurde nicht erreicht")


async def test_serve_announces_handles_commands_and_stops(db, acfg, ecfg):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    store.add_deployment(Deployment("r9", 9, AREA, "automation.x", "homeai_rule_r9", CLIMATE, "schatten", "1.0",
                                    "Bad: Test", "2026-10-15T00:00:00.000000+00:00"))
    bus, actions, executor, stop = InMemoryBus(), FakeActions(), FakeExecutor(), asyncio.Event()
    task = asyncio.create_task(serve(ecfg, acfg, db, bus, actions, executor, stop, interval=0.05))
    await until(lambda: "homeai/job/state" in bus.retained and "homeai/rule/r9/stats" in bus.retained)
    options = json.loads(bus.retained["homeassistant/select/homeai/candidate/config"])["options"]
    assert options and options[0].startswith(f"{candidate_id}: Bad ")
    assert set(rule_discovery_topics("r9")) <= set(bus.retained)
    assert json.loads(bus.retained["homeassistant/select/homeai/rule/config"])["options"] == ["r9: Bad: Test"]
    assert actions.refreshed == 1
    await bus.deliver("homeai/cmd/signals", "PRESS")
    await until(lambda: "homeai/job/result" in bus.retained)
    assert executor.calls == [["signals"]]
    stop.set()
    await asyncio.wait_for(task, 3)


async def test_serve_survives_a_failing_dashboard_refresh(db, acfg, ecfg):
    class Broken(FakeActions):
        async def refresh_dashboard(self):
            raise RuntimeError("HA nicht erreichbar")

    bus, stop = InMemoryBus(), asyncio.Event()
    task = asyncio.create_task(serve(ecfg, acfg, db, bus, Broken(), FakeExecutor(), stop, interval=0.05))
    await until(lambda: "homeai/job/state" in bus.retained)
    stop.set()
    await asyncio.wait_for(task, 3)
```

`ai/tests/test_execution_cli.py`:

```python
from pathlib import Path

import pytest

from collector.db import Database
from execution.cli import main
from execution.models import Deployment, RuleEvent
from execution.store import ExecStore

ROOT = Path(__file__).resolve().parent.parent
CONFIG = str(ROOT / "config" / "execution.yaml")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_URL", "ws://ha.test:8123/api/websocket")
    monkeypatch.delenv("HA_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("MQTT_HOST", raising=False)
    return str(tmp_path / "x.sqlite")


def run(path, *args):
    return main(["--config", CONFIG, "--analysis-config", str(ROOT / "config" / "analysis.yaml"), "--db", path, *args])


def test_rules_without_any_rule(env, capsys):
    assert run(env, "rules") == 0
    assert "Keine Regeln angelegt." in capsys.readouterr().out


def test_rules_lists_state_and_numbers(env, capsys):
    db = Database(env)
    store = ExecStore(db)
    store.add_deployment(Deployment("r5", 5, "bad", "automation.x", "homeai_rule_r5", "climate.bt", "aktiv", "1.0",
                                    "Bad: Mo-Fr 06:30 -> Preset comfort", "2026-10-15T00:00:00.000000+00:00"))
    store.add_event(RuleEvent("r5", "2026-10-15T04:30:00.000000+00:00", "aktiv", "ausgefuehrt", "", {}, {}, "{}"))
    db.close()
    assert run(env, "rules") == 0
    out = capsys.readouterr().out
    assert "r5" in out and "aktiv" in out and "Mo-Fr 06:30" in out and "Auslösungen 1" in out


def test_init_needs_the_write_token(env, capsys):
    assert run(env, "init") == 2
    assert "HA_WRITE_TOKEN" in capsys.readouterr().err


def test_deploy_needs_the_broker(env, monkeypatch, capsys):
    monkeypatch.setenv("HA_WRITE_TOKEN", "secret")
    assert run(env, "deploy", "1") == 2
    assert "MQTT_HOST" in capsys.readouterr().err


def test_serve_needs_the_broker(env, monkeypatch, capsys):
    monkeypatch.setenv("HA_WRITE_TOKEN", "secret")
    assert run(env, "serve") == 2
    assert "MQTT_HOST" in capsys.readouterr().err


def test_unknown_command_is_rejected_by_the_parser(env):
    with pytest.raises(SystemExit) as exit_info:
        run(env, "nonsense")
    assert exit_info.value.code == 2
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_actions.py tests/test_serve.py tests/test_execution_cli.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'execution.actions'` (und `execution.serve`).

- [ ] **Step 3: Implementieren**

`ai/execution/actions.py`:

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable

from analysis.config import AnalysisConfig
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import utcnow

from .config import ExecutionConfig
from .deploy import Deployer
from .ha_api import ha_connection
from .jobs import status_text
from .store import ExecStore


class LiveActions:
    """Aktionen des Dashboards gegen die echte Home Assistant; je Aktion eine eigene Verbindung."""

    def __init__(self, cfg: ExecutionConfig, db: Database, store: ExecStore, astore: AnalysisStore,
                 acfg: AnalysisConfig, bus, now: Callable[[], datetime] = utcnow):
        self.cfg, self.db, self.store, self.astore, self.acfg, self.bus, self.now = cfg, db, store, astore, acfg, bus, now

    @asynccontextmanager
    async def _deployer(self):
        async with ha_connection(self.cfg.ha_url, self.cfg.require_token()) as (rest, ws):
            yield Deployer(self.cfg, self.db, self.store, self.astore, self.acfg, rest, ws, self.bus, self.now)

    async def status(self) -> str:
        return status_text(self.db, self.store)

    async def deploy(self, candidate_id: int) -> str:
        async with self._deployer() as deployer:
            result = await deployer.deploy(candidate_id)
        lines = [f"Regel {result.rule_id} im Schattenmodus angelegt ({result.automation_entity})."]
        return "\n".join(lines + [f"Hinweis: {hint}" for hint in result.hints])

    async def undeploy(self, rule_id: str) -> str:
        async with self._deployer() as deployer:
            await deployer.undeploy(rule_id)
        return f"Regel {rule_id} entfernt."

    async def check(self) -> str:
        async with self._deployer() as deployer:
            return "\n".join(await deployer.check())

    async def refresh_dashboard(self) -> None:
        async with self._deployer() as deployer:
            await deployer.refresh_dashboard()
```

`ai/execution/serve.py`:

```python
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable

from analysis.config import AnalysisConfig
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import utcnow

from .config import ExecutionConfig
from .controls import publish_controls
from .jobs import JobActions, JobRunner, candidate_options, rule_options
from .mqtt import publish_rule_entities, rule_label
from .service import Service
from .store import ExecStore

log = logging.getLogger("execution")


async def serve(cfg: ExecutionConfig, acfg: AnalysisConfig, db: Database, bus, actions: JobActions, executor,
                stop: asyncio.Event, *, interval: float = 60.0, now: Callable[[], datetime] = utcnow) -> None:
    store, astore = ExecStore(db), AnalysisStore(db)

    def candidates() -> list[str]:
        return candidate_options(db, astore, acfg, store)

    def rules() -> list[str]:
        return rule_options(store)

    async def refresh() -> None:
        await publish_controls(bus, candidates(), rules())

    service = Service(cfg, db, store, bus, now)
    runner = JobRunner(cfg, store, bus, executor, actions, candidates=candidates, rules=rules, refresh=refresh, now=now)
    service.register()
    runner.register()

    async def announce() -> None:
        await refresh()
        await runner.start()
        for deployment in store.list_deployments():
            await publish_rule_entities(bus, deployment.rule_id, rule_label(deployment.rule_id, deployment.description))
        await service.publish_all_stats()
        try:
            await actions.refresh_dashboard()
        except Exception:
            log.exception("Das Dashboard konnte nicht aktualisiert werden")

    bus.on_connect(announce)

    async def ticker() -> None:
        while not stop.is_set():
            await runner.tick()
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    await asyncio.gather(bus.run(stop), service.run_loop(stop, interval), ticker())
    await runner.wait()
```

`ai/execution/cli.py` (ersetzt den Rumpf):

```python
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from analysis.config import AnalysisConfigError, load_analysis_config
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.hass_ws import HassAuthError, HassConnectionError

from .actions import LiveActions
from .config import ExecutionConfigError, load_execution_config
from .deploy import DeployError, Deployer
from .ha_api import HaError, ha_connection
from .jobs import SubprocessExecutor
from .mqtt import AioMqttBus
from .outcomes import rule_stats
from .serve import serve
from .store import ExecStore
from .translate import TranslateError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="execution", description="Freigabe und Ausführung der KI-Regeln")
    parser.add_argument("--config", default=os.environ.get("EXECUTION_CONFIG", "/app/config/execution.yaml"))
    parser.add_argument("--analysis-config", default=os.environ.get("ANALYSIS_CONFIG", "/app/config/analysis.yaml"))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "/data/home_ai.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Sammelschalter, Helfer und Hilfsautomationen anlegen")
    init.add_argument("--room", help="nur dieser Raum (Schlüssel aus execution.yaml)")
    deploy = sub.add_parser("deploy", help="einen bestandenen Kandidaten im Schattenmodus anlegen")
    deploy.add_argument("candidate_id", type=int)
    undeploy = sub.add_parser("undeploy", help="eine Regel entfernen (die Historie bleibt)")
    undeploy.add_argument("rule_id")
    sub.add_parser("rules", help="angelegte Regeln mit Kennzahlen anzeigen")
    sub.add_parser("check", help="Blueprint, Helfer, Schalter und Regeln in Home Assistant prüfen")
    sub.add_parser("serve", help="Dauerbetrieb: Meldungen auswerten, Dashboard bedienen")
    return parser


def _cmd_rules(args: argparse.Namespace) -> int:
    cfg = load_execution_config(args.config)
    db = Database(args.db)
    try:
        store = ExecStore(db)
        deployments = store.list_deployments()
        if not deployments:
            print("Keine Regeln angelegt.")
            return 0
        for item in deployments:
            stats = rule_stats(store, item.rule_id, cfg)
            print(f"{item.rule_id}  {item.state:<9} {item.description}")
            print(f"      {stats.text}; Auslösungen {stats.events}; Signal: {stats.reife}")
        return 0
    finally:
        db.close()


def _require_broker(cfg) -> None:
    if cfg.mqtt is None:
        raise ExecutionConfigError("MQTT_HOST fehlt (Umgebungsvariable)")


async def _deployer_command(args: argparse.Namespace) -> int:
    cfg = load_execution_config(args.config)
    token = cfg.require_token()
    _require_broker(cfg)
    acfg = load_analysis_config(args.analysis_config)
    db = Database(args.db)
    try:
        store, astore = ExecStore(db), AnalysisStore(db)
        async with ha_connection(cfg.ha_url, token) as (rest, ws):
            deployer = Deployer(cfg, db, store, astore, acfg, rest, ws, AioMqttBus(cfg.mqtt))
            if args.command == "init":
                for line in await deployer.init(args.room):
                    print(line)
                return 0
            if args.command == "deploy":
                result = await deployer.deploy(args.candidate_id)
                print(f"Regel {result.rule_id} im Schattenmodus angelegt ({result.automation_entity}).")
                for hint in result.hints:
                    print(f"Hinweis: {hint}")
                return 0
            if args.command == "undeploy":
                await deployer.undeploy(args.rule_id)
                print(f"Regel {args.rule_id} entfernt.")
                return 0
            lines = await deployer.check()
            for line in lines:
                print(line)
            return 1 if any(line.startswith("FEHLT") for line in lines) else 0
    finally:
        db.close()


async def _serve(args: argparse.Namespace) -> int:
    cfg = load_execution_config(args.config)
    cfg.require_token()
    _require_broker(cfg)
    acfg = load_analysis_config(args.analysis_config)
    db = Database(args.db)
    bus = AioMqttBus(cfg.mqtt)
    actions = LiveActions(cfg, db, ExecStore(db), AnalysisStore(db), acfg, bus)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:          # Windows
            pass
    try:
        await serve(cfg, acfg, db, bus, actions, SubprocessExecutor(), stop)
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "rules":
            return _cmd_rules(args)
        if args.command == "serve":
            return asyncio.run(_serve(args))
        return asyncio.run(_deployer_command(args))
    except (ExecutionConfigError, DeployError, HaError, TranslateError, AnalysisConfigError, FileNotFoundError,
            HassAuthError, HassConnectionError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest tests/test_actions.py tests/test_serve.py tests/test_execution_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Gesamte Suite laufen lassen und committen**

Run: `cd ai && python -m pytest -q`
Expected: PASS (alle Tests, auch die älteren).

```bash
git add ai/execution/actions.py ai/execution/serve.py ai/execution/cli.py ai/tests/test_actions.py ai/tests/test_serve.py ai/tests/test_execution_cli.py
git commit -m "feat(ai): Aktionen gegen HA, Dauerbetrieb und Kommandozeile der Ausfuehrung" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Auslieferung: Image, Compose, Umgebung und README

**Files:**
- Modify: `ai/Dockerfile`, `ai/docker-compose.yml`, `ai/.env.example`, `ai/README.md`
- Test: `ai/tests/test_packaging.py`

**Interfaces:**
- Consumes: alle vorigen Aufgaben.
- Produces: Image mit dem Paket `execution`; zweiter Dienst `execution` in der Compose-Datei (`python -m execution serve`); Umgebungsvariablen `HA_WRITE_TOKEN`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD`, `EXECUTION_CONFIG`.

- [ ] **Step 1: Failing test schreiben**

`ai/tests/test_packaging.py`:

```python
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && python -m pytest tests/test_packaging.py -v`
Expected: FAIL (`COPY execution` fehlt, Dienst `execution` fehlt).

- [ ] **Step 3: Anpassen**

`ai/Dockerfile`: nach `COPY analysis ./analysis` die Zeile `COPY execution ./execution` einfügen.

`ai/docker-compose.yml`: am Ende anhängen (gleiche Einrückung wie `collector:`):

```yaml

  # Freigabe und Ausführung der KI-Regeln (Teilprojekt 3): legt Regeln in Home Assistant an, wertet sie aus,
  # bedient das Dashboard "KI-Regeln". Selbes Image wie der Collector (zuvor `docker build -t home-ai-collector ai`).
  execution:
    image: home-ai-collector
    container_name: home-ai-execution
    restart: unless-stopped
    command: ["python", "-m", "execution", "serve"]
    depends_on:
      - collector
    environment:
      HA_URL: ${HA_URL:?HA_URL fehlt}
      # Eigener Token (Schreibrechte: Automationen, Helfer, Dashboard anlegen), getrennt vom lesenden HA_TOKEN des Collectors
      HA_WRITE_TOKEN: ${HA_WRITE_TOKEN:?HA_WRITE_TOKEN fehlt}
      MQTT_HOST: ${MQTT_HOST:?MQTT_HOST fehlt}
      MQTT_PORT: ${MQTT_PORT:-1883}
      MQTT_USER: ${MQTT_USER:-}
      MQTT_PASSWORD: ${MQTT_PASSWORD:-}
      # Für die Aufträge "Analyse mit Modell" im Dashboard
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}
      OPENROUTER_MODEL: ${OPENROUTER_MODEL:-}
      OPENROUTER_PRICE_INPUT: ${OPENROUTER_PRICE_INPUT:-}
      OPENROUTER_PRICE_OUTPUT: ${OPENROUTER_PRICE_OUTPUT:-}
      EXECUTION_CONFIG: /app/config/execution.yaml
      ANALYSIS_CONFIG: /app/config/analysis.yaml
      DB_PATH: /data/home_ai.sqlite
      TZ: Europe/Berlin
    volumes:
      - /mnt/data/nobackup/homeassistant/ai:/data
```

`ai/.env.example` anhängen:

```
# Ausführung der KI-Regeln (Dienst "execution")
# Eigener Long-Lived Access Token mit Schreibrechten (empfohlen: eigener HA-Benutzer "homeai", Administrator)
HA_WRITE_TOKEN=hier-den-schreibtoken-eintragen
# MQTT-Broker (derselbe, den Home Assistant nutzt)
MQTT_HOST=192.168.1.100
MQTT_PORT=1883
MQTT_USER=homeai
MQTT_PASSWORD=hier-das-passwort-eintragen
```

`ai/README.md` anhängen:

````markdown

## Freigabe und Ausführung (Teilprojekt 3)

Der Dienst `execution` legt geprüfte Regeln (Status `bestanden`) als Automationen in Home Assistant an, zunächst im **Schattenmodus** (die Regel meldet nur, was sie getan hätte), wertet sie aus und stellt alles auf dem Dashboard **KI-Regeln** bereit. Was schaltet, liegt in Home Assistant und läuft auch bei gestopptem Container weiter: Blueprint `blueprints/ki_regel.yaml`, die Schalter und der Mindestabstand nach einem Eingriff.

**Einrichten (einmalig)**
1. `blueprints/ki_regel.yaml` nach `/config/blueprints/automation/homeai/ki_regel.yaml` in Home Assistant kopieren; `heating_control.yaml` (V4.29, neue Eingabe `ki_rules_toggle`) in den Instanzen aktualisieren.
2. In Home Assistant einen Benutzer „homeai“ (Administrator) und einen langlebigen Token anlegen; in den Stack-Variablen `HA_WRITE_TOKEN`, `MQTT_HOST`, `MQTT_USER`, `MQTT_PASSWORD` setzen (siehe `.env.example`). Image neu bauen, Stack neu deployen.
3. Helfer und Hilfsautomationen anlegen und prüfen:

```bash
docker exec home-ai-execution python -m execution init
docker exec home-ai-execution python -m execution check
```

`init` nennt je Raum den Helfer, den du in der `heating_control`-Instanz unter „KI-Regeln erlaubt (Schalter)“ einträgst. Danach `collector origins --rebuild` ausführen, damit die Herkunft der Änderungen aus den Dienstaufrufen berechnet wird (neue Änderungen werden laufend berechnet).

**Normalbetrieb: Dashboard „KI-Regeln“**
Scan, Analyse (Trockenlauf und mit Modell), Kandidaten ansehen, Regeln anlegen und entfernen, Systemprüfung und Status laufen über Knöpfe auf dem Dashboard (ein Auftrag zur Zeit). Die Analyse mit dem Modell kostet Geld: zuerst „Kosten freigeben“ einschalten (gilt 60 Sekunden, nur für einen Lauf), höchstens 3 Läufe je Tag (`execution.yaml`). Weiter in der Shell nötig: Backfill eines Snapshots, Änderungen an den Konfigurationsdateien, Update und Neustart des Containers.

**Kommandos (Einrichtung und Fehlersuche)**

```bash
docker exec home-ai-execution python -m execution rules                 # angelegte Regeln mit Kennzahlen
docker exec home-ai-execution python -m execution deploy 12             # Kandidat 12 im Schattenmodus anlegen
docker exec home-ai-execution python -m execution undeploy r12          # Regel entfernen (Historie bleibt)
```

**Regeln und Schalter.** Eine Regel schaltet nur, wenn der Raumschalter („KI-Regeln erlaubt“), ihr eigener Schalter „aktiv“ an und der Sammelschalter „alle Regeln in Schatten“ aus sind, der Wohnungszustand nicht `Vacation` ist (und der Winter-Modus an) und der letzte Eingriff im Raum mindestens 60 Minuten zurückliegt. In `Away` und `Sleep` darf sie ein Preset oder einen Sollwert setzen; die Heizung schaltet sie nie ein.

**Kennzahlen.** Im Schatten zählt ein Eingriff im Raum zwischen 60 Minuten vor und 90 Minuten nach der Auslösung (der letzte entscheidet): gleiches Ziel = Treffer, anderes Ziel = Fehlauslösung, keiner = neutral. Bei aktiven Regeln: kein Eingriff = bestätigt, anderes Ziel = korrigiert. Das Signal „grün“ gilt ab 10 gewerteten Auslösungen und weniger als 20 % Fehl- bzw. Korrekturquote. Alle Schwellen stehen in `config/execution.yaml`.
````

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/Dockerfile ai/docker-compose.yml ai/.env.example ai/README.md ai/tests/test_packaging.py
git commit -m "feat(ai): Image, Compose-Dienst, Umgebung und README fuer die Ausfuehrung" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Abnahme in der echten Home Assistant (von Hand)

Diese Aufgabe hat keinen automatischen Test. Sie prüft, was nur die echte HA zeigt, und wird mit dem Benutzer zusammen durchgeführt. Jeder Punkt hat ein erwartetes Ergebnis; weicht es ab, wird der Befund notiert und die Spezifikation (Abschnitte 5, 6, 8, 15) bzw. der betroffene Code angepasst, bevor weitergemacht wird. Testraum: Bad.

**Files:** keine Änderungen; Befunde in `docs/superpowers/specs/2026-10-15-home-ai-execution-design.md` (Abschnitt 15) ergänzen.

- [ ] **Step 1: Voraussetzungen**
  - Blueprint `ki_regel.yaml` liegt in HA unter `/config/blueprints/automation/homeai/`, die Automationen sind neu geladen, `heating_control` V4.29 ist in allen vier Instanzen aktualisiert.
  - HA-Benutzer „homeai“ mit Token, Stack-Variablen gesetzt, Image neu gebaut, Stack deployt (`docker logs home-ai-execution` zeigt keinen Fehler).
  - `docker exec home-ai-execution python -m execution check` listet fehlende Teile mit `FEHLT`.

- [ ] **Step 2: `init` und Zuordnung**
  - `docker exec home-ai-execution python -m execution init` legt Sammelschalter, Raumhelfer und die Hilfsautomationen an; `execution check` zeigt danach nur noch das fehlende `ki_rules_toggle`.
  - In jeder `heating_control`-Instanz den Helfer `input_boolean.ki_regeln_<raum>` bei „KI-Regeln erlaubt (Schalter)“ eintragen. `execution check` zeigt keine Zeile mit `FEHLT` mehr.
  - `docker exec home-ai-collector python -m collector origins --rebuild` läuft ohne Fehler.

- [ ] **Step 3: Dashboard und Entitäten**
  - Das Dashboard „KI-Regeln“ erscheint in der Seitenleiste mit den Ansichten Übersicht, Regeln, Kandidaten, Analyse, System; „Container“ ist verbunden.
  - Die Bedienelemente haben die erwarteten Entity-IDs (`button.homeai_job_scan`, `select.homeai_mode`, `switch.homeai_llm_arm`, …). Weichen sie ab (Unterschied bei `default_entity_id`), in `execution/controls.py` und `execution/mqtt.py` anpassen.
  - Knopf „Status aktualisieren“ zeigt das Ergebnis im Markdown-Feld; „Scan (ohne Modell)“ und „Analyse: Trockenlauf“ laufen; „Analyse mit Modell starten“ wird ohne „Kosten freigeben“ abgelehnt.

- [ ] **Step 4: Eine Regel im Schatten**
  - Einen bestandenen Kandidaten für das Bad wählen und „Kandidat im Schatten anlegen“ drücken (oder `execution deploy <id>`). Die Automation `KI-Regel r<id>: …` erscheint in HA, die Entitäten `switch.homeai_r<id>_aktiv` und `sensor.homeai_r<id>_*` entstehen, das Dashboard zeigt die Regel.
  - In Home Assistant die Automation mit „Ausführen“ (Bedingungen überspringen) auslösen. Erwartung: Die Meldung `homeai/rule/r<id>/fired` erscheint (zum Beispiel im MQTT-Explorer), das Thermostat bleibt unverändert, nach etwa einer Minute steht die Auslösung in `docker exec home-ai-execution python -m execution rules`.

- [ ] **Step 5: Aktivieren und Mindestabstand**
  - Raumschalter und Regelschalter „aktiv“ einschalten, Sammelschalter aus, dann die Regel erneut auslösen: das Preset beziehungsweise der Sollwert wird gesetzt, `fired` meldet `ausgefuehrt`.
  - Am Thermostat von Hand drehen und die Regel innerhalb von 60 Minuten auslösen: Grund `handeingriff`, nichts wird geschaltet; `input_datetime.ki_eingriff_bad` hat sich geändert. Eine Änderung durch die Regel selbst oder durch `heating_control` ändert diesen Zeitpunkt nicht.

- [ ] **Step 6: Zustände `Away`, `Sleep`, `Vacation`**
  - Wohnungszustand auf `Away` (Thermostat steht auf `off`): Die Regel setzt ein Preset; Better Thermostat behält das Ziel; beim Wechsel auf `Coming Home` stellt `heating_control` auf `heat` und das Ziel gilt. Mit einem Sollwert statt Preset schaltet die Regel kurz auf `heat` und wieder zurück auf `off`.
  - `Vacation` und ausgeschalteter Winter-Modus: Grund `wohnungszustand`, keine Schaltung.

- [ ] **Step 7: Betrieb ohne Container**
  - `docker stop home-ai-execution`: Das Dashboard zeigt „Container offline“, die Bedienknöpfe sind nicht verfügbar. Regel- und Raumschalter lassen sich weiter umlegen, eine aktive Regel schaltet weiter, der Mindestabstand gilt.
  - Container wieder starten: Kennzahlen und Discovery erscheinen erneut, die Schalterzustände sind erhalten.
  - Home Assistant neu starten: Die Zustände der Regelschalter (optimistisch) bleiben erhalten. Tun sie es nicht, Spezifikation Abschnitt 7 anpassen (Zustand als `input_boolean` je Regel).

- [ ] **Step 8: Aufzeichnung der Automationen (offene Punkte der Spezifikation)**
  - Die Umschaltung um 01:00 in `service_calls` und `change_origin` prüfen (kein Lauscher nötig): Die Aufrufe erscheinen ohne Benutzerkennung, mit derselben Kontext-ID wie die Zustandszeile; in `change_origin` stehen sie als `automation` mit dem Namen der Automation. Am 24.10. bestätigt. Die Zeitplan-Flanke ist erst prüfbar, sobald ein Zeitplan aktiv ist (der Wert 18:30 war nur ein Testbeispiel).
  - Ein Preset-Wechsel per Dashboard-Button und per Kachel steht als `benutzer`; Drehen am Gerät als `geraet_unklar`.

- [ ] **Step 9: Aufträge mit dem Modell**
  - „Kosten freigeben“ einschalten und „Analyse mit Modell starten“ drücken: Der Lauf startet, die Freigabe schaltet sich danach aus, das Ergebnis erscheint. Ein zweiter Druck ohne neue Freigabe wird abgelehnt; der vierte Lauf des Tages wird mit dem Tageslimit abgelehnt.

- [ ] **Step 9b: Ergänzungen aus dem Review**
  - Regel entfernen und denselben Kandidaten erneut anlegen, nachdem der Schalter „aktiv“ an war: Die neue Regel hat eine neue Kennung (`r<id>_2`) und startet im Schatten.
  - `docker logs home-ai-collector` nach dem Start mit dem echten `HA_TOKEN`: Keine Warnung „nicht abonnierbar“; sonst fehlt dem Token die Administratorrolle.
  - Die Meldung `homeai/rule/<id>/fired` im MQTT-Explorer ist gültiges JSON mit `ts`, `mode`, `result`, `reason`, `target`, `current`.
  - Eine Regel genau 59 und 61 Minuten nach einem Handeingriff auslösen (der Blueprint rundet auf eine Dezimale; Abweichung bis zu wenigen Sekunden ist bekannt).

- [ ] **Step 10: Aufräumen**
  - „Regel entfernen“ (oder `execution undeploy r<id>`): Automation und Entitäten sind weg, das Dashboard zeigt die Regel nicht mehr, `execution rules` listet sie nicht, die Historie bleibt in der Datenbank.
  - Befunde in der Spezifikation (Abschnitt 15) festhalten und committen:

```bash
git add docs/superpowers/specs/2026-10-15-home-ai-execution-design.md
git commit -m "docs: Befunde der Abnahme der Ausfuehrung in der echten Home Assistant" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
