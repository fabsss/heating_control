from datetime import datetime, timezone

from analysis.pseudonym import build_pseudonyms
from collector.models import EntityRule

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fill(db):
    db.upsert_entity(EntityRule("person.svenja", "person", "shared"), None, NOW)
    db.upsert_entity(EntityRule("person.fabian", "person", "shared"), None, NOW)
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating"), "buero", NOW)
    db.upsert_user("45171d305dd5422ca9d0ecf755abbda7", "person.fabian", "Fabian Muster")
    db.upsert_user("0f1e2d3c4b5a69788796a5b4c3d2e1f0", "person.svenja", "Svenja")


def test_aliases_follow_sorted_entity_ids(db):
    fill(db)
    pseudo = build_pseudonyms(db)
    assert pseudo.persons == ("person.fabian", "person.svenja")
    assert pseudo.aliases == {"person_1": "person.fabian", "person_2": "person.svenja"}
    assert pseudo.alias_for_person("person.svenja") == "person_2"
    assert pseudo.person_for_alias("person_1") == "person.fabian"
    assert pseudo.person_for_alias("person_9") is None
    assert pseudo.alias_for_person("sensor.x") is None


def test_users_map_to_person_aliases(db):
    fill(db)
    pseudo = build_pseudonyms(db)
    assert pseudo.alias_for_user("45171d305dd5422ca9d0ecf755abbda7") == "person_1"
    assert pseudo.alias_for_user("unbekannt") is None


def test_display_name_is_local_only_and_falls_back_to_object_id(db):
    fill(db)
    db.upsert_entity(EntityRule("person.gast", "person", "shared"), None, NOW)
    pseudo = build_pseudonyms(db)
    assert pseudo.display_name("person.fabian") == "Fabian Muster"
    assert pseudo.display_name("person.gast") == "Gast"


def test_leaks_finds_names_ids_and_user_ids(db):
    fill(db)
    pseudo = build_pseudonyms(db)
    assert pseudo.leaks("person_1 kommt heim, Wohnzimmer") == []
    found = pseudo.leaks("FABIAN ist da, person.svenja auch, user 45171d305dd5422ca9d0ecf755abbda7")
    assert "fabian" in found and "svenja" in found
    assert "person.svenja" in found
    assert "45171d305dd5422ca9d0ecf755abbda7" in found


def test_to_rows_for_the_store(db):
    fill(db)
    assert build_pseudonyms(db).to_rows() == {
        "person_1": ("person.fabian", "person"),
        "person_2": ("person.svenja", "person"),
    }


def test_database_without_persons_gives_empty_mapping(db):
    pseudo = build_pseudonyms(db)
    assert pseudo.persons == () and pseudo.aliases == {} and pseudo.leaks("egal") == []
