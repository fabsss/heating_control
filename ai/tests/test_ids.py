import pytest

from collector.ids import bytes_to_hex, bytes_to_ulid, ulid_to_bytes


@pytest.mark.parametrize("ulid", ["01M4TXPBEYE60APXTRFD3PKB80", "01M4TXPBESW8FDM38QJVTXJZS9"])
def test_ulid_roundtrip(ulid):
    raw = ulid_to_bytes(ulid)
    assert len(raw) == 16
    assert bytes_to_ulid(raw) == ulid


def test_hex_user_id_roundtrip():
    user_id = "45171d305dd5422ca9d0ecf755abbda7"
    assert bytes_to_hex(bytes.fromhex(user_id)) == user_id
