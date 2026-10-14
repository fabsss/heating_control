# KI-Analyse, Erweiterung: allgemeine Signale und automatische Voranalyse

Stand: 2026-10-14 · Status: Entwurf zur Freigabe

## 1. Anlass

Der erste Lauf der Analyse (Teilprojekt 2) auf den echten Daten hat gezeigt, dass das Verfahren zu eng ist:

- Das Modell sieht nur Merkmale, die wir vorher ausgewählt haben (Anwesenheit, Wohnungszustand, Bett, Fenster, Außentemperatur) und nur als Zustand im Moment des Eingriffs. Zeitabstände zu Ereignissen und Vergleichswerte (wie oft ist das ohnehin so?) fehlen.
- Der Bericht `analysis presence` hat von Hand gezeigt, was darin steckt: Nach der Heimkehr von einem längeren Weg folgt rund dreimal so oft ein Eingriff wie zur selben Uhrzeit an anderen Tagen. Das Modell konnte das nicht erkennen, und das Vokabular konnte es nicht ausdrücken.
- Die Auslöser sind auf sieben feste Formen beschränkt. Neue Signale (zum Beispiel die Wecker) wären ohne Codeänderung nicht nutzbar.

Diese Erweiterung ersetzt die Handarbeit durch zwei allgemeine Bausteine: eine **automatische Voranalyse**, die alle aufgezeichneten Signale gleich behandelt, und ein **allgemeines Vokabular**, mit dem jedes Ergebnis ausdrückbar und prüfbar ist. Das Modell bekommt die Ergebnisse der Voranalyse und formuliert daraus Regeln und Hypothesen.

Grundprinzip bleibt: *Ein LLM schlägt vor, die Daten prüfen, du gibst frei, Home Assistant führt deterministisch aus.* Der Backtest ist weiter der Prüfer. Nichts im Haus ändert sich.

## 2. Ziel und Erfolgskriterien

**Ziel:** Jedes Signal mit dem Label `KI_DB_Sync` wird ohne Codeänderung auf Zusammenhänge mit manuellen Eingriffen geprüft. Das Modell kann jedes dieser Signale in Regeln verwenden.

**Erfolgskriterien:**
1. `analysis scan` läuft auf der echten Datenbank durch und listet geordnete Assoziationen. Die Heimkehr nach langer Abwesenheit (der Befund aus `presence`) steht **ohne jede Vorgabe** unter den obersten Treffern für das Wohnzimmer.
2. Auf den Testdaten findet der Scan die versteckte Gewohnheit (Heimkehr, Eingriff nach 12 Minuten) als stärkste Assoziation, auf Zufallsdaten findet er nichts Signifikantes (Fehlerrate kontrolliert).
3. Ein Wecker-Signal wird ohne Sonderbehandlung im Code gefunden, wenn Eingriffe regelmäßig nach der Weckzeit folgen (Test mit Wecker-Fixture).
4. `analysis run --mode scan` sendet Signalkatalog und Assoziationen an das Modell, ohne Klarnamen, Entity-IDs und Benutzer-IDs. `--dry-run` zeigt den Text.
5. Kandidaten mit allgemeinen Auslösern und Bedingungen (beliebiges Signal, Versatz, Dauer des vorherigen Zustands) werden vom Backtest berechnet. Alte Kandidaten bleiben prüfbar.
6. Alle bisherigen Kriterien von Teilprojekt 2 gelten weiter; `weekly` und `clusters` laufen unverändert mit dem bisherigen Vokabular. Ihre Kandidaten und die des Modus `scan` stehen in derselben Tabelle und werden von demselben Backtest berechnet.

**Nicht Teil dieser Erweiterung:** Kombinationen mehrerer Signale per Entscheidungsbaum, ein Werkzeug-Modus, in dem das Modell selbst abfragt (spätere Schritte); Freigabe und Ausführung (Teilprojekt 3); Beleuchtung; Kausalnachweis (siehe Abschnitt 12).

## 3. Entscheidungen

| Thema | Entscheidung |
|-------|--------------|
| Voranalyse | Deterministisch und ohne LLM: Ereignisse je Signal, Vergleich mit einer Kontrollgruppe (dieselbe Uhrzeit an anderen Tagen), Signifikanz mit Korrektur für viele Tests |
| Rolle des Modells | Deutet die Assoziationen, kombiniert sie zu Regeln, nennt mögliche Ursachen; erfindet keine Zahlen |
| Signale | Jede aufgezeichnete Entität außer den Thermostaten selbst; Zustandsart (Kategorie, Zahl, Zeitstempel) wird aus den Daten erkannt |
| Vokabular | Allgemein: Auslöser und Bedingungen beziehen sich auf pseudonyme Signal-Kennungen. Die alten festen Formen bleiben prüfbar |
| Modi | `weekly`, `clusters` (unverändert, bisheriges Vokabular) und neu `scan` (allgemeines Vokabular); Backtest und Speicherung identisch, so bleiben alle drei vergleichbar |
| Datenschutz | Signale heißen `sig_01`, `sig_02` …; Beschreibungen kommen aus Art, Raum und einer Konfiguration, nie aus Entity-IDs oder Namen; Zonen von Personen werden zu `home` und `away` zusammengefasst |
| Betrieb | Kommando von Hand wie bisher |

## 4. Signale

Ein Signal ist eine Entität mit dem Label `KI_DB_Sync` und `kind` ungleich `climate` (die Thermostate sind das Ziel, nicht der Auslöser). Jedes Signal hat eine **Art**, die aus den gespeicherten Zuständen bestimmt wird:

| Art | Erkennung | Werte |
|-----|-----------|-------|
| `categorical` | höchstens zwölf verschiedene Zustände, jeder mindestens `min_value_count`-mal | die Zustände; seltene Werte werden zu `other` |
| `numeric` | die Zustände sind (fast) alle Zahlen | Schwellen an den Terzilen der beobachteten Werte |
| `timestamp` | die Zustände sind ISO-Zeitstempel (zum Beispiel `device_class: timestamp`) | Zeitpunkte |

**Kennungen.** Jedes Signal bekommt beim ersten Auftreten eine Kennung `sig_NN`, die in der Tabelle `signals` dauerhaft gespeichert und nie neu vergeben wird (neue Entitäten bekommen die nächste freie Nummer). Kandidaten verweisen mit der Kennung auf ein Signal.

**Beschreibung für das Modell.** Sie entsteht aus `kind`, `device_class`, Raum und der Konfiguration, nie aus der Entity-ID oder dem Namen:
- Personen: `Anwesenheit person_1`; alle Zonen außer `home` heißen `away`.
- Die vier Einträge unter `context:` in `analysis.yaml` bekommen feste Namen (Wohnungszustand, jemand im Bett, Bürotag, Außentemperatur).
- Alles Weitere: Art und Raum (zum Beispiel `Kontakt, Bad`); eigene Beschreibungen stehen in `signal_labels:` in `analysis.yaml` (zum Beispiel `sensor.pixel_9a_next_alarm: "Weckzeit (nächster Wecker)"`).
- Jede Beschreibung und jeder Wert läuft vor dem Senden durch die Prüfung auf Klarnamen. Ein Treffer bricht den Lauf ab.

## 5. Ereignisse

Aus jedem Signal entstehen automatisch Ereignisse mit Zeitpunkten:

- **`to_state`** (kategorisch): Wechsel in einen Wert aus einem bekannten Zustand. Zusätzlich je **Dauer des vorherigen Zustands**: die Klassen aus `duration_edges` (Vorschlag unter 45, 45 bis 180, ab 180 Minuten) und die Klasse „alle“. Klassen mit weniger als `min_events` Ereignissen entfallen. (Gassi, Einkauf und Arbeit sind so ohne Sonderlogik trennbar.)
- **`rises_above` / `falls_below`** (numerisch): Überschreiten beziehungsweise Unterschreiten der Terzil-Schwellen, höchstens einmal je 60 Minuten.
- **`alarm_time`** (Zeitstempel): Ein beobachteter Zeitstempel T zählt als Ereignis zum Zeitpunkt T, wenn der Sensor kurz davor (eine Minute) noch auf T stand, der Wecker also nicht gelöscht oder verschoben wurde. Das Ereignis ist im Voraus bekannt und erlaubt deshalb Versätze davor.

## 6. Assoziationsscan

Der Scan prüft für jedes Ereignis, jeden Raum und jeden Zeitabschnitt, ob in der Nähe des Ereignisses auffällig oft ein manueller Eingriff liegt.

- **Eingriffe** sind wie bisher die zusammengeführten manuellen Eingriffe (Sollwert oder Preset). Räume mit weniger als `min_room_interventions` Eingriffen entfallen; zusätzlich gibt es „alle Räume“.
- **Abschnitte:** 30 Minuten breit von zwei Stunden davor bis zwei Stunden danach. Ein Ereignis zählt als Treffer, wenn im Abschnitt mindestens ein Eingriff liegt.
- **Kontrolle:** dieselbe lokale Uhrzeit an den sieben Tagen davor und danach, ohne Ereignis derselben Art im Umkreis von zwei Stunden. Das trennt „Ereignis wirkt“ von „abends wird ohnehin viel verstellt“.
- **Wertung:** einseitiger exakter Test nach Fisher (Anteil der Treffer gegen Anteil der Kontrolle), Benjamini-Hochberg-Korrektur über alle Tests eines Laufs (`q`); `lift` ist das Verhältnis der Anteile. Aufgenommen wird, was mindestens `min_hits` Treffer und `q ≤ q_max` hat. Benachbarte Abschnitte desselben Ereignisses werden zu einem Fenster zusammengefasst.
- **Ausgewertet** werden nur Tage mit Eingriffen (± ein Tag), damit der Sommer nichts verdünnt.
- **Typische Aktion:** je Treffer-Fenster der häufigste neue Sollwert beziehungsweise das häufigste Preset samt Häufigkeit.

Ergebnisse stehen in der Tabelle `associations`. `analysis scan` zeigt sie lokal mit echten Namen; an das Modell gehen nur die Kennungen.

Die Korrektur gilt der Zahl der Tests, nicht den Abhängigkeiten zwischen Ereignissen (mehrere Ereignisse am selben Tag). `q` ist deshalb ein Rangmaß, kein Beweis (Abschnitt 12).

## 7. Modus `scan` (Daten für das Modell)

`analysis run --mode scan` berechnet die Assoziationen und sendet dem Modell:
1. den **Signalkatalog**: Kennung, Beschreibung, Art, Werte (bei Zahlen Minimum, Terzile und Maximum);
2. die obersten `top_k` **Assoziationen** (Signal, Ereignis mit Dauerklasse, Raum, Fenster, Zahl der Ereignisse und Treffer, Kontrollanteil, `lift`, `q`, typische Aktion);
3. das **Wochenraster** des Modus `weekly`, damit reine Zeitgewohnheiten nicht verloren gehen.

Der Systemprompt verlangt, dass jede Regel durch mindestens eine Assoziation oder das Raster belegt ist, höchstens zwei Bedingungen hat und eine Hypothese zur Ursache nennt. Zahlen und Trefferquoten gehören nicht in die Antwort. Der Backtest prüft jeden Kandidaten wie bisher. `weekly` und `clusters` bleiben unverändert (Daten, Prompt und Vokabular) und liefern den Vergleich.

## 8. Allgemeines Vokabular

Auslöser (genau einer):

| Form | Felder |
|------|--------|
| Zeit | `{"type":"time","weekdays":[0..6],"time":"HH:MM"}` (unverändert) |
| Signal, Zustandswechsel | `{"type":"signal","signal":"sig_03","event":"to_state","value":"home","min_prev_min":180,"max_prev_min":null,"offset_min":0}` |
| Signal, Schwelle | `{"type":"signal","signal":"sig_07","event":"rises_above"\|"falls_below","value":18.5,"offset_min":0}` |
| Signal, Weckzeit | `{"type":"signal","signal":"sig_09","event":"alarm_time","offset_min":-30}` |

`offset_min` liegt zwischen −120 und 120 Minuten; negative Werte sind nur bei `alarm_time` erlaubt, weil nur dort das Ereignis im Voraus bekannt ist. `min_prev_min` und `max_prev_min` sind optional.

Bedingungen (bis zu zwei): `weekdays` und `time_window` (unverändert), neu `{"type":"signal_state","signal":…,"op":"is"|"is_not","value":…}` und `{"type":"signal_range","signal":…,"op":"below"|"above","value":…}`. Aktionen unverändert (`preset` oder `setpoint`).

**Prüfung.** Unbekannte Kennung, Wert außerhalb des bekannten Wertevorrats, Schwelle außerhalb des beobachteten Bereichs, unzulässiger Versatz oder Signale ohne Daten ergeben `nicht_pruefbar` mit Grund, nie einen Abbruch. **Alte Formen** (`event` mit `person_arrives` usw., `outside_temp`, `person_home`) bleiben gültig, damit gespeicherte Kandidaten neu gerechnet werden können; neue Läufe verwenden nur die allgemeinen Formen.

**Backtest.** Die Zeitpunkte eines Signal-Auslösers kommen aus derselben Ereignisberechnung wie im Scan (Abschnitt 5), plus Versatz. Alles Weitere (Fenster, Fehlauslösungen, neutrale Auslösungen, aktive Zeiträume, Kennzahlen, Status) bleibt wie in Abschnitt 7 der Spezifikation von Teilprojekt 2.

## 9. Datenmodell und Konfiguration

Neue Tabellen in `home_ai.sqlite`, angelegt aus `ai/sql/analysis.sql`:

```sql
signals(handle PRIMARY KEY, entity_id UNIQUE, kind, value_kind, description)
associations(run_id, signal, event, prev_class, room, win_start, win_end,
             events, hits, control_events, control_hits, lift, p, q, typical_action)
```

`analysis.yaml` bekommt die Abschnitte `signal_labels:` (Beschreibungen) und `scan:` mit `min_events`, `min_hits`, `q_max`, `top_k`, `min_room_interventions`, `duration_edges`, `control_days`, `max_values`, `min_value_count`.

## 10. Kommandos

| Kommando | Wirkung |
|----------|---------|
| `analysis signals` | listet Signale mit Kennung, Art, Beschreibung und lokal der Entity-ID |
| `analysis scan [--room R] [--top N]` | Assoziationsscan ohne Modell, mit Ausgabe und Speicherung als Lauf `scan` |
| `analysis run --mode scan` | wie `weekly` und `clusters`, mit den Daten aus Abschnitt 7 |
| `analysis presence` | bleibt als Detailbericht erhalten |

## 11. Wecker und weitere Signale

- `sensor.pixel_9a_next_alarm` und `sensor.sm_s901b_next_alarm` (`device_class: timestamp`) tragen seit 2026-10-14 das Label und liefern Ereignisse `alarm_time`.
- `sensor.fabians_mobile_alarmclock_triggered` und `sensor.svenjas_mobile_alarmclock_triggered` sind Template-Sensoren aus der `configuration.yaml` ohne `unique_id` und stehen deshalb nicht in der Registry. Mit einer `unique_id` je Sensor lassen sie sich labeln; der Verlauf bleibt unter derselben Entity-ID erhalten. Danach den Backfill des Snapshots wiederholen (idempotent), um Dezember bis Juni nachzuladen. Die Sensoren liefern als Kategorie `True`/`False` Ereignisse `to_state True`.
- Die Automation „Flat State Machine“ verwendet die Wecker bereits; ihre Wirkung steckt zum Teil im Wohnungszustand. Der Scan zeigt, ob die Wecker darüber hinaus etwas erklären.

## 12. Fehlerbehandlung, Tests und Risiken

**Fehlerbehandlung.** Signale ohne Daten, ohne bekannte Werte oder ohne Ereignisse fallen im Scan still heraus und stehen in `analysis signals` mit dem Grund. Ein Signal, das nach der Klassifikation unbrauchbar ist (zum Beispiel nur ein Zustand), erzeugt keine Ereignisse. Läuft der Scan ohne Eingriffe oder ohne Signale, endet er mit einer klaren Meldung.

**Tests.**
- Ereignisse je Signalart (Dauerklassen, Terzile, Weckzeit mit gelöschtem und mit verschobenem Wecker) auf kleinen synthetischen Verläufen.
- Scan: Die versteckte Gewohnheit liegt als stärkste Assoziation vorn; Zufallsdaten ergeben bei `q_max` höchstens eine falsche Meldung in zwanzig Läufen mit festem Zufallskern; eine Wecker-Fixture findet die Weckzeit.
- Signalkennungen bleiben stabil, wenn Entitäten hinzukommen.
- Pseudonymisierung: weder Namen noch Entity-IDs noch Zonen-Namen im gesendeten Text (Signalkatalog, Assoziationen, Werte).
- Vokabular: jede neue Form gültig, jeder Unsinnswert `nicht_pruefbar`; alte Formen unverändert.
- Backtest mit Versatz; negativer Versatz nur bei `alarm_time`.
- Abnahme auf der echten Datenbank (Erfolgskriterium 1).

**Risiken.**
- **Wenig Daten:** 269 Eingriffe an 83 Tagen. Der Scan findet nur deutliche Effekte, und Räume mit wenigen Eingriffen (Bad) bleiben leer.
- **Viele Tests:** Die Korrektur senkt falsche Treffer, hebt sie aber nicht auf, und Ereignisse am selben Tag sind nicht unabhängig. Ergebnisse sind Hinweise, die der Backtest und später der Schattenmodus prüfen.
- **Zusammenhang ist keine Ursache:** Aus Beobachtungsdaten folgt keine Kausalität. Die Hypothese des Modells zur Ursache ist eine Vermutung.
- **Laufzeit:** einige hundert Ereignisarten mal Räume mal Abschnitte; die Hit-Indikatoren werden je Raum vorab berechnet. Ziel: unter einer Minute auf der echten Datenbank.
- **Datenschutz:** Beschreibungen und Werte kommen aus Konfiguration und Daten und könnten Namen enthalten; die Prüfung vor dem Senden fängt das ab und bricht den Lauf ab.

## 13. Verzeichnisstruktur (Ergänzung)

```
ai/analysis/signals.py        # Signalbibliothek: Arten, Kennungen, Beschreibungen, Zeitreihen
ai/analysis/events.py         # Ereignisse je Signal und Art
ai/analysis/scan.py           # Assoziationsscan, Kontrolle, Fisher, Benjamini-Hochberg
ai/analysis/generic.py        # allgemeines Vokabular: Parsen, Prüfen
ai/tests/test_signals.py, test_events.py, test_scan.py, test_generic.py
```
Geändert: `vocabulary.py`, `backtest.py`, `prompts.py`, `runner.py`, `cli.py`, `store.py`, `config.py`, `candidates.py`, `ai/sql/analysis.sql`, `ai/config/analysis.yaml`.
