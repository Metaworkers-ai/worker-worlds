import pytest

from worker_worlds.database import DatabaseSettings, validate_namespace
from worker_worlds.errors import InfrastructureError


def test_database_name_is_guarded() -> None:
    with pytest.raises(InfrastructureError, match="database name"):
        DatabaseSettings(url="postgresql://localhost/postgres").validate()


def test_cleanup_namespace_is_guarded() -> None:
    with pytest.raises(InfrastructureError, match="unsafe world namespace"):
        validate_namespace("public")
