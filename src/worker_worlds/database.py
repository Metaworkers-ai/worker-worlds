"""Safe explicit Postgres configuration and migration lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import weakref
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

from worker_worlds.errors import InfrastructureError

DEFAULT_DATABASE_URL = (
    "postgresql://worker_worlds:worker_worlds_local@127.0.0.1:55432/worker_worlds_dev"
)
_SAFE_DATABASE = re.compile(r"^worker_worlds_(?:dev|test(?:_[a-z0-9_]+)?)$")
_SAFE_NAMESPACE = re.compile(r"^ww_run_[0-9a-hjkmnp-tv-z]{26}$")


@dataclass(frozen=True)
class DatabaseSettings:
    """Validated database settings sourced from Worker Worlds variables."""

    url: str
    lease_seconds: float = 3600
    lease_renewal_seconds: float = 300
    snapshot_max_bytes: int = 10_000_000

    def __post_init__(self) -> None:
        """Validate direct construction as well as environment-based construction."""
        self.validate()

    @classmethod
    def from_env(cls, *, require_explicit: bool = False) -> DatabaseSettings:
        """Load settings, optionally forbidding the local development default."""
        raw = os.environ.get("WORKER_WORLDS_DATABASE_URL")
        if require_explicit and not raw:
            raise InfrastructureError(
                "WORKER_WORLDS_DATABASE_URL must be explicitly set for database tests"
            )
        url = raw or DEFAULT_DATABASE_URL
        settings = cls(
            url=url,
            lease_seconds=float(os.environ.get("WORKER_WORLDS_LEASE_SECONDS", "3600")),
            lease_renewal_seconds=float(
                os.environ.get("WORKER_WORLDS_LEASE_RENEWAL_SECONDS", "300")
            ),
            snapshot_max_bytes=int(os.environ.get("WORKER_WORLDS_SNAPSHOT_MAX_BYTES", "10000000")),
        )
        settings.validate()
        return settings

    @property
    def database_name(self) -> str:
        """Return the selected database name."""
        return urlparse(self.url).path.removeprefix("/")

    def validate(self) -> None:
        """Reject non-Postgres or broadly named cleanup targets."""
        parsed = urlparse(self.url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise InfrastructureError("database URL must be an explicit PostgreSQL URL")
        if not _SAFE_DATABASE.fullmatch(self.database_name):
            raise InfrastructureError(
                "database name must match worker_worlds_dev or worker_worlds_test[_suffix]"
            )
        if self.lease_seconds <= 0:
            raise InfrastructureError("WORKER_WORLDS_LEASE_SECONDS must be positive")
        if not 0 < self.lease_renewal_seconds < self.lease_seconds:
            raise InfrastructureError("lease renewal must be positive and shorter than the lease")
        if self.snapshot_max_bytes <= 0:
            raise InfrastructureError("snapshot size limit must be positive")


def validate_namespace(namespace: str) -> str:
    """Validate an exact per-run schema identifier before SQL interpolation."""
    if not _SAFE_NAMESPACE.fullmatch(namespace):
        raise InfrastructureError(f"refusing unsafe world namespace: {namespace!r}")
    return namespace


def migration_files() -> list[Path]:
    """Return numbered migrations in deterministic order."""
    root = Path(__file__).resolve().parent / "migrations"
    if not root.exists():
        root = Path.cwd() / "migrations"
    return sorted(root.glob("[0-9][0-9][0-9]_*.sql"))


async def connect(settings: DatabaseSettings) -> asyncpg.Connection[asyncpg.Record]:
    """Connect with a clear infrastructure error."""
    settings.validate()
    try:
        return await asyncpg.connect(settings.url, timeout=5)
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        raise InfrastructureError(
            "Postgres is unavailable; run 'docker compose up -d --wait postgres' "
            "or set WORKER_WORLDS_DATABASE_URL"
        ) from exc


# An asyncpg.Pool (and the Lock guarding pool creation) is bound to the event
# loop it was created under; reusing one from a different loop breaks asyncpg
# internals. Scope the cache per running loop -- keyed with a WeakKeyDictionary
# so a closed loop's entry (e.g. pytest-asyncio's function-scoped loop) is
# dropped automatically once nothing else references that loop -- rather than
# per process, so pools are still shared across concurrent callers *within*
# one loop, which is the actual concurrency win.
_state_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopPoolState] = (
    weakref.WeakKeyDictionary()
)


@dataclass
class _LoopPoolState:
    lock: asyncio.Lock
    pools: dict[str, asyncpg.Pool[asyncpg.Record]]


def _loop_state() -> _LoopPoolState:
    loop = asyncio.get_running_loop()
    state = _state_by_loop.get(loop)
    if state is None:
        state = _LoopPoolState(lock=asyncio.Lock(), pools={})
        _state_by_loop[loop] = state
    return state


async def get_pool(settings: DatabaseSettings) -> asyncpg.Pool[asyncpg.Record]:
    """Return a connection pool for these settings, shared within the current event loop.

    Callers that issue many short-lived queries under concurrency (durable
    suite-job orchestration in particular) previously opened and closed a
    fresh raw TCP connection per call. Under enough concurrent callers that
    connection-establishment churn is expensive everywhere and, on Windows'
    default ProactorEventLoop, can trip an asyncpg/event-loop interaction
    (``InvalidStateError`` deep inside ``_ProactorReadPipeTransport``) well
    before Postgres's own ``max_connections`` is reached. A small shared pool
    bounds real connection count regardless of logical concurrency.
    """
    settings.validate()
    state = _loop_state()
    pool = state.pools.get(settings.url)
    if pool is not None:
        return pool
    async with state.lock:
        pool = state.pools.get(settings.url)
        if pool is not None:
            return pool
        try:
            pool = await asyncpg.create_pool(
                settings.url, min_size=1, max_size=20, timeout=5, command_timeout=30
            )
        except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
            raise InfrastructureError(
                "Postgres is unavailable; run 'docker compose up -d --wait postgres' "
                "or set WORKER_WORLDS_DATABASE_URL"
            ) from exc
        if pool is None:  # pragma: no cover - asyncpg contract, defensive only
            raise InfrastructureError("failed to create Postgres connection pool")
        state.pools[settings.url] = pool
        return pool


async def close_all_pools() -> None:
    """Close every pool cached for the current event loop; for tests and shutdown."""
    state = _loop_state()
    async with state.lock:
        pools = list(state.pools.values())
        state.pools.clear()
    for pool in pools:
        await pool.close()


async def migrate(settings: DatabaseSettings) -> str:
    """Apply checksummed forward-only SQL migrations."""
    settings.validate()
    pool = await get_pool(settings)
    connection = await pool.acquire()
    try:
        await connection.execute("SELECT pg_advisory_lock(hashtext('worker_worlds:migrations'))")
        await connection.execute(
            "CREATE SCHEMA IF NOT EXISTS worker_worlds; "
            "CREATE TABLE IF NOT EXISTS worker_worlds.schema_migrations ("
            "version integer PRIMARY KEY, checksum text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for path in migration_files():
            version = int(path.name.split("_", maxsplit=1)[0])
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            existing = await connection.fetchval(
                "SELECT checksum FROM worker_worlds.schema_migrations WHERE version=$1", version
            )
            if existing is not None and existing != checksum:
                raise InfrastructureError(f"migration checksum mismatch for version {version}")
            if existing is None:
                async with connection.transaction():
                    await connection.execute(sql)
                    await connection.execute(
                        "INSERT INTO worker_worlds.schema_migrations"
                        "(version, checksum) VALUES($1,$2)",
                        version,
                        checksum,
                    )
        latest = await connection.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM worker_worlds.schema_migrations"
        )
        return f"{int(latest):03d}"
    finally:
        with suppress(asyncpg.PostgresError):
            await connection.execute(
                "SELECT pg_advisory_unlock(hashtext('worker_worlds:migrations'))"
            )
        await pool.release(connection)


async def database_health(settings: DatabaseSettings) -> tuple[bool, str]:
    """Check connectivity and migration readiness.

    Uses the shared pool rather than a fresh raw connection: this is called on
    every dashboard page load (often alongside several other concurrent API
    requests), and a long-running API process repeatedly opening one-off raw
    connections is exactly the churn that can trip the Windows/asyncpg
    connection-burst issue documented on ``get_pool``.
    """
    try:
        pool = await get_pool(settings)
    except InfrastructureError as exc:
        return False, str(exc)
    try:
        async with pool.acquire() as connection:
            version = await connection.fetchval("SHOW server_version")
            table = await connection.fetchval(
                "SELECT to_regclass('worker_worlds.schema_migrations')::text"
            )
            if table is None:
                return False, f"Postgres {version} reachable; migrations are not applied"
            applied = int(
                await connection.fetchval(
                    "SELECT COALESCE(MAX(version),0) FROM worker_worlds.schema_migrations"
                )
            )
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        return False, (
            "Postgres is unavailable; run 'docker compose up -d --wait postgres' "
            f"or set WORKER_WORLDS_DATABASE_URL ({type(exc).__name__})"
        )
    expected = max(int(path.name[:3]) for path in migration_files())
    if applied != expected:
        return False, f"Postgres reachable; migration {applied:03d}, expected {expected:03d}"
    return True, (
        f"Postgres {version} reachable ({settings.database_name}), migration {applied:03d}"
    )


async def cleanup_abandoned(settings: DatabaseSettings) -> int:
    """Drop only expired, inactive-safe run schemas from the lease registry."""
    connection = await connect(settings)
    cleaned = 0
    try:
        while True:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT run_id, namespace FROM worker_worlds.run_leases "
                    "WHERE active AND expires_at < $1 ORDER BY expires_at "
                    "FOR UPDATE SKIP LOCKED LIMIT 1",
                    datetime.now(UTC),
                )
                if row is None:
                    break
                namespace = validate_namespace(str(row["namespace"]))
                await connection.execute(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
                await connection.execute(
                    "DELETE FROM worker_worlds.run_leases WHERE run_id=$1", row["run_id"]
                )
            cleaned += 1
        return cleaned
    finally:
        await connection.close()
