# KI-Regeln: Freigabe und Ausführung (Teilprojekt 3)

Stand: 2026-10-15 · Status: Entwurf zur Freigabe

## 1. Anlass

Teilprojekt 1 zeichnet auf, Teilprojekt 2 findet und prüft Regeln (Status `bestanden`), geändert wird im Haus nichts. Dieses Teilprojekt schließt die Lücke: Eine geprüfte Regel soll in Home Assistant (HA) laufen können, zuerst nur beobachtend (Schattenmodus), dann schaltend, ohne dass die bestehende Heizungssteuerung (`heating_control`, Blueprint V4.28) gestört wird.

Grundprinzip bleibt: *Ein LLM schlägt vor, die Daten prüfen, du gibst frei, Home Assistant führt deterministisch aus.* Das Modell ist nie in der Schleife, die schaltet. Alles, was schaltet, liegt in HA und läuft ohne den Container weiter.

Die Messungen vom 15.10. (Bad und Büro, Lauscher auf `call_service`) haben außerdem gezeigt, dass der Kontext einer Zustandsänderung allein nicht reicht, um Mensch, Automation und Gerät zu trennen. Die Erkennung von Eingriffen (Abschnitt 5) ist deshalb Teil dieses Teilprojekts, weil Mindestabstand, Auswertung und das Lernen davon abhängen.

## 2. Ziel und Erfolgskriterien

**Ziel:** Ein Kandidat mit Status `bestanden` wird per Kommando als Automation in HA angelegt, läuft zunächst im Schattenmodus, wird auf einem Dashboard bewertet und per Schalter aktiviert. Jede Regel kann pro Heizkörper abgeschaltet werden.

**Erfolgskriterien:**
1. `execution deploy <id>` legt eine Automation in HA an, die in der Oberfläche sichtbar ist; `undeploy` entfernt Automation, Entitäten und Discovery ohne Reste.
2. Im Schattenmodus schaltet keine Regel; jede Auslösung erscheint mit Grund und Ziel in `rule_events`.
3. **Bei gestopptem Container** laufen aktive Regeln weiter, die Schalter wirken, und der Mindestabstand gilt. Ausgefallen sind nur Auswertung, Kennzahlen und das Anlegen oder Entfernen von Regeln.
4. Eine Regel schaltet nicht, wenn im selben Raum innerhalb des Mindestabstands (Standard 60 Minuten) ein Eingriff von Benutzer oder Gerät lag; Aufrufe von Automationen (auch eigene) zählen nicht als Eingriff.
5. Jede Änderung an einem Thermostat bekommt eine Herkunft (*benutzer*, *automation*, *geraet_unklar*, *unbekannt*); eigene Schaltungen und die von `heating_control` fließen nicht als Eingriffe in `manual_actions` und Analyse ein.
6. Das Dashboard `ki-regeln` zeigt je Regel Text, Schalter, Auslösungen (insgesamt und gewertet) mit Rohzahlen, Treffer- und Korrekturquote, den Zeitpunkt der Eingriffe relativ zur Regel, Ausbremsungen, Backtest daneben und die letzten Ereignisse.
7. `heating_control` bekommt nur eine optionale Eingabe (`ki_rules_toggle`, V4.29); sein Verhalten bleibt gleich. Die Prüfung auf Pseudonymisierung der Daten an das Modell bleibt unberührt.
8. **Im normalen Betrieb ist keine Shell nötig:** Status ansehen, Scan und Analyse mit Modell starten, Kandidaten ansehen, Regeln anlegen und entfernen, Schalter umlegen, Systemprüfung. Ausnahmen sind in Abschnitt 11 genannt (Backfill eines Snapshots, Änderung der Konfigurationsdateien, Update des Containers).

**Nicht Teil dieses Teilprojekts:** Bearbeiten von Regeln im Dashboard (Regel ändern heißt entfernen und neu anlegen); Komfort- und Energiekennzahlen; Beleuchtung; das nachträgliche Bereinigen alter Eingriffe ohne Kontext (siehe Abschnitt 15, eigene Aufgabe).

## 3. Entscheidungen

| Thema | Entscheidung |
|-------|--------------|
| Ausführung | Eine Automation je freigegebener Regel aus einem Blueprint `ki_regel.yaml`; angelegt über die HA-API |
| Modus | Schatten oder aktiv, Schalter je Regel (MQTT-Schalter, optimistisch, HA merkt den Zustand); zusätzlich Schalter je Heizkörper (`input_boolean`, verknüpft über die Eingabe `ki_rules_toggle` in `heating_control`) und ein Sammelschalter „alle Regeln in Schatten“. Eine Blueprint-Einstellung wie „Scheduler aktivieren“ taugt dafür nicht: Sie ist fest in der Automation gespeichert und zur Laufzeit nicht umlegbar |
| Wo liegt die Logik | Alles Schaltrelevante (Bedingungen, Schalter, Mindestabstand, Grenzen) in HA; der Container beobachtet, wertet aus und legt an |
| Anbindung Container ↔ HA | MQTT für Anzeige und Rückmeldung, HA-API (REST/WebSocket) zum Anlegen und Entfernen |
| Andocken an `heating_control` | Eine optionale Eingabe `ki_rules_toggle` je Heizkörper (V4.29), sonst unverändert. KI-Regeln schalten nur in erlaubten Wohnungszuständen und warten auf einen laufenden Durchlauf von `heating_control` (Abschnitt 6). `execution deploy` liest die Raum-Konfiguration aus der `heating_control`-Instanz |
| Bedienung | Aufträge aus dem Dashboard (Allowlist, ein Auftrag zur Zeit), damit im Normalbetrieb keine Shell nötig ist (Abschnitt 11) |
| Zeitpläne der KI | Zeit-Auslöser in der Regel selbst, kein zweiter Schedule-Helfer |
| Herkunft von Änderungen | `call_service`- und `automation_triggered`-Ereignisse zusätzlich zum Zustand (Abschnitt 5) |
| Mindestabstand | 60 Minuten, einstellbar je Regel; zählt Benutzer und Gerät, nicht Automationen |
| Fenster | keine Logik; Better Thermostat behandelt Fenster, im Bad ändert es an der Entität nichts |
| Token | eigener HA-Token für den Ausführungsdienst, getrennt vom lesenden Token des Collectors |

## 4. Aufbau

```
Collector (bestehend)           Ausführungsdienst (neu)            Home Assistant
 zeichnet Zustände auf    ──►    legt Regeln an / entfernt   ──►   Automationen (Blueprint)
 + call_service,                 Discovery, Dashboard                Helfer, MQTT-Schalter
   automation_triggered          wertet Ereignisse aus      ◄──      MQTT: fired, status
 berechnet Herkunft              stats ──► MQTT ──► Dashboard
```

- **Collector:** abonniert zusätzlich `call_service` (Domäne `climate`, nur Better-Thermostat-Entitäten) und `automation_triggered`, speichert sie und berechnet je Zustandsänderung die Herkunft.
- **Ausführungsdienst** (`execution`, eigener Prozess im selben Image, gleiche Datenbank): Kommandos zum Anlegen und Entfernen, Dauerbetrieb für MQTT (Discovery, Auswertung, Kennzahlen) und der **Auftragsläufer**, der die Aufträge des Dashboards ausführt (Scan, Analyse mit Modell, Anlegen, Prüfen). Er braucht dafür den HA-Token mit Schreibrechten und den Schlüssel für das Modell.
- **Home Assistant:** Blueprint-Automationen, Helfer, MQTT-Entitäten, Dashboard.

## 5. Herkunft von Änderungen

**Befund aus den Messungen.** Die History-Schnittstelle liefert keinen Kontext (nur `s`, `a`, `lu`, `lc`). Im Live-Strom trägt eine Änderung an der Better-Thermostat-Entität bei zehn Klicks (Button und Kachel) und zwei weiteren Klicks Benutzerkennung und dieselbe Kontext-ID wie das Ereignis `call_service`; in einem Fall (14.10.) fehlte die Kennung ohne bekannte Ursache. Das Drehen am Gerät trägt keinen Kontext; ein TRV nach Neustart oder Batteriewechsel meldet Scheinwerte (zum Beispiel 28 °C) ohne Kontext. Automationen, die durch einen Zustandswechsel ausgelöst werden, hinterlassen einen Parent, zeitgesteuerte vermutlich nicht (zu bestätigen, Abschnitt 15).

**Ereignisse.** `service_calls` (Zeit, Dienst, Ziel-Entitäten, Daten, Kontext-ID, Benutzer, Parent) und `automation_runs` (Zeit, Automation, Kontext-ID) werden vom Collector gespeichert. Ziele, die nicht als `entity_id` angegeben sind (Bereich, Gerät), werden über die Zeit zugeordnet.

**Zuordnung.** Eine Zustandsänderung an einer Better-Thermostat-Entität (Sollwert, Preset oder Modus ändert sich) wird dem Aufruf zugeordnet
1. mit gleicher Kontext-ID, sonst
2. an derselben Entität mit höchstens 1 Sekunde Abstand.

| Herkunft | Bedingung |
|----------|-----------|
| `benutzer` | zugeordneter Aufruf mit Benutzerkennung, oder Zustandszeile mit Benutzerkennung |
| `automation` | zugeordneter Aufruf ohne Benutzerkennung (mit Name der Automation, wenn `automation_triggered` dieselbe Kontext-ID hat) |
| `geraet_unklar` | kein Aufruf zugeordnet (Drehen am Gerät, Scheinwert nach Neustart des TRV); aus der Zeit mit Ereignisaufzeichnung |
| `unbekannt` | Zeitraum ohne Ereignisse und ohne Kontext (Quelle `gap`); wird nicht geraten |

Die Herkunft steht in `change_origin` und wird vom Collector nachgeführt. `manual_actions` und `interventions.py` zählen `benutzer` und `geraet_unklar` als Eingriffe, `automation` nie. Für `unbekannt` gilt vorerst das bisherige Verhalten; die Bereinigung ist eine eigene Aufgabe (Abschnitt 15).

**Grenzen.** `geraet_unklar` enthält Scheineingriffe durch schwache Batterien. Für den Mindestabstand ist das unschädlich (die Regel schaltet höchstens zu selten); für das Lernen werden sie markiert, nicht herausgerechnet.

## 6. Regel-Automation und Andocken an `heating_control`

**Eine Automation je Regel** aus `blueprints/ki_regel.yaml` (Repo; wird wie `heating_control` in HA eingespielt, `execution check` prüft Vorhandensein und Version). Der Container übersetzt den Kandidaten in die Eingaben des Blueprints; Kennungen `sig_NN` werden zu Entity-IDs, HA sieht nie ein Pseudonym.

**Eingaben:** Heizkörper (`climate`), Raumschalter, Regelschalter, Sammelschalter, Wohnungszustand (`input_select`) samt erlaubten Zuständen, die `heating_control`-Automation des Raums, Zeitstempel-Helfer des Mindestabstands, Mindestabstand in Minuten, Sollwert-Grenzen, Regel-ID, Auslöser, Bedingungen, Aktion. Heizkörper, Wohnungszustand, Raumschalter und die Automation des Raums liest `deploy` aus der `heating_control`-Instanz (`/api/config/automation/config/<id>`, nur lesend).

**Änderung an `heating_control` (V4.29).** Neue optionale Eingabe `ki_rules_toggle` (Entität, Domäne `input_boolean`, Standard leer), nach dem Muster von `scheduler_toggle`. Sie ändert das Verhalten der Automation nicht; sie ist die Zuordnung „dieser Heizkörper ↔ dieser KI-Schalter“. Bleibt sie leer, lehnt `deploy` Regeln für diesen Heizkörper ab. Den Helfer legt `execution init --room <raum>` an und nennt die Eingabe, die du einmal je Raum in `heating_control` setzt; die Automation des Benutzers wird nicht vom Programm verändert.

**Übersetzung des Vokabulars** (Teilprojekt 2, Abschnitt 8):

| Kandidat | HA |
|----------|----|
| Zeit (Wochentage, Uhrzeit) | `time`-Auslöser, Wochentage als Bedingung |
| Signal `to_state` | `state`-Auslöser; `min_prev_min` und `max_prev_min` als Bedingung auf `trigger.from_state.last_changed` |
| `rises_above` / `falls_below` | `numeric_state`-Auslöser |
| `alarm_time` mit Versatz | `time`-Auslöser auf den Zeitstempel-Sensor mit `offset` |
| Versatz > 0 bei Zustandswechsel | `delay` am Anfang der Aktion, danach Bedingungen erneut prüfen |
| `signal_state`, `signal_range`, `weekdays`, `time_window` | `state`-, `numeric_state`-, `time`-Bedingungen |
| Aktion `preset` / `setpoint` | `climate.set_preset_mode` / `climate.set_temperature` |

**Ablauf bei Auslösung:**
1. Auslöser feuert, Bedingungen der Regel werden geprüft.
2. **Sperren:** Bei `Vacation` und bei ausgeschaltetem Winter-Modus (`heating_control` schaltet dort mit höchster Priorität aus) setzt die Regel nichts; sie meldet `wohnungszustand`. In allen anderen Zuständen, auch `Away` und `Sleep`, darf sie den Sollwert oder das Preset setzen: Sie schaltet die Heizung nicht ein, der Wert greift erst, wenn `heating_control` beim Heimkommen oder Aufwachen den Modus wieder auf `heat` stellt (siehe unten). Die gesperrten Zustände sind je Regel einstellbar (Standard: `Vacation`).
3. **Warten auf `heating_control`:** Läuft die Automation des Raums (Attribut `current` größer 0) oder lief sie in den letzten 2 Minuten, wartet die Regel bis zum Ende (höchstens 2 Minuten, plus 5 Sekunden) und prüft die Bedingungen erneut. So gewinnt bei gleichzeitigen Flanken die KI-Regel, die später gesetzte Änderung bleibt stehen.
4. Entscheidung: Ziel gleich dem aktuellen Wert → `bereits_erreicht`. Mindestabstand verletzt → `handeingriff`. Sonst schalten, wenn Raumschalter, Regelschalter an und Sammelschalter nicht gesetzt sind; sonst `schatten` bzw. `raum_aus`.
5. Meldung per MQTT (Abschnitt 9) mit Modus, Ergebnis, Grund, Ziel und Ist-Zustand.

**Aktion bei ausgeschaltetem Heizkörper.** In `Away` (und beim Ausschalten in `Vacation`) steht der Modus auf `off`; `heating_control` setzt dort nur den Modus und lässt Preset und Sollwert unberührt, und beim Wechsel von `Away` oder `Vacation` auf `Coming Home` oder `Home` stellt es ausschließlich `hvac_mode: heat` ein. Ein von der KI gesetztes Ziel bleibt also stehen und greift beim Heimkommen. Daten vom 15.10. zeigen, dass Better Thermostat das Ziel im Modus `off` behält (Zeilen `off`, 18 °C, `eco`). Die Regel übernimmt dasselbe Vorgehen wie `heating_control` („Smart Temperature Setting“):
- *Preset:* wird direkt gesetzt, auch im Modus `off` (so geht `heating_control` vor).
- *Sollwert:* nimmt der Heizkörper im Modus `off` nicht an. Die Regel stellt ihn kurz auf `heat`, wartet höchstens 10 Sekunden auf den Wechsel, setzt den Sollwert und stellt den vorherigen Modus wieder her.
- Die Regel schaltet die Heizung nie dauerhaft ein; das bleibt der Erweiterung in Abschnitt 15 vorbehalten.

**Zusammenspiel.** Was eine KI-Regel setzt, bleibt bis zur nächsten Flanke von `heating_control` stehen (Statuswechsel, Zeitplan, nächtliche Umschaltung). Die KI braucht deshalb kein eigenes Zurücksetzen; Rückfälle, die du von Hand machst, lernt sie als eigene Regeln. `execution deploy` weist darauf hin, wenn eine Auslösezeit weniger als 10 Minuten neben einer Flanke des Zeitplans (`schedule/list`) oder der nächtlichen Umschaltung liegt.

## 7. Schalter und Modi

| Schalter | Art | Bedeutung |
|----------|-----|-----------|
| je Heizkörper „KI-Regeln <Raum>“ | `input_boolean`, von `execution init` angelegt, in `heating_control` über `ki_rules_toggle` zugeordnet | aus: alle Regeln dieses Heizkörpers nur im Schatten |
| je Regel „aktiv“ | MQTT-Schalter, optimistisch | aus: Schattenmodus der Regel |
| „alle Regeln in Schatten“ | `input_boolean`, einmal angelegt | Not-Aus; wirkt ohne Container |

Eine Regel schaltet nur, wenn Raumschalter **und** Regelschalter an und der Sammelschalter nicht gesetzt sind. Der Zustand liegt in HA und übersteht Neustarts.

## 8. Mindestabstand (in HA, ohne Container)

Je Raum ein `input_datetime` `ki_eingriff_<raum>` und eines für den Zeitpunkt des letzten Automationsaufrufs. Zwei kleine, vom Container angelegte Automationen:

1. **Aufrufe:** Auslöser Ereignis `call_service` (Domäne `climate`, Better-Thermostat-Entität). Mit Benutzerkennung → Zeitstempel *Eingriff*; ohne → Zeitstempel *Automationsaufruf*.
2. **Beobachter:** Änderung von Sollwert, Preset oder Modus an der Entität; nach 3 Sekunden Wartezeit: lag kein Aufruf (weder mit noch ohne Kennung) in den letzten 6 Sekunden, ist es ein Eingriff am Gerät → Zeitstempel *Eingriff*.

Die Regel prüft `now() − ki_eingriff_<raum> ≥ Mindestabstand`. Eigene Schaltungen und die von `heating_control` zählen nicht. Phantomwerte nach TRV-Neustart sperren eine Regel höchstens für den Mindestabstand.

## 9. MQTT

Präfix `homeai/`.

| Thema | Richtung | Inhalt |
|-------|----------|--------|
| `homeai/rule/<id>/fired` | Automation → Container | Zeit, Modus, Ergebnis (`ausgefuehrt`, `schatten`, …), Grund, Raum, Ziel (Preset oder Sollwert), Ist-Zustand; nicht retained, QoS 1 (früher in der Besprechung „shadow“) |
| `homeai/rule/<id>/active/set` und `…/state` | Dashboard ↔ Container | Schalterbefehl und Zustand (retained); der Schalter ist optimistisch, der Container beobachtet nur |
| `homeai/rule/<id>/stats` | Container → HA | Kennzahlen als Attribute einer Sensor-Entität (retained) |
| `homeai/status` | Container → HA | Lebenszeichen, Fehler, Last Will |
| `homeassistant/…` | Container → HA | Discovery je Regel: Schalter, Sensoren „Trefferquote“, „letzte Abweichung“ |

Beim Entfernen einer Regel wird die Discovery-Nachricht geleert. Fällt der Container aus, zeigt das Dashboard „Container offline“ und die Lücke in den Kennzahlen; Schatten-Meldungen aus dieser Zeit gehen verloren und werden nicht nachgeholt.

## 10. Auswertung und Kennzahlen

Der Container ordnet jedem `fired`-Ereignis ein Ergebnis in `rule_outcomes` zu, aus den aufgezeichneten Änderungen und ihrer Herkunft (Abschnitt 5). Das Auswertungsfenster reicht von `eval_before_min` (Standard 60, wie der Mindestabstand) **vor** bis `eval_after_min` (Standard 90) **nach** der Auslösung. Maßgeblich ist der **letzte Eingriff** (Herkunft `benutzer` oder `geraet_unklar`) im Raum innerhalb des Fensters.

| Modus | Ergebnis | Bedingung |
|-------|----------|-----------|
| Schatten | `treffer` | der letzte Eingriff im Fenster hat das Ziel der Regel |
| Schatten | `fehlausloesung` | der letzte Eingriff im Fenster hat ein anderes Ziel |
| Schatten | `neutral` | kein Eingriff im Fenster |
| Aktiv, Regel hat geschaltet | `bestaetigt` | kein Eingriff nach der Auslösung im Nachfenster |
| Aktiv, Regel hat geschaltet | `korrigiert` | Eingriff nach der Auslösung mit anderem Ziel (häufigstes abweichendes Ziel wird festgehalten) |
| Aktiv, Regel durch Eingriff davor ausgebremst | `treffer` oder `fehlausloesung` | wie im Schatten; die Regel hat nichts geschaltet |
| beide | `unterbrochen` | eine andere Automation hat im Raum geschaltet, zwischen Auslösung und Eingriff (ohne Eingriff: bis Fensterende) bzw. bei einem Eingriff davor zwischen Eingriff und Auslösung (zum Beispiel Flanke von `heating_control`); wird nicht gewertet |
| beide | `ohne_wirkung` | der Heizkörper stand schon auf dem Ziel; wird nicht gewertet |

**Gleiches Ziel:** Presets sind gleich, wenn sie denselben Namen haben; Sollwerte, wenn sie höchstens `target_tolerance` (Standard 0,5 °C) auseinanderliegen. Preset gegen Sollwert wird über die Temperatur des Presets verglichen, soweit sie aus den Attributen bekannt ist, sonst gelten sie als verschieden.

**Zeitpunkt.** Zu `treffer` und `fehlausloesung` wird `lage` (`vorher` oder `nachher`) und `abstand_min` des Eingriffs zur Auslösung gespeichert (negativ: davor). Ein Treffer `vorher` heißt: Der Inhalt stimmt, die Regel hätte dir die Handarbeit an diesem Tag aber nicht abgenommen.

**Nicht ausgeführt** (Gründe in `rule_events`, keine Ergebnisklassen): `handeingriff` („ausgebremst“), `wohnungszustand`, `raum_aus`, `schatten`, `bereits_erreicht`; sie werden gezählt und angezeigt.

**Kennzahlen je Regel**, alle berechnet, nie vom Modell:
- Auslösungen insgesamt und davon **gewertet** (Treffer plus Fehlauslösungen im Schatten, bestätigt plus korrigiert im aktiven Modus).
- **Trefferquote** `treffer / (treffer + fehlausloesung)`; **Korrekturquote** `korrigiert / (bestaetigt + korrigiert)`.
- **Zeitpunkt:** Median von `abstand_min` und Anteil `vorher` („meist 12 Minuten früher als die Regel“), als Hinweis, die Uhrzeit zu verschieben.
- Zahl der Ausbremsungen, häufigste Abweichung („du stellst meist 20 statt 22 °C“), daneben die Zahlen aus dem Backtest.
- Die Karte zeigt **Rohzahlen**, nicht nur Quoten: „6 von 8 gewertet richtig, 10 ohne Reaktion, 2 ohne Wirkung“.

**Aktivierungsreife** (Signal, keine Entscheidung): grün ab `min_scored` (Standard 10) **gewerteten** Auslösungen und einer Fehlauslösungs- bzw. Korrekturquote unter 20 %; darunter „zu wenig Daten (n von 10 gewertet)“. Neutrale, unterbrochene und wirkungslose Auslösungen zählen nicht mit. Die Schwellen sind Vorschläge, nicht aus deinen Daten hergeleitet, und einstellbar.

## 11. Dashboard und Bedienung

Dashboard `ki-regeln`, vom Container über die WebSocket-API angelegt und bei Änderungen neu geschrieben (nur dieses eine Dashboard; nur eingebaute Karten, nichts nachzuinstallieren). Ansichten:

| Ansicht | Inhalt |
|---------|--------|
| Übersicht | Zustand des Containers und des Collectors (letztes Ereignis, aufgezeichnete Entitäten, Größe der Datenbank), Sammelschalter, Raumschalter, Zahl aktiver Regeln, laufender und letzter Auftrag |
| Regeln | je Regel eine Karte: Regeltext in Klartext, Schalter „aktiv“, Schatten seit, Kennzahlen aus Abschnitt 10, Backtest daneben, letzte Ereignisse |
| Kandidaten | Kandidaten nach Status (`bestanden` zuerst) mit Regeltext und Backtest-Zahlen; Auswahl eines Kandidaten, Knopf „im Schatten anlegen“ |
| Analyse | Signale, Scan-Ergebnis, Vergleich der Modi; Aufträge mit und ohne Modell (unten) |
| System | Ergebnis der Systemprüfung, Verlauf der Aufträge, Fehler |

**Aufträge.** Die Bedienelemente sind per MQTT-Discovery angelegte Entitäten (Knöpfe, Auswahllisten, Schalter). Ein Knopf schickt einen Auftrag an `homeai/cmd/<name>`; der Auftragsläufer des Ausführungsdienstes führt ihn aus der festen Liste unten aus. Er führt **einen Auftrag zur Zeit** aus (ein zweiter wird abgelehnt und gemeldet), meldet Zustand (`idle`, `laeuft`, `ok`, `fehler`), Beginn und Dauer auf `homeai/job/state` (retained) und das Ergebnis als Text (`markdown`, höchstens etwa 12 KB, sonst gekürzt mit Hinweis) auf `homeai/job/result`, das eine Markdown-Karte anzeigt.

| Auftrag | Entspricht | Kosten und Schutz |
|---------|-----------|-------------------|
| Status aktualisieren | Lesen aus der Datenbank | keine |
| Signale anzeigen | `analysis signals` | keine |
| Scan | `analysis scan` | keine (ohne Modell) |
| Analyse, Trockenlauf | `analysis run --mode <weekly\|clusters\|scan> --dry-run` | keine; zeigt den Text, der an das Modell ginge (Datenschutzprüfung ohne Shell) |
| Analyse mit Modell | `analysis run --mode <…>` | kostet; nur nach „Kosten freigeben“ (Schalter, schaltet sich nach 60 Sekunden selbst aus) und innerhalb des Tageslimits `llm_runs_per_day` (Standard 3); meldet Verbrauch des Laufs |
| Kandidaten, Vergleich | `analysis list`, `analysis show <id>`, `analysis compare` | keine |
| Regel anlegen / entfernen | `execution deploy <id>` / `undeploy <id>` | Auswahl aus der Datenbank (nur Status `bestanden` bzw. angelegte Regeln) |
| Prüfen | `execution check`, `deploy --check`, `collector verify` (nur lesend) | keine |

**Sicherheit der Bedienung.** Es gibt keine freien Eingaben; Aufträge, Modi und Kennungen sind eine feste Auswahl, die der Dienst gegen die Datenbank prüft. Wer in HA die Entitäten bedienen darf, kann Aufträge auslösen; deshalb gelten Tageslimit, Kosten-Freigabe und der eine Auftrag zur Zeit. Das Dashboard zeigt Regeltexte mit Raumnamen lokal; an das Modell geht weiterhin nur das Pseudonymisierte.

**Weiter nur in der Shell oder per Datei:** Backfill eines Snapshots (Datei auf dem Host, selten), Änderungen an `analysis.yaml`, `execution.yaml` und der Collector-Konfiguration, Update und Neustart des Containers (Dockhand), Anlegen der Tokens. Die Auswahl eines Snapshots für den Backfill ließe sich später ergänzen.

## 12. Datenmodell und Konfiguration

Neue Tabellen in `home_ai.sqlite`, angelegt aus `ai/sql/execution.sql`:

```sql
service_calls(id, ts_utc, domain, service, entity_ids, data, context_id, user_id, parent_id)
automation_runs(id, ts_utc, automation_entity, name, context_id)
change_origin(state_change_id PRIMARY KEY, origin, via, actor)   -- origin: benutzer|automation|geraet_unklar|unbekannt; via: kontext|zeit|keine
deployments(rule_id PRIMARY KEY, candidate_id, room, ha_automation, state, blueprint_version, created_at, removed_at)
rule_events(id, rule_id, ts_utc, mode, outcome, reason, target, current, payload)
rule_outcomes(rule_event_id PRIMARY KEY, outcome, lage, abstand_min, window_start, window_end, deviation)
```

`ai/config/execution.yaml`: die Zuordnung Raum ↔ Automation von `heating_control` (alles Weitere liest `deploy` aus dieser Instanz), MQTT-Zugang, Mindestabstand, erlaubte Wohnungszustände, Sollwert-Grenzen (Standard 15 bis 25 °C), Auswertungsfenster (`eval_before_min`, `eval_after_min`), `target_tolerance`, Schwellen der Aktivierungsreife (`min_scored`, Fehlquote), `llm_runs_per_day`. Dazu die Tabelle `jobs(id, name, params, state, started_at, finished_at, message)` für den Verlauf der Aufträge.

## 13. Kommandos

| Kommando | Wirkung |
|----------|---------|
| `execution init [--room R]` | legt Sammelschalter, Raumschalter, Helfer und die Hilfsautomationen aus Abschnitt 8 an und nennt die Eingabe `ki_rules_toggle`, die in `heating_control` zu setzen ist |
| `execution deploy <id> [--check]` | legt die Automation im Schattenmodus an, Discovery, Dashboard; `--check` vergleicht Soll und Ist in HA |
| `execution undeploy <id>` | entfernt Automation, Entitäten, Discovery; die Historie bleibt |
| `execution rules` | listet Regeln mit Modus und Kennzahlen |
| `execution serve` | Dauerbetrieb: MQTT, Auswertung, Kennzahlen |
| `execution check` | prüft Blueprint, Helfer, Token-Rechte, Broker |

`deploy` nimmt nur Kandidaten mit Status `bestanden`. Die Kommandos bleiben für Einrichtung und Fehlersuche; im Normalbetrieb ersetzt sie das Dashboard (Abschnitt 11), das dieselben Funktionen aufruft.

## 14. Fehlerbehandlung, Tests, Sicherheit, Risiken

**Fehlerbehandlung.** Schlägt das Anlegen fehl, steht der Fehler im Kommando und in `homeai/status`; Zustand in `deployments` erst nach Erfolg, kein halbes Deployment. Eine in HA von Hand gelöschte Automation erkennt `deploy --check`. Ohne Container laufen Regeln und Schalter weiter (Erfolgskriterium 3).

**Tests (ohne echtes HA).**
- Übersetzung Kandidat → Blueprint-Eingaben für jede Form des Vokabulars; im Ergebnis weder `sig_NN` noch Klarnamen.
- Herkunft: Folgen aus Klick mit Kennung, Klick mit verlorener Kennung (Zuordnung über Zeit), Automation mit und ohne Parent, Drehen am Gerät, mehrere Räume gleichzeitig, Zeitraum ohne Ereignisse (`unbekannt`).
- Rückkopplung: eigene Schaltungen und Schaltungen von `heating_control` ergeben keinen Eintrag in `manual_actions`.
- Ergebnisse (`rule_outcomes`) auf festen Abläufen für alle Zeilen der Tabelle in Abschnitt 10, darunter Eingriff 20 Minuten vor und 10 Minuten nach der Auslösung mit gleichem Ziel (beide `treffer`, mit `lage`), mehrere Eingriffe im Fenster (der letzte zählt), Toleranz bei Sollwerten (0,5 gleich, 1,0 verschieden), Preset gegen Sollwert und die Aktivierungsreife mit vielen neutralen Auslösungen (bleibt „zu wenig Daten“).
- HA-API und MQTT durch Attrappen: Anlegen, Entfernen, Rücknahme bei Fehlern, Aufräumen der Discovery.
- Zustand der Mindestabstand-Logik als Tabelle von Abläufen (Eingriff, Automationsaufruf, Gerät, beides kurz hintereinander).
- Auftragsläufer: nur Aufträge der Liste, ein Auftrag zur Zeit, Ablehnung eines zweiten, Kosten-Freigabe (läuft nach 60 Sekunden ab), Tageslimit, Gültigkeit der Auswahl (unbekannte oder nicht `bestanden`e Kennung wird abgelehnt), Kürzung langer Ergebnisse.
- Lesen der Raum-Konfiguration aus der `heating_control`-Instanz, auch mit leerem `ki_rules_toggle` (klare Ablehnung).

**Test an der echten HA (einmalig, von Hand).** Eine Testregel für einen Raum im Schatten anlegen und auslösen; Schalter umlegen und prüfen, dass das Thermostat sich ändert; Container stoppen und prüfen, dass Regel und Schalter weiterlaufen; entfernen und prüfen, dass nichts zurückbleibt. Dabei auch bestätigen: dass ein Preset bei Modus `off` angenommen wird und das Ziel bis zum Wechsel auf `heat` erhalten bleibt; dass der Sollwert über das kurze Einschalten (wie in `heating_control`) gesetzt und der Modus danach wiederhergestellt wird; dass nach `Away` → `Coming Home` das von der Regel gesetzte Ziel gilt; Zeit-Auslöser mit `offset` auf den Wecker-Sensor, Wiederherstellung des Zustands des optimistischen MQTT-Schalters nach HA-Neustart, Mindestversion von HA für die Selektoren `trigger` und `condition` im Blueprint.

**Sicherheit.**
- Der Ausführungsdienst hat einen eigenen Token (Anlegen von Automationen braucht Administratorrechte); empfohlen ist ein eigener HA-Benutzer „homeai“, damit seine Aufrufe als solche erkennbar sind. Der Collector behält den lesenden Token.
- Geschaltet werden nur Heizkörper, die als `climate` mit dem Label geführt sind; Presets müssen in `preset_modes` des Heizkörpers stehen; Sollwerte werden auf die Grenzen begrenzt, unabhängig vom Kandidaten.
- Schaltlogik, Mindestabstand und Not-Aus liegen in HA und brauchen den Container nicht.
- An das Modell gehen weiterhin nur Pseudonyme; Herkunft, Benutzerkennungen und Entity-IDs verlassen die Datenbank nicht.

**Risiken.**
- **Zuordnung nicht lückenlos:** Aufrufe mit Bereich oder Gerät als Ziel und Kontextverlust beim Schreiben der Entität werden über die Zeit zugeordnet (1 Sekunde); zwei Aufrufe in dieser Zeit sind mehrdeutig. Im Zweifel `geraet_unklar`.
- **Schwache Batterien** erzeugen Scheineingriffe (Bad am 15.10.); der TRV im Bad meldet seit Tagen „Batterie schwach“.
- **Wenig Daten:** Der Schattenmodus braucht Wochen, bis eine Regel genug Auslösungen hat.
- **Zeitgleiche Flanken:** Die Wartezeit auf `heating_control` löst die meisten Fälle; ein Rest mit zwei Aufrufen in kurzer Folge bleibt (später gewinnt).
- **Blueprint von Hand einspielen:** Wird die Datei in HA vergessen oder veraltet, scheitert `deploy` mit klarer Meldung. Dasselbe gilt für V4.29 von `heating_control`.
- **Kosten durch das Dashboard:** Wer die Entitäten bedienen darf, kann Läufe mit dem Modell auslösen. Kosten-Freigabe und Tageslimit begrenzen das; der Schlüssel liegt im Ausführungsdienst, gemeinsam mit dem HA-Token. Eine Trennung in zwei Dienste wäre möglich, kostet aber einen zweiten Prozess.

## 15. Offene Punkte

1. **Beobachtungen zur Herkunft** (aus `service_calls` und `automation_runs` des Collectors, am 24.10. geprüft; der Lauscher ist dafür nicht mehr nötig): Die nächtliche Umschaltung (01:00) erscheint als `call_service` ohne Benutzerkennung, mit derselben Kontext-ID wie die Zustandszeile, und steht in `change_origin` als `automation` mit dem Namen der `heating_control`-Automation (bestätigt, Abschnitt 5 bleibt). Die Zeitplan-Flanke ist offen: Der Beispielwert 18:30 (Büro) stammt aus einem Testbeispiel und ist nicht belegt; sie lässt sich erst prüfen, wenn ein Zeitplan aktiv ist. Die Automation „TRV – Log actor with exact context“ ist abgeschaltet (sie füllte `automation_runs` mit rund 5000 Zeilen am Tag und liefert nur `collector verify` noch Daten).
2. **Raumschalter in `heating_control`:** Entschieden: `input_boolean` je Raum, verknüpft über die neue optionale Eingabe `ki_rules_toggle` (V4.29). Eine Blueprint-Einstellung wie „Scheduler aktivieren“ ist fest in der Automation gespeichert und zur Laufzeit nicht umlegbar, `heating_control` selbst trennt das schon so (`scheduler_enabled` und `scheduler_toggle`).
3. **Bereinigung alter Eingriffe** (Folgeaufgabe zu Teilprojekt 1): Der Backfill und die Lückenfüllung enthalten viele Änderungen ohne Kontext (`unbekannt`), darunter Schaltungen von `heating_control` (gleichzeitige Wechsel in mehreren Räumen, volle Stunden). Ein frischer Snapshot der HA-Datenbank mit anschließendem `backfill` kann Kontext nachtragen; was übrig bleibt, wird über Muster (Mehrraum-Wechsel, feste Zeiten) gefiltert, bevor das Modell damit arbeitet. Zuvor prüfen, wie die 175 Zeilen der Lücke (`gap`) zu ihrem Kontext kamen (vermutlich Nachtrag durch den Backfill).
4. **Ursache des Kontextverlusts** vom 14.10. (14:58 UTC, Büro, Button) ist unbekannt und nicht nachstellbar; die Zuordnung über die Zeit fängt den Fall ab.
5. **Erweiterung „Heizung einschalten“ (später, nicht Teil dieses Teilprojekts):** Eine Regel wie „30 Minuten vor der Heimkehr einschalten“ braucht eine Aktion, die den Modus von `off` auf `heat` stellt (Vokabular, Backtest und Eingriffe in Teilprojekt 2: bisher zählen nur Sollwert und Preset), und einen Rückfall nach N Minuten (Standard 120), wenn der Wohnungszustand nicht auf `Home` oder `Coming Home` wechselt. Der Zwischenschritt in Abschnitt 6 (Ziel setzen, auch in `Away` und `Sleep`) deckt den Teil ab, der ohne Einschalten wirkt.
6. **Alt-Entscheidungen:** `analysis deploy` aus der Besprechung wird zu `execution deploy`; das Thema `shadow` zu `fired`.

## 16. Verzeichnisstruktur

```
ai/execution/translate.py     # Kandidat → Blueprint-Eingaben
ai/execution/ha_api.py        # REST/WebSocket: Automationen, Helfer, Dashboard
ai/execution/mqtt.py          # Discovery, Befehle, Meldungen
ai/execution/outcomes.py      # rule_outcomes und Kennzahlen
ai/execution/cli.py           # init, deploy, undeploy, rules, serve, check
ai/execution/jobs.py          # Auftragsläufer: Liste, Sperre, Kosten-Freigabe, Tageslimit
ai/execution/dashboard.py     # Dashboard ki-regeln und Discovery der Bedienelemente
ai/collector/origins.py       # Herkunft: Zuordnung, change_origin
blueprints/ki_regel.yaml      # Blueprint
ai/sql/execution.sql, ai/config/execution.yaml
ai/tests/test_translate.py, test_origins.py, test_outcomes.py, test_execution_cli.py, test_jobs.py
```
Geändert: `heating_control.yaml` (V4.29, Eingabe `ki_rules_toggle`), `collector/live.py` (Abos), `collector/db.py`, `ai/sql/schema.sql` (View `manual_actions`), `analysis/interventions.py`, Compose-Datei (zweiter Dienst).
