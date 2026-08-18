"""ULID generation for sortable public identifiers."""

from __future__ import annotations

import hashlib
import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = ["0"] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


def new_ulid(*, timestamp_ms: int | None = None) -> str:
    """Return a canonical sortable ULID."""
    timestamp = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= timestamp < 2**48:
        raise ValueError("ULID timestamp is outside the 48-bit range")
    randomness = int.from_bytes(secrets.token_bytes(10))
    return _encode(timestamp, 10) + _encode(randomness, 16)


def deterministic_ulid(timestamp_ms: int, material: str) -> str:
    """Derive a stable ULID for deterministic fixtures and world events."""
    randomness = int.from_bytes(hashlib.sha256(material.encode()).digest()[:10])
    return _encode(timestamp_ms, 10) + _encode(randomness, 16)


def prefixed_ulid(prefix: str, *, timestamp_ms: int | None = None) -> str:
    """Return a readable prefix followed by a canonical ULID."""
    return f"{prefix}_{new_ulid(timestamp_ms=timestamp_ms)}"
