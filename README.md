# 🏠 Heizungssteuerung Blueprint für Home Assistant

Ein leistungsstarkes und flexibles Blueprint zur automatischen Steuerung deiner Heizung basierend auf Anwesenheitsstatus, Zeitplänen und intelligenter Priorisierung.

[![Version](https://img.shields.io/badge/Version-4.21-blue.svg)](https://github.com/fabsss/heating_control)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1+-green.svg)](https://www.home-assistant.io/)
[![Blueprint](https://img.shields.io/badge/Blueprint-Import-orange.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/fabsss/heating_control/blob/main/heating_control.yaml)

---

## ✨ Features

### 🎯 Kernfunktionen
- **Anwesenheitsbasierte Steuerung**: Automatische Anpassung der Heiztemperatur basierend auf dem Wohnungsstatus
- **Intelligente Priorisierung**: Klare Hierarchie verhindert Konflikte zwischen verschiedenen Modi
- **Smart Temperature Setting**: Thermostaten akzeptieren Temperaturänderungen auch im "Off"-Modus durch temporäres Einschalten
- **Optionaler Winter-Modus**: Globaler An/Aus-Schalter für die gesamte Heizungslogik
- **Zeitplansteuerung**: Flexibler Scheduler für Komfort-Temperatur zu definierten Zeiten

### 🚀 Erweiterte Features
- **Office-Override**: Deaktiviert den Scheduler an Arbeitstagen im Büro
- **Scheduler-Toggle**: Laufzeit-Steuerung des Schedulers per Input Boolean
- **Nächtliche Eco-Umschaltung**: Automatische Absenkung im Guest Mode
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
| **Eco-Temperatur** | 17°C | Temperatur für Away, Sleep, nächtlicher Reset |
| **Komfort-Temperatur** | 21°C | Temperatur wenn Scheduler aktiv |

### Scheduler-Optionen (Optional)

| Eingabe | Typ | Standard | Beschreibung |
|---------|-----|----------|--------------|
| **Scheduler aktivieren** | Boolean | `false` | Aktiviert die Zeitplan-Funktion |
| **Zeitplan** | Schedule | - | Schedule Helper für Komfort-Zeiten ⚠️ |
| **Scheduler Toggle** | Input Boolean | - | Runtime-Steuerung (hat Vorrang) |
| **Office-Override** | Binary Sensor | - | Deaktiviert Scheduler bei Arbeitstagen |

⚠️ **Wichtig**: Wenn "Scheduler aktivieren" = `true`, muss eine Zeitplan-Entity konfiguriert werden!

### Weitere Optionen

| Eingabe | Typ | Standard | Beschreibung |
|---------|-----|----------|--------------|
| **Heizung An/Aus** | Input Boolean | - | Globaler Winter-Modus Schalter |
| **Nächtliche Eco-Zeit** | Time | 01:00 | Zeit für Eco-Umschaltung im Guest Mode |

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
- Status wechselt von `Home` oder `Leaving` zu `Away`

**Aktion:** Thermostat auf HVAC-Modus `off` setzen

---

### Priorität 3: 😴 Sleep-Modus
**Bedingungen:**
- Status wechselt zu `Sleep`

**Aktion:** 
- Eco-Temperatur setzen
- Smart Temperature Setting: Temporär einschalten falls nötig

---

### Priorität 4: 🌙 Nächtliche Eco-Umschaltung
**Bedingungen:**
- Zeitpunkt = Konfigurierte "Nächtliche Eco-Zeit"

**Aktion:**
- Eco-Temperatur setzen
- Bei Status `Home`: HVAC-Modus `heat`
- Bei anderen Status: HVAC-Modus `off`

---

### Priorität 5: 🏠 Nach Hause kommen
**Bedingungen:**
- Status wechselt von `Away` zu `Coming Home` oder `Home`

**Aktion:** HVAC-Modus auf `heat` setzen

---

### Standard-Logik: 📅 Scheduler

**Komfort-Temperatur aktivieren wenn:**
- Scheduler ist enabled (`scheduler_enabled = true` oder `scheduler_toggle = on`)
- Schedule ist aktiv (`schedule_entity = on`)
- Office-Override ist nicht aktiv oder nicht konfiguriert
- Trigger war: `schedule_change`, `scheduler_toggle_change` oder `heating_toggle`

**Eco-Temperatur aktivieren wenn:**
- Scheduler ist enabled
- Schedule ist **nicht** aktiv (`schedule_entity = off`)
- Selbe Trigger-Bedingungen wie oben

---

## 🔧 Smart Temperature Setting

Ein besonderes Feature für Thermostaten, die im "Off"-Modus keine Temperaturen akzeptieren:

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
   - Wähle **Heizungssteuerung (V4.21)**
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
- Die Automation zeigt eine persistente Benachrichtigung bei Fehlkonfiguration

### Template-Fehler
**Problem:** `TypeError: unhashable type: 'list'`

**Lösung:** Dieses Blueprint verwendet `trigger_variables` und Template-Trigger zur sicheren Behandlung optionaler Entities. Stelle sicher, dass du die neueste Version (V4.21+) verwendest.

---

## 🔄 Updates & Changelog

### Version 4.21 (Aktuell)
- ✅ Template-Trigger mit `trigger_variables` für zuverlässiges Triggern
- ✅ Smart Temperature Setting für Thermostaten
- ✅ Vollständige Unterstützung optionaler Entities mit `default: []`
- ✅ Office-Override Feature
- ✅ Konfigurationsvalidierung mit Fehlerbenachrichtigungen

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
