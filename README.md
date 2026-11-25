# 🏠 Heizungssteuerung Blueprint für Home Assistant

Ein leistungsstarkes und flexibles Blueprint zur automatischen Steuerung deiner Heizung basierend auf Anwesenheitsstatus, Zeitplänen und intelligenter Priorisierung.

[![Version](https://img.shields.io/badge/Version-4.33-blue.svg)](https://github.com/fabsss/heating_control)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1+-green.svg)](https://www.home-assistant.io/)
[![Blueprint](https://img.shields.io/badge/Blueprint-Import-orange.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/fabsss/heating_control/blob/main/heating_control.yaml)

---

## ✨ Features

### 🎯 Kernfunktionen
- **Anwesenheitsbasierte Steuerung**: Automatische Anpassung der Heiztemperatur basierend auf dem Wohnungsstatus
- **Intelligente Priorisierung**: Klare Hierarchie verhindert Konflikte zwischen verschiedenen Modi
- **Smart Temperature Setting**: Thermostaten akzeptieren Temperaturänderungen auch im "Off"-Modus durch temporäres Einschalten
- **Optionaler Winter-Modus**: Globaler An/Aus-Schalter für die gesamte Heizungslogik
- **Zeitplansteuerung**: Ein Zeitplan, mehrere Stufen: Komfort, Eco oder Home Low (Tagesabsenkung) je Block über "Additional data"

### 🚀 Erweiterte Features
- **Office-Override**: Deaktiviert den Scheduler an Arbeitstagen im Büro
- **Scheduler-Toggle**: Laufzeit-Steuerung des Schedulers per Input Boolean
- **Nächtlicher Reset**: Setzt in jedem Wohnungszustand den Standardzustand (Sleep: Sleep-Preset, sonst Zeitplan-Stufe bzw. Eco)
- **Hold bei manuellen Änderungen**: Eine manuelle Änderung am Heizkörper wird für eine einstellbare Zeit nicht vom Zeitplan überschrieben
- **Template-Trigger-Technologie**: Zuverlässiges Triggern auch bei optionalen Entities
- **Validierung**: Automatische Prüfung der Konfiguration mit Fehlerbenachrichtigungen

---

## 📋 Voraussetzungen

### Erforderliche Helper
Erstelle folgende Helper unter **Einstellungen → Geräte & Dienste → Helfer**:

#### 1. Anwesenheitsstatus (input_select) - **Pflicht**
Verwaltet den Status deiner Wohnung.

**Erforderliche Optionen:**
- `Home` - Zu Hause
- `Away` - Abwesend
- `Sleep` - Schlafmodus
- `Leaving` - Beim Verlassen
- `Coming Home` - Beim Heimkommen
- `Guest Mode` - Gäste-Modus
- `Vacation` - Urlaubsmodus

**Beispiel-Konfiguration** (`configuration.yaml`):
```yaml
input_select:
  flat_occupation_state:
    name: Wohnungsstatus
    options:
      - "Home"
      - "Away"
      - "Sleep"
      - "Leaving"
      - "Coming Home"
      - "Guest Mode"
      - "Vacation"
    icon: mdi:home-account
```

#### 2. Optionale Helper

**Heizung An/Aus (input_boolean)** - Optional
- Winter-Modus für die gesamte Heizungslogik
- Wenn nicht gesetzt: Heizung ist immer aktiv

**Zeitplan (schedule)** - Optional, aber erforderlich wenn Scheduler aktiviert
- Schedule Helper für Komfort-Temperatur-Zeiten
- Erstelle über: Einstellungen → Geräte & Dienste → Helfer → Zeitplan

**Scheduler Toggle (input_boolean)** - Optional
- Laufzeit-Steuerung des Schedulers
- Hat Vorrang vor der statischen "Scheduler aktivieren" Einstellung

**Office-Override (binary_sensor)** - Optional
- Deaktiviert Scheduler an Arbeitstagen im Büro
- Beispiel: Template-Sensor für Arbeitstage

**Hold-Timer (timer)** - Optional, ein Timer pro Raum
- Wird bei einer manuellen Änderung am Heizkörper gestartet (siehe [Hold](#-hold-bei-manuellen-änderungen))
- Erstelle über: Einstellungen → Geräte & Dienste → Helfer → Timer (Option "Wiederherstellen" aktivieren)

**Hold-Dauer (input_number)** - Optional, ein Helper für alle Räume
- Dauer des Holds in Minuten (ohne Angabe: 180)

---

## ⚙️ Blueprint-Eingaben

### Pflichtfelder

| Eingabe | Typ | Beschreibung |
|---------|-----|--------------|
| **Heizkörper** | Climate Entity | Der zu steuernde Thermostat |
| **Anwesenheitsstatus** | Input Select | Status der Wohnung (siehe Voraussetzungen) |

### Temperaturen

| Eingabe | Standard | Beschreibung |
|---------|----------|--------------|
| **Eco-Preset** | kein Preset | (Optional) Preset der Climate-Entität (z.B. Better Thermostat) für Eco (Basis-Zustand) – hat Vorrang vor der Temperatur |
| **Komfort-Preset** | kein Preset | (Optional) Preset der Climate-Entität für Komfort – hat Vorrang vor der Temperatur |
| **Home-Low-Preset** | kein Preset | (Optional) Preset für die Tagesabsenkung (zuhause, aber kühler als Eco), z.B. `home` |
| **Sleep-Preset** | kein Preset | (Optional) Preset für den Wohnungszustand `Sleep`. Ohne Angabe verhält sich Sleep wie Eco |
| **Eco-Temperatur** | 17°C | Fallback-Temperatur für Eco: Zeitplan aus, nächtlicher Reset (wenn kein Preset gewählt/verfügbar) |
| **Komfort-Temperatur** | 21°C | Fallback-Temperatur für Komfort (wenn kein Preset gewählt/verfügbar) |
| **Home-Low-Temperatur** | 16°C | Fallback-Temperatur für die Tagesabsenkung |
| **Sleep-Temperatur** | 0 (= Eco) | Fallback-Temperatur für Sleep; 0 verwendet die Eco-Temperatur |

**Presets:** Die Dropdown-Liste bietet gängige Namen (`eco`, `comfort`, `away`, `sleep`, …); eigene Namen können frei eingegeben werden (exakt wie im Attribut `preset_modes` der Entität). Groß-/Kleinschreibung spielt keine Rolle; verwendet wird der echte Name aus `preset_modes`. Bietet die Entität das Preset nicht an, fällt das Blueprint auf die feste Temperatur zurück und zeigt eine persistente Benachrichtigung mit den verfügbaren Presets an. Presets werden auch bei ausgeschaltetem Thermostat gesetzt (Better Thermostat übernimmt sie beim Einschalten).

### Scheduler-Optionen (Optional)

| Eingabe | Typ | Standard | Beschreibung |
|---------|-----|----------|--------------|
| **Scheduler aktivieren** | Boolean | `false` | Aktiviert die Zeitplan-Funktion |
| **Zeitplan** | Schedule | - | Schedule Helper; je Block optional `preset: comfort`, `eco` oder `home_low` ⚠️ |
| **Scheduler Toggle** | Input Boolean | - | Runtime-Steuerung (hat Vorrang) |
| **Office-Override** | Binary Sensor | - | Deaktiviert Scheduler bei Arbeitstagen (es gilt dann Eco) |
| **Hold-Timer** | Timer | - | Timer pro Raum für den Hold bei manuellen Änderungen |
| **Hold-Dauer** | Input Number | 180 | Dauer des Holds in Minuten (gemeinsam für alle Räume) |

⚠️ **Wichtig**: Wenn "Scheduler aktivieren" = `true`, muss eine Zeitplan-Entity konfiguriert werden!

### Weitere Optionen

| Eingabe | Typ | Standard | Beschreibung |
|---------|-----|----------|--------------|
| **Heizung An/Aus** | Input Boolean | - | Globaler Winter-Modus Schalter |
| **Nächtlicher Reset** | Time | 01:00 | Zeit für den Reset in jedem Wohnungszustand (Eingabe `nightly_eco_time`) |

---

## 🔄 Funktionsweise & Prioritäten

Die Automation folgt einer **klaren Prioritätshierarchie**:

### Priorität 1: 🛑 Heizung AUS
**Bedingungen:**
- Winter-Modus ist deaktiviert (`heating_on_off = off`)
- **ODER** Anwesenheitsstatus = `Vacation`

**Aktion:** Thermostat wird ausgeschaltet

---

### Priorität 2: 🚪 Verlassen der Wohnung
**Bedingungen:**
- Status wechselt von einem beliebigen Zustand (außer `Away`, `Vacation`, `unavailable`, `unknown`) zu `Away`

**Aktion:** Thermostat auf HVAC-Modus `off` setzen

---

### Priorität 3: 😴 Sleep-Modus
**Bedingungen:**
- Status wechselt zu `Sleep`

**Aktion:** 
- Sleep-Preset setzen (ohne Sleep-Preset: Eco wie bisher; Temperatur-Fallback: Sleep-Temperatur, sonst Eco-Temperatur)
- Ein laufender Hold endet

**Beim Verlassen von `Sleep`** (in jeden anderen Zustand, auch `Away`, `Vacation` oder Winter-Modus aus) wird die Zielrolle (siehe unten) neu gesetzt, damit das Sleep-Preset nicht stehen bleibt: Zeitplan aktiv → Stufe des Blocks, sonst Eco. Bei `Away`/`Vacation` wird nur das Preset gesetzt, der Heizmodus bleibt wie von den übrigen Regeln bestimmt.

---

### Priorität 4: 🌙 Nächtlicher Reset
**Bedingungen:**
- Zeitpunkt = Konfigurierter "Nächtlicher Reset"
- Gilt in jedem Wohnungszustand, nicht bei Winter-Modus aus oder `Vacation` (dort bleibt die Heizung aus)

**Aktion:**
- Zielrolle setzen: bei `Sleep` das Sleep-Preset, sonst die Stufe des aktiven Zeitplan-Blocks bzw. Eco
- Ein laufender Hold endet
- Bei Status `Home`, `Guest Mode`, `Sleep`, `Leaving`, `Coming Home`: HVAC-Modus `heat`
- Bei anderen Status: HVAC-Modus `off`

---

### Priorität 5: 🏠 Nach Hause kommen
**Bedingungen:**
- Status wechselt von `Away` zu `Coming Home` oder `Home`

**Aktion:** HVAC-Modus auf `heat` setzen

---

### Standard-Logik: 📅 Scheduler und Rollen

Die Automation kennt vier **Rollen**. Jede hat ein Preset und eine Fallback-Temperatur:

| Rolle | Wann | Preset / Temperatur |
|-------|------|---------------------|
| **eco** (Basis) | Zeitplan aus, Scheduler deaktiviert, Office-Override, nächtlicher Reset | Eco-Preset / Eco-Temperatur |
| **comfort** | Zeitplan-Block ohne Angabe oder mit `preset: comfort` | Komfort-Preset / Komfort-Temperatur |
| **home_low** | Zeitplan-Block mit `preset: home_low` (Tagesabsenkung: zuhause, aber kühler als Eco) | Home-Low-Preset / Home-Low-Temperatur |
| **sleep** | Wohnungszustand `Sleep` | Sleep-Preset / Sleep-Temperatur (ohne Angabe wie Eco) |

**Zeitplan-Block konfigurieren:** Beim Bearbeiten eines Blocks unter *Weitere Optionen → Additional data* eintragen:

```yaml
preset: home_low
```

Erlaubt sind `comfort`, `eco` und `home_low`. Ohne Eintrag gilt Komfort (wie bisher), ein unbekannter Wert löst eine Benachrichtigung aus und es gilt Komfort. Home Assistant stellt die Daten nur bei aktivem Block als Attribut bereit; ist kein Block aktiv, gilt Eco. Die Standard-Oberfläche des Zeitplans zeigt die Zusatzdaten nicht an (nur beim Öffnen des jeweiligen Blocks).

**Wann wird geschrieben?** Nur bei einem **echten Wechsel der Rolle**. Grenzen zwei Blöcke mit gleicher Rolle aneinander, bleibt der Heizkörper unverändert. Der Zeitplan-Trigger reagiert auch auf Wechsel zwischen aneinandergrenzenden Blöcken (nur das Attribut ändert sich).

**Weitere Auslöser:** Scheduler-Toggle und Winter-Modus (wenn sie die Steuerung einschalten).

**Während `Sleep`** werden Scheduler-Wechsel nicht angewendet (das Sleep-Preset bleibt). Beim Verlassen von `Sleep` wird die Rolle neu berechnet (siehe Priorität 3).

**Office-Override:** Ist er aktiv, verhält sich die Heizung, als gäbe es keinen Scheduler (Basis Eco, bei `Away` aus). Es wird dabei nichts aktiv geschrieben, bis ein anderer Auslöser kommt (wie bisher).

---

### 🖐️ Hold bei manuellen Änderungen

Mit einem **Hold-Timer** wird eine manuelle Änderung am Heizkörper (App, Dashboard oder am Gerät) für eine einstellbare Zeit (Standard 180 Minuten, gemeinsame Einstellung über den Helper "Hold-Dauer") nicht vom Zeitplan überschrieben.

- **Start:** Eine Änderung von Preset oder Temperatur, die nicht von einer Automation stammt und vom Soll der aktuellen Rolle abweicht. Auch bei ausgeschaltetem Zeitplan (dann ist das Soll Eco); nur in der Heizsaison, nicht in `Vacation` oder `Sleep`.
- **Solange der Hold läuft:** Zeitplan-Wechsel werden nicht angewendet, KI-Regeln mit demselben Timer pausieren.
- **Ablauf:** Es wird nichts geschaltet. Die manuelle Einstellung bleibt stehen, bis ein neues Ereignis kommt: der nächste Rollenwechsel im Zeitplan, `Sleep`, der nächtliche Reset oder eine KI-Regel. Während des Holds unterdrückte Zeitplan-Wechsel werden nicht nachgeholt.
- **Vorzeitiges Ende:** Sleep, nächtlicher Reset, Einschalten von Scheduler-Toggle oder Winter-Modus. `Away`/`Vacation` beenden den Hold **nicht**.
- Ohne Hold-Timer gibt es keinen Hold (Verhalten wie bisher).

> **Hinweis:** Die Erkennung "manuell" nutzt den Kontext der Statusänderung des Thermostats (keine Automation als Verursacher). Ob Better Thermostat diesen Kontext durchreicht, ist je nach Version unterschiedlich. Bitte nach der Einrichtung einmal testen: Preset in der App ändern → der Timer sollte starten.

---

## 🔧 Smart Temperature Setting

Ein besonderes Feature für Thermostaten, die im "Off"-Modus keine Temperaturen akzeptieren (gilt nur für den Temperatur-Fallback; Presets können auch im "Off"-Modus gesetzt werden):

```yaml
1. Aktuellen HVAC-Modus speichern
2. Falls "off": Temporär auf "heat" schalten
3. 2 Sekunden warten
4. Temperatur setzen
5. Falls vorher "off": Wieder auf "off" schalten
```

Dies garantiert, dass Temperaturänderungen immer funktionieren!

---

## 📦 Installation

### Methode 1: Automatischer Import
[![Blueprint importieren](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/fabsss/heating_control/blob/main/heating_control.yaml)

### Methode 2: Manuelle Installation
1. Kopiere den Inhalt von [`heating_control.yaml`](heating_control.yaml)
2. Gehe zu **Einstellungen → Automationen & Szenen**
3. Klicke auf **Blueprints** → **Blueprint importieren**
4. Füge die URL ein: `https://github.com/fabsss/heating_control/blob/main/heating_control.yaml`

---

## 🚀 Schnellstart

1. **Helper erstellen** (siehe [Voraussetzungen](#-voraussetzungen))
2. **Automation anlegen**:
   - Gehe zu **Einstellungen → Automationen & Szenen**
   - **Automation erstellen** → **Blueprint verwenden**
   - Wähle **Heizungssteuerung (V4.33)**
3. **Konfigurieren**:
   - Heizkörper auswählen
   - Anwesenheitsstatus-Helper zuweisen
   - Optional: Winter-Modus, Scheduler, etc. konfigurieren
4. **Speichern & Testen**! 🎉

---

## 💡 Beispiel-Konfigurationen

### Minimale Konfiguration
```yaml
Heizkörper: climate.wohnzimmer_thermostat
Anwesenheitsstatus: input_select.flat_occupation_state
Eco-Temperatur: 17°C
Komfort-Temperatur: 21°C
```

### Mit Scheduler
```yaml
Heizkörper: climate.wohnzimmer_thermostat
Anwesenheitsstatus: input_select.flat_occupation_state
Scheduler aktivieren: ✓
Zeitplan: schedule.heating_schedule
Eco-Temperatur: 17°C
Komfort-Temperatur: 22°C
```

### Vollständige Konfiguration
```yaml
Heizkörper: climate.buero_thermostat
Anwesenheitsstatus: input_select.flat_occupation_state
Heizung An/Aus: input_boolean.winter_mode
Scheduler aktivieren: ✓
Zeitplan: schedule.heating_schedule_office
Scheduler Toggle: input_boolean.scheduler_override
Office-Override: binary_sensor.office_day_template
Eco-Temperatur: 17°C
Komfort-Temperatur: 21°C
Nächtliche Eco-Zeit: 01:00:00
```

---

## 🐛 Fehlerbehebung

### Scheduler triggert nicht
**Problem:** Schedule-Entity wechselt, aber nichts passiert

**Lösung:**
1. Prüfe, ob `scheduler_enabled = true` ist
2. Stelle sicher, dass eine `schedule_entity` konfiguriert ist
3. Prüfe, ob `office_day_eco_override` die Scheduler-Logik blockiert

### Konfigurationsfehler beim Speichern
**Problem:** `Message malformed: ...`

**Lösung:** 
- Wenn "Scheduler aktivieren" = `true`, muss eine Schedule-Entity gewählt werden
- Die Automation zeigt eine persistente Benachrichtigung bei Fehlkonfiguration und läuft danach ohne Scheduler weiter

### Template-Fehler
**Problem:** `TypeError: unhashable type: 'list'`

**Lösung:** Dieses Blueprint verwendet `trigger_variables` und Template-Trigger zur sicheren Behandlung optionaler Entities. Stelle sicher, dass du die neueste Version (V4.21+) verwendest.

---

## 🔄 Updates & Changelog

### Version 4.33 (Aktuell)
- 🐛 Hold ist nur noch eine Sperre: Beim Ablauf wird nicht mehr auf die Zeitplan-Stufe bzw. Eco zurückgeschaltet

### Version 4.32
- ✅ Hold-Timer startet auch bei ausgeschaltetem Zeitplan, damit KI-Regeln Handeingriffe respektieren

### Version 4.31
- 🐛 Hold-Timer: Eine reine Temperaturänderung am Heizkörper (Preset bleibt gleich, z.B. Drehen am Thermostat) wird jetzt als manueller Eingriff erkannt

### Version 4.30
- ✅ Zeitplan mit mehreren Stufen: Block-Attribut `preset: comfort | eco | home_low` (ohne Angabe: Komfort, Zeitplan aus: Eco)
- ✅ Neue Rolle Home Low (Tagesabsenkung) mit eigenem Preset und eigener Temperatur
- ✅ Sleep setzt ein eigenes Sleep-Preset (ohne Angabe wie bisher Eco); beim Verlassen von Sleep wird die Rolle neu gesetzt
- ✅ Nächtlicher Reset setzt die Zielrolle (Sleep, Zeitplan-Stufe oder Eco) statt fest Eco; Beschreibung korrigiert (gilt in jedem Wohnungszustand)
- ✅ Hold-Timer: manuelle Änderungen werden für eine einstellbare Zeit nicht vom Zeitplan überschrieben
- ✅ Zeitplan-Trigger reagiert auch auf Wechsel zwischen aneinandergrenzenden Blöcken; geschrieben wird nur bei echtem Rollenwechsel
- ✅ Presets werden ohne kurzes Einschalten gesetzt; Temperatur-Fallback unverändert

### Version 4.28
- 🐛 Konfigurationsfehler "Scheduler ohne Zeitplan" bricht die Automation nicht mehr ab, sondern meldet nur noch (zuvor wurde z.B. `Away` nicht mehr verarbeitet)

### Version 4.27
- ✅ Presets der Climate-Entität (z.B. Better Thermostat) für Eco und Komfort, mit Fallback auf die feste Temperatur
- ✅ Kein kurzes Einschalten mehr nötig, wenn ein Preset gesetzt wird
- ✅ Verlassen-Erkennung: Wechsel aus beliebigen Zuständen nach `Away` schaltet die Heizung aus
- ✅ Rückkehr aus `Vacation` schaltet die Heizung wieder ein
- ✅ Scheduler pausiert während `Sleep` und wird beim Verlassen von `Sleep` nachgeholt
- ✅ Nächtliche Umschaltung: `Coming Home` wird nicht mehr ausgeschaltet
- ✅ `wait_template` statt fester Wartezeit; `continue_on_error` bei Thermostat-Aufrufen

### Version 4.21
- ✅ Template-Trigger mit `trigger_variables` für zuverlässiges Triggern
- ✅ Smart Temperature Setting für Thermostaten
- ✅ Vollständige Unterstützung optionaler Entities mit `default: []`
- ✅ Office-Override Feature
- ✅ Konfigurationsvalidierung mit Fehlerbenachrichtigungen

---

## 🔍 Zusätzlicher Blueprint: TRV Actor Logging

Dieses Repository enthält einen zweiten Blueprint zur **Protokollierung von Thermostat-Änderungen**: [`log_trv_actor_with_context.yaml`](log_trv_actor_with_context.yaml)

### Was macht dieser Blueprint?

Erfasst **manuelle Änderungen** an Thermostaten und erstellt detaillierte Event-Logs:
- 📊 **Setpoint-Änderungen**: Protokolliert Temperaturänderungen
- 🎚️ **HVAC-Mode-Änderungen**: Erfasst Modusänderungen (heat, off, etc.)
- 👤 **Actor-Tracking**: Identifiziert welcher Benutzer die Änderung vorgenommen hat
- 🔗 **Context-ID-Verknüpfung**: Nutzt `context.id` für exakte Zuordnung

### Anwendungsfälle

- **Debugging**: Verstehe warum und von wem dein Thermostat geändert wurde
- **Analyse**: Tracke manuelle Überschreibungen deiner Automationen
- **Audit**: Protokolliere alle Änderungen für spätere Auswertung
- **Integration**: Reagiere auf User-Änderungen mit eigenen Automationen

### Features

✅ **Multi-Thermostat-Unterstützung**: Überwache mehrere Thermostaten gleichzeitig  
✅ **Selektive Protokollierung**: Wähle ob Setpoint, HVAC-Mode oder beides geloggt wird  
✅ **Filterbare HVAC-Modi**: Definiere welche Modi relevant sind (z.B. nur "heat, off")  
✅ **Event-basiert**: Erstellt `thermostat_actor_logged` Events für einfache Weiterverarbeitung  
✅ **Context-ID-Tracking**: Präzise Zuordnung über `state_context_id`

### Blueprint-Konfiguration

| Eingabe | Beschreibung | Standard |
|---------|--------------|----------|
| **Thermostat entities** | Ein oder mehrere Climate-Entities | - |
| **Setpoint-Änderungen protokollieren** | Logge Temperaturänderungen | `true` |
| **HVAC-Mode-Änderungen protokollieren** | Logge Modusänderungen | `true` |
| **Relevante HVAC-Modi** | CSV-Liste der zu trackenden Modi | `"heat, off"` |

### Event-Datenstruktur

Das erstellte `thermostat_actor_logged` Event enthält:

```yaml
entity_id: climate.wohnzimmer_thermostat
state_context_id: "abc123..."              # Context-ID des State-Changes
state_last_changed: "2025-10-21T14:30:00"
old_setpoint: "20.0"
new_setpoint: "22.0"
old_hvac_mode: "heat"
new_hvac_mode: "heat"
setpoint_changed: true
hvac_mode_changed: false
change_types: ["setpoint"]                  # Liste: setpoint, hvac_mode
actor_type: "user"
actor_id: "abc123user"                      # User-ID aus context
actor_name: "Fabian"                        # Resolved von person entity
detection_method: "context_user"
```

### Verwendung mit anderen Automationen

Du kannst auf diese Events reagieren:

```yaml
trigger:
  - platform: event
    event_type: thermostat_actor_logged
    event_data:
      entity_id: climate.wohnzimmer_thermostat
      setpoint_changed: true

action:
  - service: notify.mobile_app
    data:
      message: >
        {{ trigger.event.data.actor_name }} hat die Temperatur 
        von {{ trigger.event.data.old_setpoint }}° 
        auf {{ trigger.event.data.new_setpoint }}° geändert!
```

### Installation

[![Blueprint importieren](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/fabsss/heating_control/blob/main/log_trv_actor_with_context.yaml)

Oder manuell über die URL:
```
https://github.com/fabsss/heating_control/blob/main/log_trv_actor_with_context.yaml
```

---

## 🤝 Beitragen

Fehler gefunden? Feature-Wunsch? 
- [Issue erstellen](https://github.com/fabsss/heating_control/issues)
- [Pull Request einreichen](https://github.com/fabsss/heating_control/pulls)

---

## 📄 Lizenz

Dieses Blueprint ist unter der MIT-Lizenz veröffentlicht.

---

## ⭐ Gefällt dir dieses Blueprint?

Gib dem Repository einen Stern auf GitHub! ⭐
