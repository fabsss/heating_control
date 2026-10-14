from __future__ import annotations

from dataclasses import dataclass

from collector.db import Database


@dataclass(frozen=True)
class Pseudonyms:
    """Zuordnung Person -> Alias. Klarnamen verlassen den Rechner nie."""

    persons: tuple[str, ...]                  # Entity-IDs; Reihenfolge = person_1, person_2, ...
    display_names: dict[str, str]             # entity_id -> Anzeigename (nur lokal)
    user_to_person: dict[str, str]            # user_id -> person-Entity-ID

    @property
    def aliases(self) -> dict[str, str]:
        return {f"person_{number}": entity_id for number, entity_id in enumerate(self.persons, start=1)}

    def alias_for_person(self, entity_id: str) -> str | None:
        for alias, entity in self.aliases.items():
            if entity == entity_id:
                return alias
        return None

    def person_for_alias(self, alias: str) -> str | None:
        return self.aliases.get(alias)

    def alias_for_user(self, user_id: str) -> str | None:
        person = self.user_to_person.get(user_id)
        return self.alias_for_person(person) if person else None

    def display_name(self, entity_id: str) -> str:
        return self.display_names.get(entity_id) or entity_id.split(".", 1)[-1].title()

    def _sensitive_terms(self) -> set[str]:
        terms: set[str] = set(self.user_to_person)
        for entity_id in self.persons:
            terms.add(entity_id)
            terms.add(entity_id.split(".", 1)[-1])
            name = self.display_names.get(entity_id)
            if name:
                terms.add(name)
                terms.update(word for word in name.split() if len(word) >= 3)
        return {term for term in terms if len(term) >= 3}

    def leaks(self, text: str) -> list[str]:
        lowered = text.lower()
        return sorted({term.lower() for term in self._sensitive_terms() if term.lower() in lowered})

    def to_rows(self) -> dict[str, tuple[str, str]]:
        return {alias: (entity_id, "person") for alias, entity_id in self.aliases.items()}


def build_pseudonyms(db: Database) -> Pseudonyms:
    persons = tuple(sorted(
        row["entity_id"] for row in db.conn.execute("SELECT entity_id FROM entities WHERE kind = 'person'")))
    display_names: dict[str, str] = {}
    user_to_person: dict[str, str] = {}
    for row in db.conn.execute("SELECT user_id, person_entity, name FROM users"):
        if row["person_entity"]:
            user_to_person[row["user_id"]] = row["person_entity"]
            if row["name"]:
                display_names[row["person_entity"]] = row["name"]
    return Pseudonyms(persons, display_names, user_to_person)
