from __future__ import annotations

import json

SYSTEM_PROMPT = """Du analysierst das Heizverhalten einer Wohnung und schlägst Regeln vor, die ein System später deterministisch ausführen kann.

Grundlage sind verdichtete Daten über manuelle Eingriffe der Bewohner in die Raumtemperatur (Sollwert oder Preset) samt Kontext. Personen sind pseudonymisiert (person_1, person_2, ...), Räume stehen im Klartext. Alle Zeiten sind lokale Zeiten, Wochentage zählen von 0 (Montag) bis 6 (Sonntag).

Deine Aufgabe: Finde wiederkehrende Muster und formuliere sie als Regelkandidaten. Ein Kandidat sagt: Wenn der Auslöser eintritt (und die Bedingungen gelten), sollte im Raum die Aktion ausgeführt werden.

Regeln für dich:
- Schlage nur Muster vor, die in den Daten belegt sind. Erfinde keine Zahlen und keine Gewohnheiten.
- Verwende ausschließlich die erlaubten Bausteine unten, mit genau diesen Schlüsseln und Werten.
- Verwende nur Räume, Presets und Wohnungszustände aus den Angaben der Nachricht.
- Gib höchstens {max_candidates} Kandidaten zurück, die aussagekräftigsten zuerst.
- Die Hypothese ist ein kurzer deutscher Satz (höchstens 200 Zeichen), warum das Muster auftritt. Sie darf keine Zahlen behaupten.
- Das System prüft jeden Kandidaten anschließend gegen die Daten. Trefferquoten und Zählungen gehören nicht in deine Antwort.

{vocabulary}

Antwortformat: ausschließlich ein JSON-Objekt, ohne Text davor oder danach:
{"candidates": [{"room": "<Raum>", "trigger": {...}, "conditions": [...], "action": {...}, "hypothesis": "<Satz>"}]}"""

MODE_INTROS = {
    "weekly": (
        "Modus: Wochenraster. Die Daten zeigen je Raum, Wochentag und Stunde, wie oft manuell eingegriffen "
        "wurde, welche Aktion am häufigsten war und welche Personen dabei zu Hause waren; dazu je Wochentag "
        "den durchschnittlichen Wohnungszustand und die Anwesenheit. Suche selbst nach Mustern."
    ),
    "clusters": (
        "Modus: Cluster. Die Daten zeigen gebündelte Eingriffe (gleicher Raum, gleiche Aktion, ähnliche "
        "Uhrzeit, gleiche Wochentagsgruppe) mit dem Kontext zum Zeitpunkt der Eingriffe als Anteile. "
        "Formuliere zu den Clustern die Ursache als Auslöser und Bedingungen."
    ),
}


def build_messages(mode: str, data: dict, vocabulary: str, max_candidates: int, *,
                   presets: list[str], flat_states: list[str], rooms: list[str]) -> list[dict]:
    if mode not in MODE_INTROS:
        raise ValueError(f"unbekannter Modus {mode!r}")
    system = SYSTEM_PROMPT.replace("{max_candidates}", str(max_candidates)).replace("{vocabulary}", vocabulary)
    user = "\n".join([
        MODE_INTROS[mode],
        f"Bekannte Räume: {', '.join(rooms) or '-'}",
        f"Bekannte Presets: {', '.join(presets) or '-'}",
        f"Bekannte Wohnungszustände: {', '.join(flat_states) or '-'}",
        "Daten (JSON):",
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    ])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
