from pathlib import Path

from worker_worlds.openapi_cli import generated_openapi


def test_openapi_is_checked_in_and_current() -> None:
    assert generated_openapi() == Path("schemas/v1/openapi.json").read_text(encoding="utf-8")
