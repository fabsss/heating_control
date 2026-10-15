# KI-Datenbasis für Home Assistant

Zeichnet Zustandsänderungen ausgewählter Entitäten dauerhaft in SQLite auf (`home_ai.sqlite`), unabhängig von den 180 Tagen des Home-Assistant-Recorders. Grundlage für die KI-gestützte Heizungssteuerung. Spezifikation: `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md`.

## Welche Entitäten werden aufgezeichnet?

Genau die, die in Home Assistant das Label **`KI_DB_Sync`** tragen. Es gibt keine zweite Liste. Ein Label setzt du in der Oberfläche unter Einstellungen → Geräte & Dienste → Entitäten (Entität öffnen → Label). Raumgebundene Entitäten brauchen einen Bereich, daraus wird der Raum.

Entfernst du das Label, endet die Aufzeichnung innerhalb von 10 Minuten (`active = 0`, `tracked_until` wird gesetzt). Die bisherigen Daten bleiben erhalten. Endgültig löschen:

```bash
docker compose exec collector python -m collector purge-entity sensor.beispiel
```

## Regeln

`config/collector.yaml` enthält nur Regeln (Schwellen, Whitelist je Art, Ausnahmen je Entität), keine Entitäten. Die Datei wird ins Image eingebaut; nach einer Änderung das Image neu bauen (`docker compose up -d --build`).

## Starten

Einmalig auf dem Host die Ordner anlegen (siehe „Sicherung“):

```bash
mkdir -p /mnt/data/nobackup/homeassistant/ai /mnt/data/backup/databases/home-ai-collector
```

```bash
cp .env.example .env     # Token und Adresse eintragen
docker compose up -d --build
docker compose logs -f collector
```

Nach dem Start erscheint `Registry-Abgleich: N Entitäten mit Label KI_DB_Sync`. Trägt keine Entität das Label, beendet sich der Collector mit einer Fehlermeldung.

## Alte Daten einspielen (nur lesend)

1. Im Proxmox Backup Server den Snapshot der Home-Assistant-VM wählen, „File Restore“ öffnen und auf der Datenpartition `home-assistant_v2.db` samt `-wal` und `-shm` herunterladen (HAOS: `/mnt/data/supervisor/homeassistant/`).
2. Die Dateien in einen eigenen Ordner unter `/mnt/data/nobackup/homeassistant/snapshots/` legen (z. B. `june/`). Dieser Ordner ist im Container unter `/snapshots` eingebunden (Stack neu deployen, wenn die Compose-Datei ihn noch nicht hatte).
3. Einspielen im laufenden Container (`HA_TOKEN` und `HA_URL` stehen dort schon):

```bash
docker exec home-ai-collector python -m collector backfill --db /snapshots/june/home-assistant_v2.db
```

Mit `--history-map /app/config/history_map.yaml` lassen sich umbenannte Entitäten zuordnen (nur nötig, wenn es welche gibt).

Mehrfaches Einspielen derselben oder überlappender Dateien ist unschädlich.

## Prüfen

```bash
docker exec home-ai-collector python -m collector verify --db /snapshots/june/home-assistant_v2.db
```

Vergleicht die abgeleiteten manuellen Eingriffe (`manual_actions`) mit den Ereignissen `thermostat_actor_logged` aus derselben Datenbank. Erwartet: Trefferquote mindestens 95 %.

## Datenbank ansehen

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); print(c.execute('SELECT entity_id, active, tracked_since FROM entities ORDER BY entity_id').fetchall())"
```

## Sicherung

Die Live-Datenbank liegt unter `/mnt/data/nobackup/homeassistant/ai/home_ai.sqlite` und wird nicht direkt gesichert: Sie läuft im WAL-Modus, eine Dateikopie im Betrieb kann inkonsistent sein.

Stattdessen schreibt der Collector täglich um 00:30 Uhr (lokale Zeit, vor dem PBS-Backup um 01:00) mit der SQLite-Backup-Schnittstelle eine konsistente Kopie nach `/mnt/data/backup/databases/home-ai-collector/home_ai.sqlite` (erst in eine temporäre Datei, dann umbenannt). Diese Datei sichert Restic mit. Der Ordnername muss dem Containernamen entsprechen, weil `pre-backup-databases.sh` jeden Ordner unter `databases/` als Container behandelt; für diesen Container überspringt das Skript den Ordner ohne Fehler.

Wiederherstellen: Kopie nach `/mnt/data/nobackup/homeassistant/ai/home_ai.sqlite` legen, Collector starten. Lücken seit der Kopie holt er aus der History-API von Home Assistant nach (höchstens 180 Tage, ohne Benutzerkontext).

## Hinweise

- Zeitstempel sind UTC.
- Lücken (Verbindungsabbruch, Neustart) werden aus der History-API von Home Assistant nachgeholt, höchstens 180 Tage zurück. Diese Zeilen haben `source = 'gap:history_api'` und tragen keinen Benutzerkontext, daher erscheinen manuelle Eingriffe während einer Lücke nicht in `manual_actions`.
- Tokens und Backups gehören nicht ins Repository.

## Analyse (Teilprojekt 2)

Erzeugt aus den manuellen Eingriffen Regelkandidaten (Zeit- oder Ereignisauslöser), lässt ein LLM über OpenRouter (mit Zero Data Retention) Hypothesen formulieren und prüft jeden Kandidaten gegen die Historie. Nichts im Haus ändert sich dadurch; die Kandidaten sind Vorschläge. Spezifikation: `docs/superpowers/specs/2026-10-13-home-ai-analysis-design.md`.

Zwei umschaltbare Verfahren: `weekly` (das Modell sucht Muster im Wochenraster) und `clusters` (ein deterministischer Schritt bündelt Eingriffe samt Kontext, das Modell formuliert die Ursache). Backtest und Speicherung sind identisch, so lassen sich beide vergleichen.

Alle Befehle laufen **im laufenden Collector-Container** (`docker exec`), der Datenbank (`/data`), Konfiguration und die Umgebungsvariablen des Stacks schon hat. Es ist kein weiterer Container und keine Shell-Variable nötig:

```bash
# Vorab: was würde gesendet? (kein Aufruf, kostenlos)
docker exec home-ai-collector python -m analysis run --mode weekly --dry-run

# Lauf (OPENROUTER_API_KEY und OPENROUTER_MODEL stehen in den Stack-Variablen)
docker exec home-ai-collector python -m analysis run --mode weekly

# Vergleichen und ansehen
docker exec home-ai-collector python -m analysis compare
docker exec home-ai-collector python -m analysis list --status bestanden
```

Zusammenhang zwischen Anwesenheit und Eingriffen (lokal, ohne LLM, zeigt Klarnamen):

```bash
docker exec home-ai-collector python -m analysis presence
```

Der Bericht zeigt (1) einen Zeitstrahl um jede Ankunft (zwei Stunden davor bis zwei Stunden danach, 30-Minuten-Abschnitte): bei wie viel Prozent der Ankünfte gab es in dem Abschnitt einen Eingriff, verglichen mit derselben Uhrzeit an anderen Tagen ohne Ankunft (Kontrolle). Die Ankünfte sind nach der Dauer der Abwesenheit davor getrennt (`--classes 45,180`: unter 45 min, 45 bis 180 min, ab 180 min), denn ein Gassi-Gang löst etwas anderes aus als die Heimkehr von der Arbeit. Vorheizen vor der Ankunft zeigt sich in den Abschnitten vor 0. (2) Die Zeit seit der letzten Ankunft bei Eingriffen im Vergleich zu zufälligen Zeitpunkten.

Die Regeln liegen in `config/analysis.yaml` (Modell, Kostenlimit, Schwellen des Backtests, Ausschlüsse, Raumnamen) und sind ins Image eingebaut; nach einer Änderung das Image neu bauen. Der Schlüssel steht nie in einer Datei. Zugangsdaten und Modell kommen aus Umgebungsvariablen:

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `OPENROUTER_API_KEY` | ja | OpenRouter-Schlüssel (nur aus der Umgebung, nie aus der Datei); im Stack setzen, die Compose-Datei reicht ihn an den Container weiter |
| `OPENROUTER_MODEL` | nein | überschreibt `model` aus `analysis.yaml`, z. B. `anthropic/claude-sonnet-4.5` |
| `OPENROUTER_PRICE_INPUT`, `OPENROUTER_PRICE_OUTPUT` | nein | USD je Million Token; nur nötig, um die Preise zu erzwingen |

Die Preise holt `analysis run` selbst aus der öffentlichen OpenRouter-Modellliste (`/api/v1/models`, ohne Schlüssel) und gibt sie vor dem Lauf auf stderr aus, z. B. `Preise: 3.00 / 15.00 USD je Million Token (OpenRouter)`. Reihenfolge: Umgebungsvariablen, dann OpenRouter, dann `price_per_mtok` in `analysis.yaml` (Rückfall, wenn OpenRouter nicht erreichbar ist oder das Modell nicht kennt). Die Preise gehen nur in die Kostenschätzung vor dem Aufruf ein; abgerechnet wird nach den Kosten, die OpenRouter im Ergebnis meldet.

Modell oder Schlüssel ändern: Variable im Stack (Dockhand) anpassen und den Stack neu deployen. Für einen einzelnen Lauf mit anderem Modell: `docker exec -e OPENROUTER_MODEL='vendor/modell' home-ai-collector python -m analysis run --mode weekly`.

Hinweise:

- Das Modell sieht nur verdichtete Muster. Personen sind `person_1`, `person_2`; Räume stehen im Klartext. `--dry-run` zeigt den vollständigen Text.
- Ein Lauf bricht ohne Aufruf ab, wenn weniger als `min_interventions` Eingriffe im Zeitraum liegen oder das geschätzte Kostenlimit überschritten würde.
- Kandidaten mit Status `nicht_pruefbar` nutzen etwas, das das System nicht nachrechnen kann (unbekannter Raum, fehlender Sensor); die Notiz nennt den Grund.
- Das Ende des Ausschlusses für das Schlafzimmer in `analysis.yaml` ist das Datum des Thermostattauschs und beim Einrichten anzupassen.

### Signale und automatische Voranalyse (Scan)

Jede Entität mit dem Label `KI_DB_Sync` (außer den Thermostaten) ist ein **Signal** mit einer dauerhaften Kennung `sig_01`, `sig_02` …, deren Art (Kategorie, Zahl, Zeitstempel) aus den Daten erkannt wird. Der Scan prüft für jedes Signal automatisch, ob in der Nähe seiner Ereignisse auffällig oft manuell eingegriffen wird (Vergleich mit derselben Uhrzeit an anderen Tagen, Korrektur für viele Tests). Das Modell bekommt im Modus `scan` die Ergebnisse und formuliert daraus Regeln mit einem allgemeinen Vokabular (beliebiges Signal als Auslöser oder Bedingung, Versatz, Dauer des vorherigen Zustands).

```bash
docker exec home-ai-collector python -m analysis signals          # Kennungen, Art, Beschreibung, lokal die Entität
docker exec home-ai-collector python -m analysis scan             # Assoziationen ohne Modell, mit Klarnamen lokal
docker exec home-ai-collector python -m analysis scan --room Wohnzimmer --top 50
docker exec home-ai-collector python -m analysis run --mode scan --dry-run
docker exec home-ai-collector python -m analysis run --mode scan
```

Beschreibungen für das Modell stehen in `signal_labels:` in `analysis.yaml` (keine Klarnamen). Die Grenzen des Scans (`min_events`, `min_hits`, `q_max`, `top_k`, Dauerklassen) stehen unter `scan:`.

**Wecker:** Die beiden `*_next_alarm`-Sensoren (Zeitstempel) liefern das Ereignis „Weckzeit erreicht“. Die beiden `*_alarmclock_triggered`-Sensoren sind Template-Sensoren aus der `configuration.yaml` und brauchen ein `unique_id`, damit sie in der Registry stehen und das Label tragen können:

```yaml
template:
  - sensor:
      - name: fabians_mobile_alarmclock_triggered
        unique_id: fabians_mobile_alarmclock_triggered
        state: "..."          # wie bisher
```

Danach Template-Entitäten neu laden, Label setzen und den Backfill der Snapshots wiederholen (idempotent).

## Freigabe und Ausführung (Teilprojekt 3)

Der Dienst `execution` legt geprüfte Regeln (Status `bestanden`) als Automationen in Home Assistant an, zunächst im **Schattenmodus** (die Regel meldet nur, was sie getan hätte), wertet sie aus und stellt alles auf dem Dashboard **KI-Regeln** bereit. Was schaltet, liegt in Home Assistant und läuft auch bei gestopptem Container weiter: Blueprint `blueprints/ki_regel.yaml`, die Schalter und der Mindestabstand nach einem Eingriff.

**Einrichten (einmalig)** – ausführlich mit Prüfpunkten, Zurücknehmen und Fehlersuche in [INBETRIEBNAHME.md](INBETRIEBNAHME.md).
1. `blueprints/ki_regel.yaml` nach `/config/blueprints/automation/fabsss/ki_regel.yaml` in Home Assistant kopieren; `heating_control.yaml` (V4.29, neue Eingabe `ki_rules_toggle`) in den Instanzen aktualisieren.
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
