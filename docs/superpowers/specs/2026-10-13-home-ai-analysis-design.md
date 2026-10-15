# KI-Analyse: Regelkandidaten aus dem Heizverhalten – Teilprojekt 2

Stand: 2026-10-13 · Status: Entwurf freigegeben

## 1. Einordnung

Teilprojekt 2 baut auf der Datenbasis (Teilprojekt 1, `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md`) auf. Es liest `home_ai.sqlite` und erzeugt aus dem Verhalten der Bewohner **Regelkandidaten** für die Heizung. Kandidaten sind Vorschläge. Sie werden weder ausgeführt noch freigegeben; das ist Teilprojekt 3.

| # | Teilprojekt | Inhalt |
|---|-------------|--------|
| 1 | Datenbasis | Langzeit-Datenspeicher, Collector, Backfill (umgesetzt) |
| 2 | **Analyse** (dieses Dokument) | Verdichtung, LLM-Hypothesen über OpenRouter (ZDR), Backtest |
| 3 | Laufzeit | Regel-Blueprint, Freigabe-Dashboard, Schattenmodus, Auto-Modus |

Grundprinzip bleibt: *Ein LLM schlägt vor, die Daten prüfen, du gibst frei, Home Assistant führt deterministisch aus.* Das Modell ist nie in der Echtzeitschleife, und alle Zahlen an einem Kandidaten stammen aus dem Backtest, nie vom Modell.

## 2. Ziel und Erfolgskriterien

**Ziel:** Ein Kommando erzeugt aus den manuellen Eingriffen und dem Kontext Regelkandidaten, prüft sie gegen die Historie und legt sie samt Kennzahlen in der Datenbank ab. Zwei Verfahren sind umschaltbar, damit sich ihr Nutzen vergleichen lässt.

**Erfolgskriterien:**
1. `analysis run --mode weekly` und `analysis run --mode clusters` laufen auf der echten Datenbank durch und speichern Kandidaten samt Backtest-Ergebnis.
2. Der an das LLM gesendete Text enthält keinen Klarnamen einer Person (Test und `--dry-run`).
3. Der Backtest ist ohne LLM reproduzierbar: Derselbe Kandidat auf denselben Daten ergibt dieselben Kennzahlen.
4. `analysis compare` stellt Läufe beider Modi gegenüber (Zahl der Kandidaten, davon bestanden, Überlappung, Kosten).
5. Zero Data Retention wird bei jedem Aufruf erzwungen; ohne passenden Anbieter bricht der Lauf ab.
6. Eine in den Testdaten versteckte, bekannte Regel wird vom Backtest mit voller Abdeckung bewertet.

**Nicht Teil dieses Teilprojekts:** Freigabe, Dashboard, Ausführung der Regeln, Auto-Modus (Teil 3); Beleuchtung; Komfort- und Energiekennzahlen (das Bewertungsformat lässt sie zu, sie werden hier aber nicht berechnet).

## 3. Entscheidungen

| Thema | Entscheidung |
|-------|--------------|
| Ergebnis | Maschinenlesbare Regelkandidaten in der Datenbank; kein Bericht |
| Kandidatentypen | Zeit- und Ereignisauslöser in einem Format |
| Bewertung | Abdeckung und Fehlauslösungen mit Mindestzahl an Belegen; Format für Komfort und Energie später erweiterbar |
| Sichtbarkeit für das LLM | Personen pseudonym (`person_1`, `person_2`), Räume im Klartext, verdichtete Muster statt Rohereignisse |
| Betrieb | Kommando von Hand (`python -m analysis …`), so gebaut, dass ein Zeitplan es später aufrufen kann |
| Verfahren | Zwei umschaltbare Modi: `weekly` (LLM zuerst) und `clusters` (Hybrid); Backtest und Speicherung sind identisch |
| Zeitzone | Wochentage und Uhrzeiten in lokaler Zeit (Europe/Berlin), gespeichert wird weiter UTC |
| Ort | Paket `ai/analysis/` neben `collector/`; der Collector bleibt unverändert |
| LLM-Zugang | OpenRouter, ZDR erzwungen; Schlüssel nur als Umgebungsvariable `OPENROUTER_API_KEY` |

## 4. Architektur

```
home_ai.sqlite (Teil 1)
  state_changes, manual_actions, room_map, users, entities
        │
        ▼
 1 Verdichtung ── Modus weekly: Raster Raum × Wochentag × Stunde
                  Modus clusters: Eingriffs-Cluster mit Kontext
        │
        ▼
 2 Pseudonymisierung (lokal)  ──  Tabelle pseudonyms
        │
        ▼
 3 LLM-Aufruf (OpenRouter, ZDR)  ──▶ Kandidaten als JSON
        │
        ▼
 4 Validierung gegen das Vokabular
        │
        ▼
 5 Backtest (ohne LLM)
        │
        ▼
 6 Speichern: analysis_runs, candidates, backtest_results
```

Stufe 1 und 3 (Inhalt des Prompts) unterscheiden sich je Modus. Stufen 2 und 4 bis 6 sind für beide Modi identisch, damit der Vergleich fair ist.

## 5. Verdichtung

Beide Modi lesen denselben Zeitraum (Standard: alle vorhandenen Daten, einstellbar mit `--from` und `--to`) und wenden dieselben Ausschlüsse an. Ausschlüsse stehen in `analysis.yaml` (Raum, von, bis). Vorbelegt ist das Schlafzimmer ab 2026-03-16 bis zum Tausch der Thermostate, weil dort keine echte Regelung lief (Spezifikation Teil 1, 14.3).

**Eingriff.** Ein Eingriff ist eine Zeile der View `manual_actions` mit `actor_class = 'manual'`. Mehrere Zeilen derselben Entität innerhalb von 60 Sekunden zählen als ein Eingriff. Nur Eingriffe mit Benutzerkontext sind Lernsignal (Teil 1, 14.2).

**Modus `weekly`.** Raster je Raum, Wochentag und Stunde (7 × 24). Mitgeschickt werden nur Zellen mit Eingriffen: Anzahl, häufigster neuer Preset oder Sollwert, Anwesenheitsanteil. Dazu eine Zusammenfassung des Wohnungszustands je Wochentag. Das Modell findet Muster selbst.

**Modus `clusters`.** Ein deterministischer Schritt bündelt Eingriffe nach Raum, Aktion (neuer Preset oder Sollwertbereich) und Uhrzeit (±45 Minuten, Wochentagsgruppe). Ein Cluster hat mindestens drei Eingriffe. Jeder Cluster trägt den Kontext zum Zeitpunkt der Eingriffe als Anteile, zum Beispiel „12 von 14: `person_2` zu Hause, Wohnungszustand X, Fenster geschlossen, Außentemperatur unter 8 °C“. Das Modell sieht Cluster, keine Rohzeilen, und formuliert die Hypothese über die Ursache.

**Kontextquellen** (alle bereits mit dem Label aufgezeichnet): `person.*` (zu Hause oder nicht), `input_select.flat_occupation_state`, `input_boolean.someone_in_bed`, `binary_sensor.office_day_template`, Präsenzsensoren, Fensterkontakte, `sensor.environment_sensor_outside_temperature`, `weather.home`. Der Handy-Raumsensor geht nur als Anwesenheitsmerkmal ein, nicht als Bewegungsprofil.

**Pseudonymisierung.** Personen werden stabil nach sortierter Entity-ID zu `person_1`, `person_2` und über die Tabelle `users` auch den Benutzern zugeordnet. Die Zuordnung liegt in der Tabelle `pseudonyms`. Räume bleiben im Klartext. Die Rückübersetzung geschieht lokal beim Einlesen der Kandidaten.

## 6. Kandidatenformat

Das Modell antwortet mit einem JSON-Schema. Höchstens 20 Kandidaten je Lauf.

```
Kandidat:
  raum:        Raumname aus room_map
  ausloeser:   Zeit | Ereignis
  bedingungen: optional, Liste aus dem Vokabular
  aktion:      Preset | Sollwert
  hypothese:   kurzer Text, ohne Einfluss auf die Berechnung
```

**Vokabular (geschlossen).** Der Backtest muss jeden Auslöser selbst aus den Daten berechnen können. Deshalb sind nur diese Formen zulässig:

- **Zeit:** Wochentage und Uhrzeit (lokale Zeit).
- **Ereignis:** `person_n kommt heim`, `person_n geht`, Wohnungszustand wechselt zu einem Wert, `jemand legt sich ins Bett`, `jemand steht auf`, Fenster im Raum wird geschlossen, Büro-Tag beginnt.
- **Bedingungen:** Wochentage, Zeitfenster, Außentemperatur unter oder über einem Wert, Person zu Hause oder nicht.
- **Aktion:** ein Preset, der in den Daten vorkommt, oder ein Sollwert in °C.

Etwas anderes ergibt den Status `nicht_pruefbar`. Der Kandidat wird gespeichert und nicht verworfen, damit sichtbar bleibt, was dem System fehlt.

## 7. Backtest

Der Backtest läuft ohne LLM. Für jeden Tag, an dem der Auslöser samt Bedingungen zutrifft, wird im Fenster danach (Standard 30 Minuten) nach einem passenden manuellen Eingriff im Raum gesucht. Alle Parameter stehen in `analysis.yaml`.

| Kennzahl | Bedeutung |
|----------|-----------|
| `abdeckung` | Anteil der Eingriffe dieser Art (Raum, gleiche Aktion), die die Regel vorweggenommen hätte |
| `fehlausloesungen` | Anteil der **wirksamen** Auslösungen, auf die kein ähnlicher Eingriff folgt oder bei denen innerhalb einer Stunde das Gegenteil eingestellt wird |
| `neutral` | Auslösungen, die nichts verändert hätten: Heizung aus (alle Thermostate des Raums) oder der Zielwert gilt bereits. Sie zählen weder als Treffer noch als Fehler |
| `wirksam` | Auslösungen ohne die neutralen (`ausloesungen` − `neutral`); Nenner der Fehlauslösungen |
| `faelle` | Anzahl passender Auslösungen mit Eingriff |
| `wochen` | Anzahl verschiedener Kalenderwochen, in denen Fälle vorkamen |

**Wirksame Auslösungen und aktive Zeiträume (Änderung nach dem ersten Lauf auf echten Daten).** Die erste Fassung zählte jede Auslösung ohne Eingriff als Fehler, auch im Sommer und an Tagen, an denen der Zeitplan den Zielwert ohnehin schon gesetzt hatte. Bei seltenen Eingriffen lagen die Fehlauslösungen dadurch bei 0,93 bis 1,00 und kein Kandidat konnte bestehen. Jetzt gilt:

1. **Neutral:** Zum Auslösezeitpunkt wird der tatsächliche Zustand der Thermostate des Raums aus `state_changes` gelesen (Zeitplan, Automationen und Eingriffe zusammen). Ist die Heizung aus oder der Sollwert (Toleranz `setpoint_tolerance`) beziehungsweise das Preset schon der Zielwert, ist die Auslösung neutral. Fehlen Daten zum Thermostat oder ist der Sollwert unbekannt, gilt sie als wirksam (altes Verhalten). Ein Treffer geht immer vor.
2. **Aktive Zeiträume:** Ausgewertet werden nur Tage in der Nähe von Eingriffen im Raum. Pausen von mehr als `active_gap_days` (Vorschlag 21) trennen Zeiträume, der Rand beträgt einen lokalen Tag. So zählt ein Sommer ohne Eingriffe nicht gegen die Regel. `active_gap_days: 0` schaltet das ab; Räume ohne Eingriffe werden nicht gefiltert.

**Status eines Kandidaten:** `bestanden` (genug Belege, Abdeckung und Fehlauslösungen innerhalb der Schwellen), `zu_wenig_belege` (unter fünf Fälle oder unter drei Wochen), `abgelehnt` (Belege genug, Schwellen verfehlt), `nicht_pruefbar`. Die Schwellen sind Vorschläge und liegen in `analysis.yaml`.

Weitere Kennzahlen (Komfort, Energie) werden als zusätzliche Zeilen in `backtest_results` ergänzt; das Format ändert sich dafür nicht.

## 8. Datenmodell

Neue Tabellen in `home_ai.sqlite`, angelegt durch ein eigenes Schema `ai/sql/analysis.sql`:

```sql
analysis_runs(id, mode, model, started_at, finished_at, params, status,
              prompt_hash, prompt_text, response_text, tokens_in, tokens_out, cost_usd)
candidates(id, run_id, room, trigger, conditions, action, hypothesis, status)
backtest_results(candidate_id, metric, value)
pseudonyms(alias, entity_id, kind)
```

`trigger`, `conditions` und `action` sind JSON. Prompt und Antwort werden pseudonymisiert gespeichert, damit ein Lauf nachvollziehbar bleibt.

**Umsetzung, Abweichungen:** Die Spalten heißen `trigger_json`, `conditions_json` und `action_json` (`TRIGGER` ist ein SQL-Schlüsselwort). Zusätzlich gibt es `candidates.note` (Grund für `nicht_pruefbar`, `zu_wenig_belege`, `abgelehnt`) und `analysis_runs.error`. Räume stehen in der Datenbank als `area_id`; die Klartextnamen für das Modell kommen aus `room_names` in `analysis.yaml`.

## 9. Kommandos und Konfiguration

| Kommando | Wirkung |
|----------|---------|
| `analysis run --mode weekly\|clusters [--from TS] [--to TS] [--dry-run]` | Ein Lauf; `--dry-run` zeigt den zu sendenden Text, ruft nichts auf |
| `analysis compare [RUN_ID …]` | Gegenüberstellung von Läufen: Kandidaten, bestanden, Überlappung, Kosten |
| `analysis list [--status bestanden] [--run RUN_ID]` | Kandidaten anzeigen |
| `analysis backtest CANDIDATE_ID` | Backtest eines gespeicherten Kandidaten erneut rechnen |

`ai/config/analysis.yaml` enthält Modell, Ausschlüsse, Fenster und Schwellen des Backtests, Mindestzahl Belege, Clusterparameter, Kostenlimit je Lauf (Vorschlag 2 USD) und Wiederholungen. Die Datei wird wie `collector.yaml` ins Image eingebaut.

## 10. Fehlerbehandlung

- **Ungültige Antwort:** Ist die Antwort kein gültiges JSON oder passt nicht zum Schema, folgt ein einziger Wiederholungsversuch mit der Fehlermeldung. Danach gilt der Lauf als fehlgeschlagen.
- **Netz:** Zeitüberschreitung oder Verbindungsfehler führen zu Wiederholungen mit Wartezeit, danach Abbruch mit klarer Meldung.
- **Atomar:** Ein Lauf schreibt in einer Transaktion. Bei Fehler bleibt nichts Halbes zurück; der Lauf wird als `fehlgeschlagen` vermerkt.
- **Zu wenig Daten:** Gibt es im Zeitraum weniger als die konfigurierte Mindestzahl an Eingriffen, endet das Kommando mit Meldung, ohne Aufruf.
- **ZDR:** Findet sich kein Anbieter mit Zero Data Retention, bricht der Lauf ab, statt ohne ZDR zu senden. Die genauen Parameter werden beim Planen gegen die aktuelle OpenRouter-Dokumentation geprüft.
- **Kostenlimit:** Überschreitet die geschätzte Größe das Limit, bricht der Lauf vor dem Aufruf ab.
- **Unbekannte Räume und Auslöser:** Status `nicht_pruefbar`, kein Abbruch.
- **Zeitzone:** Wochentage und Uhrzeiten werden in lokaler Zeit berechnet; die Umstellung der Sommerzeit verschiebt Muster nicht.

## 11. Tests

- **Verdichtung** gegen eine kleine Fixture-Datenbank mit bekannten Eingriffen, für beide Modi, einschließlich Ausschlüssen und Zusammenfassen von Eingriffen innerhalb von 60 Sekunden.
- **Backtest** mit synthetischen Eingriffen: Eine passende Regel erreicht volle Abdeckung (Erfolgskriterium 6), eine unpassende zeigt viele Fehlauslösungen, zu wenige Fälle ergeben `zu_wenig_belege`, Auslöser an Sommerzeit-Grenzen verschieben sich nicht.
- **Pseudonymisierung:** Rückübersetzung stimmt, und im gesendeten Text steht kein Klarname einer Person.
- **LLM-Client** gegen eine Fake-Schnittstelle wie `FakeHass`: Schemafehler, einmalige Wiederholung, Kostenlimit, ZDR-Pflicht, Netzfehler.
- **Vokabular:** Ein unbekannter Auslöser ergibt `nicht_pruefbar`.
- **Modi:** Bei identischer Antwort des Modells liefern beide Modi dasselbe Backtest-Ergebnis.
- **Rauchtest** gegen die echte Datenbank nach dem Backfill, nur zur Plausibilität.

## 12. Risiken und offene Punkte

- **Wenig Wiederholung:** Mit einer Heizsaison fehlt saisonale Wiederholung. Ein Muster, das nur im Dezember vorkam, lässt sich nicht gegen den Frühling prüfen. Das Backtest-Ergebnis ist ein Indiz, kein Beweis.
- **Sollwert und Preset gemeinsam:** Better Thermostat ändert beide zusammen. Die Zusammenfassung innerhalb von 60 Sekunden muss das als einen Eingriff zählen, sonst werden Cluster doppelt gezählt.
- **Datenlücken:** Ohne den zweiten Snapshot fehlen Oktober bis Dezember 2025. Die Analyse läuft auf dem, was da ist; der Zeitraum steht im Lauf (`params`).
- **Qualität des Lernsignals:** Eingriffe aus Lücken der Aufzeichnung tragen keinen Benutzerkontext und zählen nicht als manuell. Dadurch kann das Signal unvollständig sein.
- **Vergleich der Modi:** Mit wenigen Läufen und einem nicht deterministischen Modell ist der Unterschied zwischen `weekly` und `clusters` ein Eindruck, keine Statistik. `compare` zeigt deshalb die Rohzahlen.
- **Kandidaten sind nur Vorschläge:** Nichts im Haus ändert sich durch Teil 2.

## 13. Verzeichnisstruktur (Vorschlag)

```
ai/
  analysis/
    __init__.py
    __main__.py
    features.py        # Verdichtung beider Modi, Eingriffe, Kontext
    pseudonym.py       # Zuordnung Person -> Alias
    vocabulary.py      # geschlossenes Vokabular, Prüfung von Kandidaten
    llm.py             # OpenRouter-Client, ZDR, Kosten, Wiederholung
    candidates.py      # Schema, Speicherung, Rückübersetzung
    backtest.py        # Kennzahlen, Status
    cli.py             # run | compare | list | backtest
  config/
    analysis.yaml
  sql/
    analysis.sql
  tests/
    test_features.py, test_backtest.py, test_pseudonym.py,
    test_llm.py, test_vocabulary.py, test_analysis_cli.py
```
