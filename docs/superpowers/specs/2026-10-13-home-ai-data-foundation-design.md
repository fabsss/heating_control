# KI-Datenbasis für Home Assistant (erste Anwendung: Heizung) – Teilprojekt 1

Stand: 2026-10-13 · Status: Entwurf zur Freigabe

## 1. Einordnung

Die Datenbasis ist **anwendungsneutral** gebaut. Erste Anwendung ist die Heizung, als zweite ist die Beleuchtung vorgesehen (siehe Abschnitt 12). Benennung und Architektur sind deshalb nicht heizungsspezifisch: Das Label heißt `KI_DB_Sync`, die Datenbank `home_ai.sqlite`, und jede Entität gehört zu einer Anwendungsdomäne (`heating`, später `lighting`).

Die erste Anwendung ist ein KI-gestützter Scheduler für die Heizung mit zwei Zielen:

1. Vorschläge für Zeitpläne und Regeln pro Raum, abgeleitet aus dem Verhalten der Bewohner.
2. Ein Auto-Modus, der Raumtemperaturen anhand freigegebener Regeln selbst anpasst.

Beispiele für Regeln, die das System selbst finden soll (nicht vorgegeben):
- Svenja kommt unter der Woche heim → Wohnzimmer auf Komfort.
- Fabian wird geweckt und hat keinen Office-Day → Arbeitszimmer auf Komfort; bleibt er abends lange weg → wieder Eco.

Das Gesamtprojekt besteht aus drei Teilprojekten, jedes mit eigener Spezifikation und Umsetzung:

| # | Teilprojekt | Inhalt |
|---|-------------|--------|
| 1 | **Datenbasis** (dieses Dokument) | Langzeit-Datenspeicher, Collector, Backfill aus alten Backups |
| 2 | Analyse | Verdichtung, LLM-Hypothesen über OpenRouter (ZDR), Backtest |
| 3 | Laufzeit | Regel-Blueprint, Freigabe-Dashboard, Schattenmodus, Auto-Modus |

Grundprinzip des Gesamtsystems: *Ein LLM schlägt vor, die Daten prüfen, du gibst frei, Home Assistant führt deterministisch aus.* Das Modell ist nie in der Echtzeitschleife.

## 2. Ziel und Erfolgskriterien von Teilprojekt 1

**Ziel:** Ein dauerhafter, lokaler Datenbestand über Heizverhalten und Kontext (Anwesenheit, Umgebung), der nicht auf die 180 Tage des Home-Assistant-Recorders begrenzt ist und die letzte Heizsaison aus alten Backups enthält.

**Erfolgskriterien:**
1. Alle Entitäten aus Abschnitt 5 werden live aufgezeichnet; der Collector überlebt Verbindungsabbrüche und Neustarts ohne Lücken (Lücken werden aus der History-API nachgeholt).
2. Der Backfill ist idempotent: Zweimaliges Einspielen derselben Datenbank ändert die Zeilenzahl nicht.
3. Die View `manual_actions` reproduziert für den Zeitraum, in dem der Logger `thermostat_actor_logged` lief (seit 2025-10-21), die dort protokollierten manuellen Sollwert- und Modusänderungen (gleiche Zeitpunkte, gleiche Benutzer).
4. Eine neue Entität lässt sich ohne Codeänderung aufnehmen (Label setzen).
5. Die letzte Heizsaison (ca. Oktober 2025 bis April 2026) ist per Backfill im Datenbestand.

**Nicht Teil dieses Teilprojekts:** Analyse, LLM-Anbindung, Regeln, Dashboard, Auto-Modus. Die Erweiterung des Blueprints `log_trv_actor_with_context.yaml` um Preset und Kontext ist optional und ein späterer, kleiner Schritt, weil der Rohstrom Presets bereits erfasst.

## 3. Entscheidungen

| Thema | Entscheidung |
|-------|--------------|
| Laufzeitumgebung | Docker-Container neben Home Assistant OS auf demselben Proxmox-Host |
| Sprache | Python mit asyncio |
| Home-Assistant-Anbindung | WebSocket-API, Long-Lived Access Token als Umgebungsvariable |
| Speicher | SQLite (WAL-Modus), alle Zeiten in UTC |
| Aufnahmeverfahren | Rohstrom von `state_changed` statt nur Events; manuelle Eingriffe werden in der Analyse abgeleitet |
| Schreibintervall | Gebündelt alle **30 Sekunden** und beim ordentlichen Beenden; konfigurierbar |
| Erweiterbarkeit | Ein einziges Home-Assistant-Label `KI_DB_Sync` für alle Anwendungen, plus Standardregeln je Gerätetyp |
| Quelle der Entitäten | Ausschließlich das Label `KI_DB_Sync`; `collector.yaml` enthält nur Regeln, keine Entitäten |
| Anwendungsneutralität | Jede Entität hat eine `domain` (`heating`, später `lighting`); Namen von Datei, Tabellen und Views sind nicht heizungsspezifisch |
| Historische Daten | Auslesen der `home-assistant_v2.db` aus Proxmox-Backup-Server-Snapshots, kein Wiederherstellen |
| Ablage | Verzeichnis `ai/` in diesem Repository |
| Datenschutz | Rohdaten bleiben lokal; nur verdichtete Tabellen gehen später an ein LLM (Teilprojekt 2) |

## 4. Architektur

```
Home Assistant (HAOS-VM)                 Docker-Container "collector"
  WebSocket-API  ── state_changed ──▶    ┌────────────────────────────┐
  History-API    ◀── Lückenabgleich ──   │ collector (live)           │
  Entity Registry ◀── Label/Bereich ──   │   Filter → Entprellen →    │
                                         │   Puffer (30 s) → SQLite   │
Proxmox Backup Server                    │ backfill (CLI)             │
  File Restore → home-assistant_v2.db ─▶ │   liest alte HA-DB         │
                                         │   gleiche Umwandlung       │
                                         └─────────────┬──────────────┘
                                                       ▼
                                              home_ai.sqlite (Volume)
                                              nächtliche Kopie → PBS
```

Collector und Backfill teilen sich dieselbe Umwandlungslogik (Filter, Speicherregeln, Attribut-Whitelist), damit alte und neue Daten im selben Format liegen.

## 5. Erfasste Entitäten

### 5.1 Startbestand (wird per Label vergeben)

**Einzige Quelle** dafür, welche Entitäten aufgezeichnet werden, ist das Label `KI_DB_Sync` in Home Assistant. Es gibt keine zweite Liste in einer Konfigurationsdatei. Die folgende Tabelle ist der Satz an Entitäten, der einmalig bei der Einrichtung mit dem Label versehen wird (siehe 5.2).

| Gruppe | Entitäten | Speicherregel |
|--------|-----------|---------------|
| Thermostate | `climate.better_thermostat_livingroom`, `_bedroom`, `_bathroom`, `_office` | siehe 6.2 |
| Personen | `person.fabian`, `person.svenja` | jede Änderung |
| Wohnung | `input_select.flat_occupation_state`, `input_button.sleep_mode`, `input_boolean.someone_in_bed`, `binary_sensor.office_day_template` | jede Änderung |
| Heizungsschalter | `input_boolean.heating_on_off`, `input_boolean.heating_automation`, `schedule.heating_schedule_livingroom`, `_bathroom`, `_office` | jede Änderung |
| Anwesenheit | `binary_sensor.presence_office_presence`, `binary_sensor.presence_kitchen_presence`, `binary_sensor.front_door_contact` | jede Änderung |
| Raum des Handys | `sensor.fabians_s22_presence_tracker_area_last_seen` | Raumwechsel erst nach 60 s Verweildauer |
| Fenster | `binary_sensor.windows_livingroom_and_door`, `binary_sensor.windows_bedroom`, `binary_sensor.window_bathroom_contact`, `binary_sensor.window_office_balcony_door_contact`, `binary_sensor.window_office_balcony_door_bottom_contact` | jede Änderung |
| Umgebung | `sensor.environment_sensor_{livingroom,bedroom,bathroom,kitchen,outside}_temperature` und `_humidity`, `sensor.office_air_quality_sensor_temperature`, `weather.home` (Zustand, Temperatur, Feuchte, Windgeschwindigkeit) | siehe 6.2 |

Alle Entitäten dieser Liste mit Thermostaten, Zeitplänen und Heizungsschaltern erhalten `domain = heating`; Personen, Wohnung, Anwesenheit, Raum des Handys, Fenster, Umgebung und Wetter erhalten `domain = shared`.

Das Schlafzimmer hat keinen Zeitplan. Das ist korrekt und kein Fehler. Für Svenja gibt es keinen Raumsensor; die Analyse muss damit umgehen, dass Raumdaten nur für eine Person vorliegen.

### 5.2 Aufnahme neuer Entitäten

- **Mitgliedschaft = Label.** Der Collector liest beim Start und danach in regelmäßigen Abständen (alle 10 Minuten) die Entity Registry und nimmt jede Entität mit dem Label `KI_DB_Sync` auf. Das Label hängt an der Registry-Kennung und überlebt Umbenennungen von Entitäten.
- **Raum:** kommt aus dem in Home Assistant zugeordneten Bereich. Raumgebundene Entitäten (Thermostate, Präsenz, Fenster, Umgebungssensoren) ohne Bereich werden aufgenommen, der Collector meldet aber eine Warnung, weil die Analyse sie keinem Raum zuordnen kann.
- **`domain`:** kommt aus einer Zuordnung in `collector.yaml` (Standard: Entitätsdomäne `climate` → `heating`, später `light` → `lighting`). Entitäten ohne eindeutige Zuordnung, etwa Personen oder Anwesenheitssensoren, erhalten `domain = shared` und gelten für alle Anwendungen.
- **`collector.yaml` enthält nur Regeln, keine Entitäten:** Standardwerte je Domäne und `device_class` (Temperatur 0,5 °C, Luftfeuchte 5 %, Binärsensoren jede Änderung), die Attribut-Whitelist je `kind` und Ausnahmen je `entity_id` (z. B. Entprellung für den Handy-Raumsensor). Eine Ausnahme für eine Entität ohne Label hat keine Wirkung.
- **Einschränkung von Labels:** Nur Entitäten mit einer eindeutigen Kennung in der Entity Registry können ein Label tragen. Entitäten ohne sie (z. B. manche YAML-Template-Sensoren) lassen sich nicht aufnehmen. Das wird bei der Einrichtung geprüft.
- Wird eine Entität neu erkannt, holt der Collector ihren Verlauf aus der History-API von Home Assistant nach (höchstens 180 Tage). Älteres liefert der Backfill.
- `entities.tracked_since` hält fest, ab wann Daten vorliegen. Die Analyse behandelt fehlende Daten davor als unbekannt, nicht als „aus“.
- **Einrichtung des Startbestands:** Das Label `KI_DB_Sync` wird einmalig angelegt und den Entitäten aus 5.1 zugewiesen. Das kann in der Home-Assistant-Oberfläche oder über die MCP-Werkzeuge geschehen. Fehlt das Label oder trägt keine Entität es, beendet sich der Collector mit einer klaren Fehlermeldung, statt still nichts aufzuzeichnen.

### 5.3 Entfernen einer Entität

- **Label entfernt:** Die Entität wird beim nächsten Abgleich der Entity Registry (spätestens nach 10 Minuten) auf `active = 0` gesetzt und `tracked_until` erhält den Zeitpunkt. Ab dann kommen keine neuen Daten mehr hinzu.
- **Bereits aufgezeichnete Daten bleiben erhalten.** Es wird nichts automatisch gelöscht, weil ein versehentlich entferntes Label sonst Monate an Verlauf vernichten würde. Die Analyse behandelt die Zeit nach `tracked_until` als unbekannt.
- **Label wieder gesetzt:** `active` wird wieder 1, `tracked_until` wird geleert, und die Lücke dazwischen wird aus der History-API nachgeholt, soweit sie noch im Recorder liegt (höchstens 180 Tage).
- **Endgültig löschen** geht nur über einen ausdrücklichen Befehl, z. B. `purge-entity <entity_id>`, der die Zeilenzahl nennt und eine Bestätigung verlangt.
- **Alle Entitäten gelten gleich.** Es gibt keine Sonderfälle für Entitäten des Startbestands.

## 6. Datenmodell

### 6.1 Schema (SQLite, UTC)

```sql
entities(
  entity_id     TEXT PRIMARY KEY,
  domain        TEXT NOT NULL,       -- heating|lighting|shared
  kind          TEXT NOT NULL,       -- climate|light|scene|person|flat|schedule|presence|contact|env|weather|button
  room          TEXT,
  tracked_attrs TEXT,                -- JSON-Liste der gespeicherten Attribute
  min_delta     REAL,                -- Schwelle für Zahlenwerte
  heartbeat_s   INTEGER,             -- spätestens nach dieser Zeit speichern
  debounce_s    INTEGER,             -- z. B. 60 für area_last_seen
  tracked_since TEXT,                -- ISO-8601 UTC, ab hier liegen Daten vor
  active        INTEGER NOT NULL DEFAULT 1,  -- 0 = wird nicht mehr aufgezeichnet
  tracked_until TEXT                 -- ISO-8601 UTC, gesetzt wenn active = 0
);

room_map(
  room        TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  role        TEXT NOT NULL,         -- thermostat_bt|thermostat_trv|temp|humidity|presence|window|light|scene
  valid_from  TEXT,
  valid_to    TEXT                   -- NULL = noch gültig
);

users(
  user_id       TEXT PRIMARY KEY,    -- Home-Assistant-Benutzer-ID
  person_entity TEXT,
  name          TEXT
);

state_changes(
  id                INTEGER PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  entity_id         TEXT NOT NULL,
  state             TEXT,
  attrs             TEXT,            -- JSON, nur Whitelist
  attrs_hash        TEXT NOT NULL,
  context_id        TEXT,
  context_user_id   TEXT,
  context_parent_id TEXT,
  source            TEXT NOT NULL,   -- 'live' oder 'backfill:<dateiname>'
  UNIQUE(entity_id, ts_utc, state, attrs_hash)
);
CREATE INDEX idx_sc_entity_ts ON state_changes(entity_id, ts_utc);
CREATE INDEX idx_sc_user ON state_changes(context_user_id);

ingest_runs(
  id INTEGER PRIMARY KEY, source TEXT, from_ts TEXT, to_ts TEXT,
  rows INTEGER, status TEXT, started_at TEXT
);
```

Namen werden erst bei der Analyse aufgelöst (`users`), gespeichert wird die ID.

### 6.2 Speicherregeln

- **Thermostate:** Speichern bei Änderung von `state` (hvac_mode), `temperature`, `preset_mode` oder `window_open`, oder wenn `current_temperature` sich um mindestens 0,5 °C gegenüber dem zuletzt gespeicherten Wert unterscheidet. Whitelist der Attribute: `temperature`, `preset_mode`, `hvac_action`, `current_temperature`, `current_humidity`, `window_open`. Alle übrigen Attribute werden verworfen (z. B. `heating_power`, `heat_loss`, `batteries`).
- **Zahlenwerte:** Speichern bei Änderung ab `min_delta` oder spätestens nach `heartbeat_s` (Standard 1800 s).
- **Schalter, Personen, Kontakt- und Präsenzsensoren, Zeitpläne:** Jede Änderung.
- **Raum des Handys:** Ein Raumwechsel wird erst gespeichert, wenn der neue Raum mindestens `debounce_s` (60 s) gehalten wurde.
- **`unavailable` und `unknown`:** Werden gespeichert (sie sind Information über Ausfälle), aber von der Analyse nicht als Messwert verwendet.

### 6.3 Zuordnung von Eingriffen (View `manual_actions`)

Die View ist anwendungsneutral und liefert pro Änderung die Spalten `ts_utc`, `domain`, `room`, `entity_id`, `changed_attrs`, `old`, `new`, `actor_class`, `actor_user_id`. In dieser Version deckt sie nur Thermostate ab (`domain = heating`). Aus `state_changes` der Thermostate werden Änderungen von `temperature`, `preset_mode` und Modus gegenüber dem vorigen gespeicherten Zeitpunkt der Entität ermittelt und so klassifiziert:

| Bedingung | Klasse |
|-----------|--------|
| `context_user_id` gesetzt, `context_parent_id` leer | **manuell** (Mensch, direkt) |
| `context_parent_id` gesetzt | Automation oder Better Thermostat |
| `context_user_id` leer, `context_parent_id` leer | unbekannt (z. B. Eingriff am Gerät) |

Änderungen durch die Heizungsautomatik werden **ebenfalls aufgezeichnet**, denn der Rohstrom speichert jede Änderung der überwachten Attribute, unabhängig vom Verursacher. Sie erscheinen in der Klasse *Automation oder Better Thermostat*. Das ist nötig, damit die Analyse jederzeit weiß, welches Preset oder welcher Sollwert gerade aktiv war, und eigene Änderungen der Automatik nicht mit manuellen Eingriffen verwechselt. Löst ein Mensch eine Automation indirekt aus (z. B. über `flat_occupation_state`), steht der Benutzer nicht am Thermostat, sondern am auslösenden Zustandswechsel. Die Auflösung dieser Kontextkette ist wie bei Szenen (Abschnitt 12) vorgesehen, aber in diesem Teilprojekt nicht umgesetzt.

Als Lernsignal gilt nur die Klasse *manuell*. Eingriffe am Thermostat selbst sind laut Nutzer selten; sie werden deshalb nicht als manuell gewertet. Eine Heuristik dafür (Zeitabstand zu Automationsaktionen) ist bei Bedarf nachrüstbar.

## 7. Komponenten

### 7.1 Collector (live)

- Verbindet sich per WebSocket, abonniert `state_changed`, filtert im Code auf die aktive Entitätenliste und wendet Speicherregeln und Entprellung an.
- Schreibt gepuffert alle **30 Sekunden** und beim ordentlichen Beenden (SIGTERM) in SQLite (eine Transaktion pro Schreibvorgang).
- **Wiederverbindung:** Exponentieller Backoff. Nach einem Abbruch holt der Collector die Lücke seit dem letzten gespeicherten Zeitstempel je Entität über die History-API nach.
- **Konfiguration:** `collector.yaml` (URL, Label-Name, Standardregeln, Ausnahmen je Entität, Schreibintervall); sie enthält keine Entitätenliste. Das Token kommt aus der Umgebungsvariable `HA_TOKEN`.
- **Betrieb:** Docker-Healthcheck prüft, dass in den letzten 10 Minuten eine Nachricht von Home Assistant ankam.
- **Nächtliche Kopie** der SQLite-Datei (SQLite-Online-Backup) auf ein zweites Ziel; die Datei lässt sich vom Proxmox Backup Server mitsichern.

### 7.2 Backfill (Kommandozeile)

```
backfill --db <home-assistant_v2.db> [--from <ts>] [--to <ts>] [--config collector.yaml] [--history-map history_map.yaml]
```

- Öffnet die Home-Assistant-Datenbank **nur lesend**.
- Liest Zustände aus den Tabellen `states`, `states_meta` und `state_attributes` und berücksichtigt die Schemaunterschiede über die Jahre (z. B. Benutzer-ID als `context_user_id_bin`).
- Wendet dieselben Filter-, Whitelist- und Speicherregeln wie der Collector an.
- Schreibt mit `source = 'backfill:<dateiname>'`. Wegen `UNIQUE` ist das mehrfache Einspielen und das Einspielen überlappender Zeiträume unschädlich.
- Protokolliert jeden Lauf in `ingest_runs`.
- **Welche Entitäten** eingespielt werden, bestimmt dasselbe Label wie beim Collector (Entitäten mit `KI_DB_Sync`).
- **Historische Zuordnung:** Frühere direkte Thermostate (`climate.trv_*`) und umbenannte oder entfernte Entitäten tragen kein Label mehr oder nie eines. Ihre Zuordnung zu Räumen mit Gültigkeitszeitraum steht deshalb in einer eigenen Datei `history_map.yaml`, die **nur vom Backfill** gelesen wird und keine Auswirkung auf die Live-Aufzeichnung hat. Das ist keine zweite Quelle für die Mitgliedschaft, sondern eine Übersetzungstabelle für Vergangenes.

## 8. Alte Daten aus den Backups holen

1. Im Proxmox-Backup-Server oder in der Proxmox-Oberfläche den Snapshot der Home-Assistant-VM wählen und die **Dateiwiederherstellung** (File Restore) öffnen.
2. Auf der Datenpartition (ext4) `home-assistant_v2.db` herunterladen, zusammen mit `home-assistant_v2.db-wal` und `-shm`, falls vorhanden. Pfad bei HAOS: `/mnt/data/supervisor/homeassistant/`.
3. Empfohlen: **ein Snapshot von Mitte April 2026** (Verlauf reicht bei 180 Tagen bis etwa Mitte Oktober 2025) und **ein Snapshot von Oktober oder November 2025** zur Absicherung des Anfangs.
4. Die Dateien liegen in einem eigenen leeren Verzeichnis außerhalb des Repos und werden nur lesend an `backfill` übergeben.

Die Backups enthalten Zugangsdaten und Tokens. Sie bleiben lokal und werden nicht committet.

## 9. Fehlerbehandlung

| Fall | Verhalten |
|------|-----------|
| WebSocket-Verbindung bricht ab | Backoff, danach Lückenabgleich über die History-API |
| Home Assistant nicht erreichbar beim Start | Wiederholung, Healthcheck schlägt nach 10 Minuten an |
| SQLite-Schreibfehler | Puffer bleibt erhalten, nächster Versuch beim folgenden Intervall, Fehler wird protokolliert |
| Ungültiges Token | Start bricht mit klarer Meldung ab |
| Entität verschwindet aus Home Assistant | Wird weiter geführt, es kommen keine Daten mehr; `tracked_since` bleibt erhalten |
| Backfill-Datei mit unbekanntem Schema | Abbruch mit Hinweis auf die Schemaversion, es wird nichts halb geschrieben (eine Transaktion pro Lauf) |
| Umbenannte Entität in alten Daten | Wird über `history_map.yaml` (nur Backfill) einem Raum und einer aktuellen Entität zugeordnet |
| Label fehlt oder trägt keine Entität | Collector beendet sich mit klarer Fehlermeldung |
| Raumgebundene Entität ohne Bereich | Wird aufgenommen, Warnung im Log |

## 10. Tests

- **Einheitentests** mit kleinen Beispieldaten:
  - Speicherregeln: unveränderte Werte werden verworfen, Schwellen und Heartbeat greifen.
  - Zuordnung manuell / Automation / unbekannt.
  - Raumwechsel-Entprellung für `area_last_seen`.
  - Idempotenz des Backfills (zweimal einspielen, gleiche Zeilenzahl).
  - Lückenabgleich nach Verbindungsverlust.
- **Abgleichtest gegen die Realität:** Die View `manual_actions` wird für den Zeitraum seit 2025-10-21 mit den protokollierten `thermostat_actor_logged`-Events verglichen (Erfolgskriterium 3).
- **Rauchtest** des Collectors gegen die echte Instanz, nur lesend, mit einem eigenen Token.

## 11. Risiken und offene Punkte

- **Konsistenz der Snapshot-Datenbank:** Eine Datenbank aus einem laufenden Backup kann unvollständige Schreibvorgänge enthalten. Gegenmaßnahme: `-wal`/`-shm` mitnehmen, `PRAGMA integrity_check` vor dem Backfill.
- **Aufbewahrung damals:** Ob der Recorder in der letzten Saison ebenfalls 180 Tage hielt, zeigt erst die Datei. Fehlen Wochen, wird das in `ingest_runs` sichtbar.
- **Zeitpunkt der Better-Thermostat-Einführung** ist unbekannt. Er bestimmt die Gültigkeitsgrenzen in `room_map` und ist aus den Daten ablesbar (erste Änderung der `better_thermostat_*`-Entitäten).
- **Kontext bei Better Thermostat:** Es wird angenommen, dass eine App-Änderung am Better-Thermostat-Entity die Benutzer-ID im Kontext behält und die Weitergabe an das TRV einen Eltern-Kontext trägt. Das ist am Anfang mit realen Daten zu bestätigen (Erfolgskriterium 3 prüft es).
- **Raumsensor des Handys** zeigt das Gerät, nicht die Person. Er ist ein Hinweis, kein Beleg.
- **Dateien vor dem Labeln:** Eine Entität, die nicht aufgezeichnet wurde, hat nur den Verlauf aus Recorder und Backups.
- **Datenmenge:** Das Büro-Thermostat schrieb zuletzt rund 30.000 Zustandszeilen in zwei Wochen. Die Speicherregeln müssen diese Menge deutlich senken; die tatsächliche Größe der SQLite-Datei wird nach dem ersten Monat geprüft.

## 12. Vorbereitung für die Beleuchtung

Die Beleuchtung wird später über dasselbe Konzept gesteuert. In diesem Teilprojekt wird sie **nicht umgesetzt**, aber die folgenden Punkte werden jetzt festgelegt, damit später nichts umgebaut werden muss:

1. **Ein Label, mehrere Anwendungen:** `KI_DB_Sync` gilt für Heizung und Beleuchtung. Die Zuordnung zur Anwendung läuft über `entities.domain`, nicht über zusätzliche Labels.
2. **Neutrale Namen:** Datei `home_ai.sqlite`, View `manual_actions`, Verzeichnis `ai/`. Heizungsspezifisches steht nur in `collector.yaml` und in den Tests.
3. **Attribut-Whitelist je `kind`:** Für `light` später `state`, `brightness`, `color_temp_kelvin`, `color_mode`, `rgb_color`/`xy_color`/`hs_color`; für `scene` nur `state`. Die Whitelist wird in `collector.yaml` je `kind` gepflegt, nicht im Code.
4. **Entprellung von Dimmvorgängen:** Lichter melden bei Übergängen viele Zwischenwerte. Für `kind = light` gilt eine Sammelzeit (`debounce_s`, Vorschlag 2 s), danach wird nur der Endwert gespeichert. Dieselbe Entprellung existiert bereits für den Raumsensor (6.2).
5. **Szenen als Auslöser:** Wird eine Szene von einem Benutzer aktiviert, tragen die resultierenden Licht-Änderungen als `context_parent_id` die Kontext-ID dieser Aktivierung. Der Benutzer steht nur an der Szene selbst. Deshalb werden Szenen (`scene.*`) später ebenfalls aufgezeichnet und die View löst `context_parent_id` zur Wurzel auf (`actor_user_id`). Die Spalten `context_id` und `context_parent_id` sind dafür bereits Teil des Schemas.
6. **Automation von Mensch unterscheiden:** Die Klassifizierung aus 6.3 bleibt gleich. Eine Auflösung der Kontextkette macht aus „Automation“ bei Bedarf „Mensch über Szene“.
7. **Gemeinsame Kontextdaten:** Personen, Anwesenheit, Raum des Handys, Wohnungszustand, Umgebung und Wetter tragen `domain = shared` und stehen beiden Anwendungen ohne Duplikat zur Verfügung. Helligkeit von Außensensoren (Lux) lässt sich später über das Label aufnehmen.
8. **Unterschiedliche Datenmenge:** Beleuchtung erzeugt mehr Ereignisse als Heizung. Das ist bei SQLite weiter unkritisch; die Größe wird nach dem ersten Monat geprüft (Abschnitt 11).

Nicht festgelegt und bewusst offen: welche Lichtentitäten aufgenommen werden, welche Regeln das System lernen soll und wie Lichtszenen im Laufzeit-Teilprojekt angewendet werden. Das gehört in die Spezifikation der Beleuchtungsanwendung.

## 13. Verzeichnisstruktur (Vorschlag)

```
ai/
  collector/            # Python-Paket: live-Collector, Backfill, gemeinsame Umwandlung
  config/
    collector.yaml      # Regeln (keine Entitätenliste): Standardwerte, Whitelist je kind, Ausnahmen
    history_map.yaml    # nur Backfill: alte Entitäten -> Raum, mit Gültigkeitszeitraum
  sql/
    schema.sql          # Tabellen, Indizes, View manual_actions
  tests/
  Dockerfile
  docker-compose.yml
  README.md
```
