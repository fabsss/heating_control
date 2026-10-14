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
2. Die Dateien in einen eigenen Ordner unter `snapshots/` legen (liegt nicht im Repository).
3. Einspielen (Ordner schreibbar einbinden, SQLite öffnet die Datei trotzdem nur lesend):

```bash
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector backfill --db /snapshots/april/home-assistant_v2.db --history-map /app/config/history_map.yaml
```

Mehrfaches Einspielen derselben oder überlappender Dateien ist unschädlich.

## Prüfen

```bash
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector verify --db /snapshots/april/home-assistant_v2.db
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

Die Regeln liegen in `config/analysis.yaml` (Modell, Kostenlimit, Schwellen des Backtests, Ausschlüsse, Raumnamen) und sind ins Image eingebaut; nach einer Änderung das Image neu bauen. Der Schlüssel steht nie in einer Datei. Zugangsdaten und Modell kommen aus Umgebungsvariablen:

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `OPENROUTER_API_KEY` | ja | OpenRouter-Schlüssel (nur aus der Umgebung, nie aus der Datei) |
| `OPENROUTER_MODEL` | nein | überschreibt `model` aus `analysis.yaml`, z. B. `anthropic/claude-sonnet-4.5` |
| `OPENROUTER_PRICE_INPUT`, `OPENROUTER_PRICE_OUTPUT` | nein | USD je Million Token, überschreiben `price_per_mtok` (nur für die Kostenschätzung vor dem Aufruf; abgerechnet wird nach den Kosten, die OpenRouter meldet). Bei einem anderen Modell mitsetzen, sonst stimmt die Schätzung nicht. |

Beispiel für einen Lauf mit anderem Modell:

```bash
read -s OPENROUTER_API_KEY; export OPENROUTER_API_KEY
docker run --rm -e OPENROUTER_API_KEY -e OPENROUTER_MODEL='vendor/modell' -e OPENROUTER_PRICE_INPUT=1.0 -e OPENROUTER_PRICE_OUTPUT=5.0 \
  -v /mnt/data/nobackup/homeassistant/ai:/data home-ai-collector python -m analysis run --mode weekly
```

Hinweise:

- Das Modell sieht nur verdichtete Muster. Personen sind `person_1`, `person_2`; Räume stehen im Klartext. `--dry-run` zeigt den vollständigen Text.
- Ein Lauf bricht ohne Aufruf ab, wenn weniger als `min_interventions` Eingriffe im Zeitraum liegen oder das geschätzte Kostenlimit überschritten würde.
- Kandidaten mit Status `nicht_pruefbar` nutzen etwas, das das System nicht nachrechnen kann (unbekannter Raum, fehlender Sensor); die Notiz nennt den Grund.
- Das Ende des Ausschlusses für das Schlafzimmer in `analysis.yaml` ist das Datum des Thermostattauschs und beim Einrichten anzupassen.
