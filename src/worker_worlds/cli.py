"""Minimal Worker Worlds command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from worker_worlds.adapters import (
    LangGraphAdapter,
    OpenAIAgentsAdapter,
    refund_fake_runtime,
)
from worker_worlds.contracts import Scenario
from worker_worlds.database import DatabaseSettings, database_health, migrate
from worker_worlds.errors import WorkerWorldsError
from worker_worlds.grading import DeterministicGrader
from worker_worlds.postgres_world import PostgresWorld
from worker_worlds.protocols import WorkerAdapter, World
from worker_worlds.reporting import HtmlReporter, JsonReporter, JUnitReporter, SuiteJsonReporter
from worker_worlds.runner import Runner
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld
from worker_worlds.suite import SuiteRunner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worker-worlds")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one scenario")
    run.add_argument("scenario", type=Path)
    workers = ("stub", "langgraph-fake", "openai-agents-fake")
    run.add_argument("--worker", choices=workers, default="stub")
    run.add_argument("--world", choices=("stub", "postgres"), default="stub")
    run.add_argument("--database-url", help="explicit safe Worker Worlds Postgres URL")
    run.add_argument("--output", type=Path, default=Path(".worker-worlds/runs"))
    doctor = subparsers.add_parser("doctor", help="check runtime and database readiness")
    doctor.add_argument("--database-url")
    migration = subparsers.add_parser("migrate", help="apply database migrations")
    migration.add_argument("--database-url")
    suite = subparsers.add_parser("suite", help="run a scenario file or directory repeatedly")
    suite.add_argument("scenario_path", type=Path)
    suite.add_argument("--worker", choices=workers, default="stub")
    suite.add_argument("--world", choices=("stub", "postgres"), default="stub")
    suite.add_argument("--database-url")
    suite.add_argument("--repetitions", type=int, default=5)
    suite.add_argument("--concurrency", type=int, default=4)
    suite.add_argument("--provider-concurrency", type=int, default=2)
    suite.add_argument("--output", type=Path, default=Path(".worker-worlds/runs"))
    return parser


async def _run(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    world = StubWorld()
    if args.world == "postgres":
        settings = _database_settings(args.database_url)
        world = PostgresWorld(settings, str(scenario.id))  # type: ignore[assignment]
    record = await Runner(DeterministicGrader()).run(scenario, world, _worker(args.worker))
    reporter = JsonReporter(args.output)
    await reporter.report(record)
    print(f"run_id={record.id}")
    print(f"scenario_id={record.scenario_id}")
    print(f"initial_snapshot_hash={record.initial_snapshot_hash}")
    print(f"final_snapshot_hash={record.final_snapshot_hash}")
    print(f"terminal_reason={record.terminal_reason.value}")
    print(f"passed={str(record.passed).lower()}")
    print(
        "verdicts="
        + ",".join(f"{verdict.assertion_id}:{verdict.status.value}" for verdict in record.verdicts)
    )
    print(f"record={reporter.output_path}")
    print(f"cleanup_succeeded={str(record.cleanup_succeeded).lower()}")
    return 0 if record.passed else 1


def _database_settings(url: str | None) -> DatabaseSettings:
    if url:
        settings = DatabaseSettings(url=url)
        settings.validate()
        return settings
    return DatabaseSettings.from_env()


def _worker(name: str) -> WorkerAdapter:
    if name == "langgraph-fake":
        return LangGraphAdapter(refund_fake_runtime())
    if name == "openai-agents-fake":
        return OpenAIAgentsAdapter(refund_fake_runtime())
    return StubWorkerAdapter()


async def _suite(args: argparse.Namespace) -> int:
    paths = (
        sorted(args.scenario_path.glob("*.yaml"))
        if args.scenario_path.is_dir()
        else [args.scenario_path]
    )
    scenarios = [load_scenario(path) for path in paths]
    settings = _database_settings(args.database_url) if args.world == "postgres" else None

    def world_factory(scenario: Scenario) -> World:
        if settings is not None:
            return PostgresWorld(settings, str(scenario.id))
        return StubWorld()

    suite = await SuiteRunner(
        Runner(DeterministicGrader()),
        concurrency=args.concurrency,
        provider_concurrency=args.provider_concurrency,
    ).run(
        args.scenario_path.name,
        scenarios,
        world_factory,
        lambda: _worker(args.worker),
        repetitions=args.repetitions,
    )
    json_path = await SuiteJsonReporter().report(suite, args.output)
    junit_path = await JUnitReporter().report(suite, args.output)
    html_path = await HtmlReporter().report(suite, args.output)
    passed = sum(record.passed for record in suite.runs)
    print(f"suite_id={suite.id}")
    print(f"scenarios={len(suite.scenarios)}")
    print(f"repetitions={len(suite.runs)}")
    print(f"passed={passed}")
    print(f"failed={len(suite.runs) - passed}")
    print(f"json={json_path}")
    print(f"junit={junit_path}")
    print(f"html={html_path}")
    return 0 if passed == len(suite.runs) else 1


async def _doctor(args: argparse.Namespace) -> int:
    settings = _database_settings(args.database_url)
    healthy, message = await database_health(settings)
    print(f"python={platform_version()}")
    print(f"database={'ready' if healthy else 'unavailable'}: {message}")
    return 0 if healthy else 1


async def _migrate(args: argparse.Namespace) -> int:
    version = await migrate(_database_settings(args.database_url))
    print(f"migration_version={version}")
    return 0


def platform_version() -> str:
    """Return the executing Python version for doctor output."""
    return ".".join(str(part) for part in sys.version_info[:3])


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its process status."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return asyncio.run(_run(args))
        if args.command == "doctor":
            return asyncio.run(_doctor(args))
        if args.command == "migrate":
            return asyncio.run(_migrate(args))
        if args.command == "suite":
            return asyncio.run(_suite(args))
        return 2
    except WorkerWorldsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
