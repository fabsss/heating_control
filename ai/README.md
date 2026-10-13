# KI-Datenbasis für Home Assistant

Zeichnet Zustandsänderungen ausgewählter Entitäten dauerhaft in SQLite auf (`home_ai.sqlite`), unabhängig von den 180 Tagen des Home-Assistant-Recorders. Grundlage für die KI-gestützte Heizungssteuerung. Spezifikation: `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md`.

## Welche Entitäten werden aufgezeichnet?

Genau die, die in Home Assistant das Label **`KI_DB_Sync`** tragen. Es gibt keine zweite Liste. Ein Label setzt du in der Oberfläche unter Einstellungen → Geräte & Dienste → Entitäten (Entität öffnen → Label). Raumgebundene Entitäten brauchen einen Bereich, daraus wird der Raum.

Entfernst du das Label, endet die Aufzeichnung innerhalb von 10 Minuten (`active = 0`, `tracked_until` wird gesetzt). Die bisherigen Daten bleiben erhalten. Endgültig löschen:

```bash
docker compose exec collector python -m collector purge-entity sensor.beispiel
```

## Regeln

`config/collector.yaml` enthält nur Regeln (Schwellen, Whitelist je Art, Ausnahmen je Entität), keine Entitäten.

## Starten

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
  python -m collector backfill --db /snapshots/april/home-assistant_v2.db --history-map /config/history_map.yaml
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

Der Collector kopiert die Datenbank täglich um 03:30 Uhr nach `./backup/home_ai.sqlite`. Dieser Ordner lässt sich vom Proxmox Backup Server mitsichern.

## Hinweise

- Zeitstempel sind UTC.
- Lücken (Verbindungsabbruch, Neustart) werden aus der History-API von Home Assistant nachgeholt, höchstens 180 Tage zurück. Diese Zeilen haben `source = 'gap:history_api'` und tragen keinen Benutzerkontext, daher erscheinen manuelle Eingriffe während einer Lücke nicht in `manual_actions`.
- Tokens und Backups gehören nicht ins Repository.
