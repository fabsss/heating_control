from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford-Base32 wie in ULIDs


def ulid_to_bytes(value: str) -> bytes:
    number = 0
    for char in value.upper():
        number = number * 32 + ALPHABET.index(char)
    return number.to_bytes(16, "big")


def bytes_to_ulid(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    chars = []
    for _ in range(26):
        chars.append(ALPHABET[number & 31])
        number >>= 5
    return "".join(reversed(chars))


def bytes_to_hex(raw: bytes) -> str:
    return raw.hex()
