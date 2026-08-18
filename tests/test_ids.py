import re

from worker_worlds.ids import deterministic_ulid, new_ulid


def test_ulid_format_and_sortability() -> None:
    earlier = new_ulid(timestamp_ms=1)
    later = new_ulid(timestamp_ms=2)
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", earlier)
    assert earlier < later


def test_deterministic_ulid_is_stable() -> None:
    assert deterministic_ulid(123, "fixture") == deterministic_ulid(123, "fixture")
