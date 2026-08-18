from pathlib import Path

from worker_worlds.schema_cli import check


def test_checked_in_schemas_match_models() -> None:
    assert check() == []


def test_source_and_packaged_migrations_match() -> None:
    for path in Path("migrations").glob("*.sql"):
        packaged = Path("src/worker_worlds/migrations") / path.name
        assert packaged.read_bytes() == path.read_bytes()
