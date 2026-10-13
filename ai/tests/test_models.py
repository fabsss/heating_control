from datetime import datetime, timedelta, timezone

import pytest

from collector.models import attrs_hash, attrs_json, parse_utc, to_utc_iso


def test_to_utc_iso_has_fixed_microseconds_and_sorts():
    a = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    b = a + timedelta(microseconds=5)
    assert to_utc_iso(a) == "2026-01-01T10:00:00.000000+00:00"
    assert to_utc_iso(a) < to_utc_iso(b)


def test_to_utc_iso_converts_offset_to_utc():
    local = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone(timedelta(hours=1)))
    assert to_utc_iso(local) == "2026-01-01T10:00:00.000000+00:00"


def test_to_utc_iso_rejects_naive_datetime():
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 1, 1))


@pytest.mark.parametrize(
    "text",
    ["2026-01-01T10:00:00Z", "2026-01-01T11:00:00+01:00", "2026-01-01T10:00:00"],
)
def test_parse_utc_accepts_z_offsets_and_naive(text):
    assert parse_utc(text) == datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_attrs_json_is_stable_and_tolerates_odd_values():
    first = {"b": 1, "a": datetime(2026, 1, 1, tzinfo=timezone.utc), "n": {"x": [1, 2]}}
    second = {"n": {"x": [1, 2]}, "a": datetime(2026, 1, 1, tzinfo=timezone.utc), "b": 1}
    assert attrs_json(first) == attrs_json(second)
    assert attrs_hash(first) == attrs_hash(second)
    assert attrs_json({}) == "{}"
    assert attrs_hash({"a": 1}) != attrs_hash({"a": 2})
