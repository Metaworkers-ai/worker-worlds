"""Reproducible local reset and reference-suite benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import time
from pathlib import Path
from typing import Any

from worker_worlds.database import DatabaseSettings
from worker_worlds.ids import prefixed_ulid
from worker_worlds.postgres_world import PostgresWorld
from worker_worlds.seeding import build_fixture


class SequentialFixtureWorld(PostgresWorld):
    """Pre-optimization fixture loader retained only for A/B benchmarking."""

    async def _insert_fixture(self, fixture: dict[str, list[dict[str, Any]]]) -> None:
        connection, schema = self._ready()
        specifications = (
            (
                "customers",
                f"INSERT INTO {schema}.customers VALUES($1,$2,$3,$4)",
                lambda v: (v["id"], v["email"], v["name"], self._as_datetime(v["created_at"])),
            ),
            (
                "products",
                f"INSERT INTO {schema}.products VALUES($1,$2,$3,$4,$5,$6)",
                lambda v: (
                    v["id"],
                    v["sku"],
                    v["title"],
                    v["price"]["amount_minor"],
                    v["price"]["currency"],
                    v["active"],
                ),
            ),
            (
                "inventory",
                f"INSERT INTO {schema}.inventory VALUES($1,$2,$3,$4,$5,$6)",
                lambda v: (
                    v["id"],
                    v["product_id"],
                    v["location"],
                    v["available"],
                    v["reserved"],
                    v["backorder_allowed"],
                ),
            ),
            (
                "orders",
                f"INSERT INTO {schema}.orders VALUES($1,$2,$3,$4,$5,$6,$7)",
                lambda v: (
                    v["id"],
                    v["customer_id"],
                    v["status"],
                    v["captured"]["amount_minor"],
                    v["refunded"]["amount_minor"],
                    v["captured"]["currency"],
                    self._as_datetime(v["created_at"]),
                ),
            ),
            (
                "line_items",
                f"INSERT INTO {schema}.line_items VALUES($1,$2,$3,$4,$5,$6)",
                lambda v: (
                    v["id"],
                    v["order_id"],
                    v["product_id"],
                    v["quantity"],
                    v["unit_price"]["amount_minor"],
                    v["unit_price"]["currency"],
                ),
            ),
            (
                "shipments",
                f"INSERT INTO {schema}.shipments VALUES($1,$2,$3,$4,$5)",
                lambda v: (
                    v["id"],
                    v["order_id"],
                    v["status"],
                    v["quantity"],
                    self._as_datetime(v["created_at"]),
                ),
            ),
            (
                "tickets",
                f"INSERT INTO {schema}.tickets VALUES($1,$2,$3,$4,$5,$6)",
                lambda v: (
                    v["id"],
                    v["customer_id"],
                    v["order_id"],
                    v["subject"],
                    v["status"],
                    self._as_datetime(v["created_at"]),
                ),
            ),
            (
                "facts",
                f"INSERT INTO {schema}.commerce_facts VALUES($1,$2::jsonb,$3)",
                lambda v: (v["key"], json.dumps(v["value"]), v["trust"]),
            ),
        )
        for name, statement, transform in specifications:
            for row in fixture[name]:
                await connection.execute(statement, *transform(row))


def percentile(samples: list[float], proportion: float) -> float:
    """Return nearest-rank percentile in milliseconds."""
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * proportion)))] * 1000


async def measure_world(
    settings: DatabaseSettings, seed: int, world_type: type[PostgresWorld] = PostgresWorld
) -> dict[str, float]:
    """Measure isolated reset, snapshot, and cleanup phases."""
    world = world_type(settings, f"benchmark.{seed}")
    started = time.perf_counter()
    await world.reset(seed=seed, run_id=prefixed_ulid("run"))
    reset = time.perf_counter() - started
    started = time.perf_counter()
    await world.snapshot()
    snapshot = time.perf_counter() - started
    started = time.perf_counter()
    await world.close()
    cleanup = time.perf_counter() - started
    return {"reset": reset, "snapshot": snapshot, "cleanup": cleanup}


async def main_async(output: Path) -> None:
    """Execute deterministic fixture and ten-run database measurements."""
    settings = DatabaseSettings.from_env()
    compile_samples: list[float] = []
    for seed in range(100):
        started = time.perf_counter()
        build_fixture("1.0", seed)
        compile_samples.append(time.perf_counter() - started)
    started = time.perf_counter()
    sequential = await asyncio.gather(
        *(measure_world(settings, seed, SequentialFixtureWorld) for seed in range(10))
    )
    sequential_total = time.perf_counter() - started
    started = time.perf_counter()
    worlds = await asyncio.gather(*(measure_world(settings, seed) for seed in range(10)))
    concurrent_total = time.perf_counter() - started
    result: dict[str, object] = {
        "profile": "local-postgres-10-concurrent-resets-v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "fixture_compile_ms": {
            "median": statistics.median(compile_samples) * 1000,
            "p95": percentile(compile_samples, 0.95),
        },
        "concurrent_total_ms": concurrent_total * 1000,
        "sequential_loader_concurrent_total_ms": sequential_total * 1000,
        "sequential_loader_reset_ms": {
            "median": statistics.median(item["reset"] for item in sequential) * 1000,
            "p95": percentile([item["reset"] for item in sequential], 0.95),
        },
    }
    for phase in ("reset", "snapshot", "cleanup"):
        samples = [item[phase] for item in worlds]
        result[f"{phase}_ms"] = {
            "median": statistics.median(samples) * 1000,
            "p95": percentile(samples, 0.95),
        }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))


def main() -> None:
    """Parse the output path and run the asynchronous benchmark."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark-reset.json"))
    args = parser.parse_args()
    asyncio.run(main_async(args.output))


if __name__ == "__main__":
    main()
