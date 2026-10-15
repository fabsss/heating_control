# Inbetriebnahme der KI-Regeln (Teilprojekt 3)

Schritt für Schritt, vom Merge bis zur ersten Regel im Schattenmodus. Hintergrund und Entscheidungen stehen in `docs/superpowers/specs/2026-10-15-home-ai-execution-design.md`, Kommandos in der [README](README.md#freigabe-und-ausführung-teilprojekt-3).

Die Reihenfolge ist wichtig: Erst die Dateien in Home Assistant (HA), dann der Dienst, dann `init`, dann der Eintrag in `heating_control`.

## Voraussetzungen
- Der Collector läuft (Teilprojekt 1) und hat Daten.
- Ein MQTT-Broker, den HA nutzt, mit der MQTT-Integration in HA.
- HA mindestens in der Version, die der Blueprint verlangt (`min_version` im Kopf von `blueprints/ki_regel.yaml`). Die Discovery mit `default_entity_id` braucht eine aktuelle HA; schlägt das fehl, bleibt die Regel im Schatten.

## 1. Code holen
Im Docker-LXC:

```bash
cd /opt/heating_control && git checkout main && git pull
docker build -t home-ai-collector ai
```

Das Image enthält Collector und Ausführung (derselbe Code, zwei Container).

## 2. Dateien in Home Assistant
1. `blueprints/ki_regel.yaml` nach `/config/blueprints/automation/fabsss/ki_regel.yaml` kopieren oder per Import einspielen (dort liegt auch `heating_control.yaml`). Der Pfad muss zu `blueprint_path` in `ai/config/execution.yaml` passen.
2. `heating_control.yaml` auf V4.29 aktualisieren, in jeder Instanz (Bad, Schlafzimmer, Wohnzimmer, Bastelzimmer). Neu ist nur die optionale Eingabe „KI-Regeln erlaubt (Schalter)“; das Verhalten ändert sich nicht. Den Schalter trägst du erst in Schritt 5 ein.
3. Blueprints in HA neu laden oder HA kurz neu laden (Entwicklerwerkzeuge → YAML), damit der neue Blueprint erscheint.

## 3. Benutzer und Zugangsdaten
| Was | Wo | Hinweis |
|---|---|---|
| HA-Benutzer `homeai` (Administrator) mit langlebigem Token | HA → Profil → Sicherheit | Der Token ist `HA_WRITE_TOKEN`. Er legt Automationen, Helfer und das Dashboard an. |
| Token des Collectors (`HA_TOKEN`) | bestehender Stack | Für die Herkunft der Änderungen braucht er die **Administratorrolle**. Ohne sie läuft die Aufzeichnung weiter, im Log steht eine Warnung und es gibt keine Herkunft. |
| MQTT-Benutzer und Passwort | Broker | Für den Dienst `execution` (`MQTT_USER`, `MQTT_PASSWORD`). |

## 4. Stack-Variablen und Deploy
In Dockhand beim Stack ergänzen (Vorlage: `ai/.env.example`):

```
HA_WRITE_TOKEN=...
MQTT_HOST=...
MQTT_PORT=1883
MQTT_USER=...
MQTT_PASSWORD=...
```

`docker-compose.yml` aus dem Repo übernehmen und den Stack neu deployen. Es laufen dann zwei Container: `home-ai-collector` und `home-ai-execution`. Prüfen:

```bash
docker ps --filter name=home-ai
docker logs home-ai-execution --tail 30
docker logs home-ai-collector --tail 30
```

Im Log des Collectors darf keine Warnung „nicht abonnierbar“ stehen (sonst fehlt `HA_TOKEN` die Administratorrolle).

## 5. Einrichten
Die Räume stehen in `ai/config/execution.yaml` (`rooms`: area-ID, Schlüssel, Entity-ID der `heating_control`-Automation). **Prüfe, dass die Entity-IDs der Automationen stimmen**, sonst schlägt `init` fehl. Die Datei ist im Image, nach einer Änderung neu bauen und deployen.

```bash
docker exec home-ai-execution python -m execution init
```

`init` legt den Sammelschalter „alle Regeln in Schatten“ an, je Raum die Helfer für den Mindestabstand und die beiden Hilfsautomationen. Es nennt dir je Raum den Helfer `input_boolean.…`. **Diesen trägst du in der jeweiligen `heating_control`-Instanz** unter „KI-Regeln erlaubt (Schalter)“ ein und speicherst. Danach:

```bash
docker exec home-ai-execution python -m execution check
```

`check` meldet je Prüfpunkt `OK` oder `FEHLT` (Blueprint, Sammelschalter, Helfer, Hilfsautomationen). Erst wenn alles ok ist, weiter.

```bash
docker exec home-ai-collector python -m collector origins --rebuild
```

Das rechnet die Herkunft der bisherigen Änderungen aus den aufgezeichneten Dienstaufrufen nach (neue werden laufend berechnet). Eingriffe aus der Zeit vor der Aufzeichnung der Dienstaufrufe haben keine Herkunft.

## 6. Dashboard
Es muss nicht von Hand gebaut werden. Der Dienst legt das Dashboard `ki-regeln` („KI-Regeln“) beim Start (sobald er mit dem Broker verbunden ist) an und aktualisiert es bei jedem `deploy`/`undeploy`. Vor der ersten Regel ist es fast leer. Die Seite „System“ hat die Knöpfe für Status, Prüfungen, Signale, Scan und Analyse.

Erscheint es nicht in der Seitenleiste: Seite neu laden, in HA unter Einstellungen → Dashboards prüfen und im Log des Dienstes nach einem Fehler beim Speichern suchen.

## 7. Erste Regel im Schattenmodus
Vorher müssen Analyse oder Scan Kandidaten mit Status `bestanden` geliefert haben (Ablauf in der README, Abschnitte zur Analyse). Dann:

```bash
docker exec home-ai-collector python -m analysis list --status bestanden
docker exec home-ai-execution python -m execution deploy <id>
```

(oder über den Knopf im Dashboard). Die Regel startet im **Schatten**: Sie meldet nur, was sie getan hätte. Den Schalter „aktiv“ der Regel legst du erst um, wenn die Karte „grün“ zeigt (mindestens 10 gewertete Auslösungen, unter 20 % Fehl- bzw. Korrekturquote) und du den Verlauf geprüft hast. Der Sammelschalter „alle Regeln in Schatten“ ist der Not-Aus und wirkt ohne den Container.

## 8. Abnahme in der echten HA
Nur dort prüfbar; die Checkliste steht im Plan unter Aufgabe 19 (`docs/superpowers/plans/2026-10-15-home-ai-execution.md`). Wichtigste Punkte:
- Der Schalter „aktiv“ behält seinen Zustand nach einem HA-Neustart.
- Entfernen und erneutes Anlegen einer Regel bei eingeschaltetem Schalter: die neue Regel startet im Schatten.
- Container stoppen: aktive Regeln und `heating_control` laufen weiter, das Dashboard zeigt „Container offline“.
- Preset und Sollwert im Modus `off` (Wechsel `Away` → `Coming Home`).
- Die Aufrufe von Zeitplan-Flanke und der Umschaltung um 01:00 erscheinen ohne Benutzerkennung und mit derselben Kontext-ID wie die Zustandszeile.

## Zurücknehmen
- **Eine Regel:** `docker exec home-ai-execution python -m execution undeploy r<id>` (Automation und Entitäten werden entfernt, die Historie bleibt in der Datenbank).
- **Alles anhalten:** Sammelschalter „alle Regeln in Schatten“ in HA einschalten. Das geht ohne Container.
- **Dienst abschalten:** Container `home-ai-execution` stoppen. Der Collector läuft unabhängig weiter. Aktive Regeln laufen in HA weiter, bis du sie entfernst oder in den Schatten schaltest.
- **`heating_control`:** Die Eingabe „KI-Regeln erlaubt“ leer lassen; ohne sie schaltet keine KI-Regel dieses Raums.

## Fehlersuche
| Symptom | Prüfen |
|---|---|
| `init` meldet Automation nicht lesbar | Entity-ID in `execution.yaml` (`heating_automation`) und dass die Automation aus dem Blueprint `heating_control` stammt |
| `check`: Blueprint fehlt | Pfad `/config/blueprints/automation/fabsss/ki_regel.yaml`, Blueprints neu geladen |
| Dashboard-Knöpfe ohne Wirkung | Log `home-ai-execution`; MQTT-Zugang; Status `homeai/status` im MQTT-Explorer |
| Keine Herkunft in der Datenbank | Log des Collectors auf „nicht abonnierbar“; `HA_TOKEN` braucht Administratorrolle |
| Regel schaltet nicht | Raumschalter, Regelschalter, Sammelschalter, Wohnungszustand `Vacation`, letzter Eingriff unter 60 Minuten (die Karte zeigt den Grund) |
