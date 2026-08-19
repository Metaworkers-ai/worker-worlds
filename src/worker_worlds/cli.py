"""Minimal Worker Worlds command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import Never

import yaml
from pydantic import ValidationError

from worker_worlds.adapters import (
    LangGraphAdapter,
    OpenAIAgentsAdapter,
    refund_fake_runtime,
)
from worker_worlds.baselines import create_baseline, inspect_baseline, load_baseline, load_suite
from worker_worlds.comparison import compare_suites
from worker_worlds.config import load_config
from worker_worlds.contracts import ComparisonConfig, Scenario
from worker_worlds.database import DatabaseSettings, database_health, migrate, migration_files
from worker_worlds.errors import WorkerWorldsError
from worker_worlds.grading import DeterministicGrader
from worker_worlds.postgres_world import PostgresWorld
from worker_worlds.protocols import WorkerAdapter, World
from worker_worlds.reporting import (
    ComparisonReporter,
    HtmlReporter,
    JsonReporter,
    JUnitReporter,
    SuiteJsonReporter,
)
from worker_worlds.review import generate_review_package
from worker_worlds.runner import Runner
from worker_worlds.scenario_release import (
    export_scenarios,
    validate_scenario_directory,
    write_coverage_report,
)
from worker_worlds.scenarios import load_scenario
from worker_worlds.schema_cli import check as check_schemas
from worker_worlds.schema_cli import generate as generate_schemas
from worker_worlds.stubs import StubWorkerAdapter, StubWorld
from worker_worlds.suite import SuiteRunner


class CliParser(argparse.ArgumentParser):
    """Argument parser that returns typed CLI errors instead of exiting internally."""

    def error(self, message: str) -> Never:
        """Raise a stable validation error handled by the CLI boundary."""
        raise WorkerWorldsError(f"invalid arguments: {message}")


def _parser() -> argparse.ArgumentParser:
    parser = CliParser(prog="worker-worlds")
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    parser.add_argument("--quiet", action="store_true", help="suppress ordinary output")
    parser.add_argument("--verbose", action="store_true", help="emit safe diagnostics to stderr")
    parser.add_argument(
        "--dry-run", action="store_true", help="validate without persistent effects"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing mutable artifacts"
    )
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
    suite.add_argument("--artifact-mode", choices=("auto", "full", "summary"), default="auto")
    baseline = subparsers.add_parser("baseline", help="manage immutable local baselines")
    baseline_commands = baseline.add_subparsers(dest="baseline_command", required=True)
    baseline_create = baseline_commands.add_parser("create")
    baseline_create.add_argument("--from", dest="source", type=Path, required=True)
    baseline_create.add_argument("--name", required=True)
    baseline_create.add_argument("--output", type=Path, required=True)
    baseline_list = baseline_commands.add_parser("list")
    baseline_list.add_argument("--directory", type=Path, required=True)
    baseline_inspect = baseline_commands.add_parser("inspect")
    baseline_inspect.add_argument("--baseline", type=Path, required=True)
    compare = subparsers.add_parser("compare", help="compare baseline and candidate suites")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--config", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--split-threshold-bytes", type=int, default=500_000)
    scenario = subparsers.add_parser("scenario", help="validate and export release scenarios")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    scenario_validate = scenario_commands.add_parser("validate", help="validate a YAML directory")
    scenario_validate.add_argument("directory", type=Path)
    scenario_export = scenario_commands.add_parser("export", help="export canonical release YAML")
    scenario_export.add_argument(
        "directory", type=Path, nargs="?", default=Path("scenarios/release")
    )
    scenario_export.add_argument("--check", action="store_true", help="fail on checked-in drift")
    scenario_coverage = scenario_commands.add_parser("coverage", help="write coverage diagnostics")
    scenario_coverage.add_argument(
        "directory", type=Path, nargs="?", default=Path("scenarios/release")
    )
    scenario_coverage.add_argument(
        "--output", type=Path, default=Path("artifacts/scenario-coverage.json")
    )
    scenario_review = scenario_commands.add_parser("review", help="generate domain-review package")
    scenario_review.add_argument(
        "directory", type=Path, nargs="?", default=Path("scenarios/release")
    )
    scenario_review.add_argument("--output", type=Path, default=Path("docs/domain-review"))
    subparsers.add_parser("version", help="print the Worker Worlds version")
    config = subparsers.add_parser("config", help="inspect validated effective configuration")
    config.add_argument("config_command", choices=("show",))
    config.add_argument("--config", type=Path)
    schema = subparsers.add_parser("schema", help="generate or check public JSON Schemas")
    schema.add_argument("schema_command", choices=("generate", "check"))
    schema.add_argument("--directory", type=Path, default=Path("schemas/v1"))
    report = subparsers.add_parser("report", help="validate portable report artifacts")
    report.add_argument("path", type=Path)
    return parser


def _emit(args: argparse.Namespace, payload: Mapping[str, object], lines: list[str]) -> None:
    """Emit stable machine output or human output without mixing modes."""
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif not args.quiet:
        print("\n".join(lines))
    if args.verbose:
        identifiers = " ".join(
            f"{key}={value}" for key, value in payload.items() if key.endswith("_id")
        )
        print(
            f"diagnostic component=cli command={args.command} {identifiers}".rstrip(),
            file=sys.stderr,
        )


def _success(args: argparse.Namespace, **payload: object) -> dict[str, object]:
    return {"status": "ok", "exit_code": 0, "dry_run": bool(args.dry_run), **payload}


def _require_replaceable(path: Path, args: argparse.Namespace) -> None:
    """Refuse implicit replacement of an existing artifact tree."""
    if path.exists() and any(path.iterdir()) and not args.overwrite:
        raise WorkerWorldsError(f"output already exists and is not empty: {path}; use --overwrite")


async def _run(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    if args.dry_run:
        payload = _success(
            args,
            scenario_id=str(scenario.id),
            worker=args.worker,
            world=args.world,
            intended_output=str(args.output),
        )
        _emit(
            args, payload, [f"validated scenario={scenario.id}", f"intended_output={args.output}"]
        )
        return 0
    world = StubWorld()
    if args.world == "postgres":
        settings = _database_settings(args.database_url)
        world = PostgresWorld(settings, str(scenario.id))  # type: ignore[assignment]
    record = await Runner(DeterministicGrader()).run(scenario, world, _worker(args.worker))
    reporter = JsonReporter(args.output)
    await reporter.report(record)
    exit_code = 0 if record.passed else 1
    verdict_lines = [f"{item.assertion_id}:{item.status.value}" for item in record.verdicts]
    result_payload: dict[str, object] = {
        "status": "passed" if record.passed else "failed",
        "exit_code": exit_code,
        "dry_run": False,
        "run_id": str(record.id),
        "scenario_id": str(record.scenario_id),
        "initial_snapshot_hash": record.initial_snapshot_hash,
        "final_snapshot_hash": record.final_snapshot_hash,
        "terminal_reason": record.terminal_reason.value,
        "passed": record.passed,
        "verdicts": verdict_lines,
        "record": str(reporter.output_path),
        "cleanup_succeeded": record.cleanup_succeeded,
    }
    _emit(
        args,
        result_payload,
        [
            f"run_id={record.id}",
            f"scenario_id={record.scenario_id}",
            f"initial_snapshot_hash={record.initial_snapshot_hash}",
            f"final_snapshot_hash={record.final_snapshot_hash}",
            f"terminal_reason={record.terminal_reason.value}",
            f"passed={str(record.passed).lower()}",
            "verdicts=" + ",".join(verdict_lines),
            f"record={reporter.output_path}",
            f"cleanup_succeeded={str(record.cleanup_succeeded).lower()}",
        ],
    )
    return exit_code


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
    if args.dry_run:
        payload = _success(
            args,
            scenarios=len(scenarios),
            repetitions=len(scenarios) * args.repetitions,
            intended_output=str(args.output),
        )
        _emit(
            args, payload, [f"scenarios_valid={len(scenarios)}", f"intended_output={args.output}"]
        )
        return 0
    _require_replaceable(args.output, args)
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
    html_path = await HtmlReporter(artifact_mode=args.artifact_mode).report(suite, args.output)
    passed = sum(record.passed for record in suite.runs)
    exit_code = 0 if passed == len(suite.runs) else 1
    payload = {
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "dry_run": False,
        "suite_id": str(suite.id),
        "scenarios": len(suite.scenarios),
        "repetitions": len(suite.runs),
        "passed": passed,
        "failed": len(suite.runs) - passed,
        "artifacts": {"json": str(json_path), "junit": str(junit_path), "html": str(html_path)},
    }
    _emit(
        args,
        payload,
        [
            f"suite_id={suite.id}",
            f"scenarios={len(suite.scenarios)}",
            f"repetitions={len(suite.runs)}",
            f"passed={passed}",
            f"failed={len(suite.runs) - passed}",
            f"json={json_path}",
            f"junit={junit_path}",
            f"html={html_path}",
        ],
    )
    return exit_code


async def _doctor(args: argparse.Namespace) -> int:
    settings = _database_settings(args.database_url)
    healthy, message = await database_health(settings)
    exit_code = 0 if healthy else 1
    payload = {
        "status": "ready" if healthy else "unavailable",
        "exit_code": exit_code,
        "dry_run": bool(args.dry_run),
        "python": platform_version(),
        "database": message,
    }
    _emit(
        args,
        payload,
        [
            f"python={platform_version()}",
            f"database={'ready' if healthy else 'unavailable'}: {message}",
        ],
    )
    return exit_code


async def _migrate(args: argparse.Namespace) -> int:
    settings = _database_settings(args.database_url)
    if args.dry_run:
        intended = [path.name for path in migration_files()]
        payload = _success(
            args,
            intended_migrations=intended,
            database="[REDACTED]",
        )
        _emit(
            args,
            payload,
            ["database=[REDACTED]", "migrations=" + ",".join(intended)],
        )
        return 0
    version = await migrate(settings)
    payload = _success(args, migration_version=version)
    _emit(args, payload, [f"migration_version={version}"])
    return 0


def _baseline(args: argparse.Namespace) -> int:
    if args.baseline_command == "create":
        suite = load_suite(args.source)
        if args.dry_run:
            payload = _success(
                args, name=args.name, suite_id=str(suite.id), intended_output=str(args.output)
            )
            _emit(
                args,
                payload,
                [
                    f"validated_suite={suite.id}",
                    f"intended_baseline={args.output / (args.name + '.json')}",
                ],
            )
            return 0
        path = create_baseline(args.source, args.name, args.output)
        payload = _success(args, baseline=str(path))
        _emit(args, payload, [f"baseline={path}"])
        return 0
    if args.baseline_command == "list":
        paths = [str(path) for path in sorted(args.directory.glob("*.json"))]
        payload = _success(args, baselines=paths)
        _emit(args, payload, paths)
        return 0
    data = inspect_baseline(args.baseline)
    payload = _success(args, baseline=data)
    _emit(args, payload, [json.dumps(data, sort_keys=True, indent=2)])
    return 0


async def _compare(args: argparse.Namespace) -> int:
    baseline = load_baseline(args.baseline)
    candidate = load_suite(args.candidate)
    config = ComparisonConfig()
    if args.config:
        raw = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
        config_data = raw.get("comparison", raw) if isinstance(raw, dict) else raw
        config = ComparisonConfig.model_validate(config_data)
    report = compare_suites(
        baseline.suite,
        candidate,
        config,
        baseline_source=str(args.baseline),
        candidate_source=str(args.candidate),
    )
    if args.dry_run:
        payload = _success(
            args,
            comparison_id=str(report.id),
            gate="pass" if report.verdict.passed else "fail",
            intended_output=str(args.output),
        )
        _emit(
            args,
            payload,
            [
                f"comparison_id={report.id}",
                f"gate={'pass' if report.verdict.passed else 'fail'}",
                f"intended_output={args.output}",
            ],
        )
        return 0
    _require_replaceable(args.output, args)
    paths = await ComparisonReporter(split_threshold_bytes=args.split_threshold_bytes).report(
        report, args.output
    )
    exit_code = 0 if report.verdict.passed else 1
    delta = sum(item.pass_rate_delta for item in report.scenarios)
    payload = {
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "dry_run": False,
        "comparison_id": str(report.id),
        "gate": "pass" if report.verdict.passed else "fail",
        "new_critical": report.verdict.new_critical,
        "new_high": report.verdict.new_high,
        "pass_rate_delta": delta,
        "artifacts": {"json": str(paths[0]), "junit": str(paths[1]), "html": str(paths[2])},
    }
    _emit(
        args,
        payload,
        [
            f"comparison_id={report.id}",
            f"gate={payload['gate']}",
            f"new_critical={report.verdict.new_critical}",
            f"new_high={report.verdict.new_high}",
            f"pass_rate_delta={delta:.6f}",
            f"json={paths[0]}",
            f"junit={paths[1]}",
            f"html={paths[2]}",
        ],
    )
    return exit_code


def _scenario(args: argparse.Namespace) -> int:
    if args.scenario_command == "export":
        if (
            not args.check
            and not args.dry_run
            and args.directory.exists()
            and any(args.directory.glob("*.yaml"))
            and not args.overwrite
        ):
            raise WorkerWorldsError(
                f"release scenarios already exist: {args.directory}; use --overwrite"
            )
        if args.dry_run:
            count, drift = export_scenarios(args.directory, check=True)
            payload = _success(
                args, scenarios=count, drift=drift, intended_output=str(args.directory)
            )
            _emit(
                args,
                payload,
                [
                    f"scenarios_valid={count}",
                    f"drift={len(drift)}",
                    f"intended_output={args.directory}",
                ],
            )
            return 0
        count, drift = export_scenarios(args.directory, check=args.check)
        if drift:
            if not args.quiet:
                print("scenario drift: " + ", ".join(drift), file=sys.stderr)
            if args.json:
                print(
                    json.dumps(
                        {
                            "status": "drift",
                            "exit_code": 1,
                            "dry_run": False,
                            "scenarios": count,
                            "drift": drift,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            return 1
        payload = _success(args, scenarios=count, operation="check" if args.check else "export")
        _emit(args, payload, [f"scenarios={'current' if args.check else 'exported'}: {count}"])
        return 0
    scenarios = validate_scenario_directory(args.directory)
    if args.scenario_command == "review":
        if args.dry_run:
            payload = _success(
                args, scenarios_valid=len(scenarios), intended_output=str(args.output)
            )
            _emit(
                args,
                payload,
                [f"scenarios_valid={len(scenarios)}", f"intended_output={args.output}"],
            )
            return 0
        if args.output.exists() and any(args.output.iterdir()) and not args.overwrite:
            raise WorkerWorldsError(f"review package exists: {args.output}; use --overwrite")
        paths = generate_review_package(scenarios, args.output)
        payload = _success(
            args, scenarios_valid=len(scenarios), artifacts=[str(path) for path in paths]
        )
        _emit(args, payload, [f"review_artifact={path}" for path in paths])
        return 0
    if args.scenario_command == "coverage":
        if not args.dry_run:
            if args.output.exists() and not args.overwrite:
                raise WorkerWorldsError(f"coverage artifact exists: {args.output}; use --overwrite")
            write_coverage_report(scenarios, args.output)
        payload = _success(args, scenarios_valid=len(scenarios), coverage=str(args.output))
        _emit(args, payload, [f"coverage={args.output}", f"scenarios_valid={len(scenarios)}"])
        return 0
    payload = _success(args, scenarios_valid=len(scenarios))
    _emit(args, payload, [f"scenarios_valid={len(scenarios)}"])
    return 0


def platform_version() -> str:
    """Return the executing Python version for doctor output."""
    return ".".join(str(part) for part in sys.version_info[:3])


def _schema(args: argparse.Namespace) -> int:
    drift = check_schemas(args.directory)
    if args.schema_command == "check" or args.dry_run:
        exit_code = 1 if drift and args.schema_command == "check" else 0
        payload: dict[str, object] = {
            "status": "current" if not drift else "drift",
            "exit_code": exit_code,
            "dry_run": bool(args.dry_run),
            "directory": str(args.directory),
            "drift": drift,
        }
        _emit(
            args,
            payload,
            [
                f"schemas={'current' if not drift else 'drift'}: {len(drift)}",
                *(f"drift={item}" for item in drift),
            ],
        )
        return exit_code
    if args.directory.exists() and any(args.directory.glob("*.schema.json")) and not args.overwrite:
        raise WorkerWorldsError(f"schemas already exist: {args.directory}; use --overwrite")
    generate_schemas(args.directory)
    payload = _success(
        args, directory=str(args.directory), schemas=len(check_schemas(args.directory))
    )
    _emit(args, payload, [f"schemas_generated={args.directory}"])
    return 0


def _report(args: argparse.Namespace) -> int:
    path: Path = args.path
    if not path.exists():
        raise WorkerWorldsError(f"report artifact does not exist: {path}")
    checked: list[str] = []
    candidates = list(path.rglob("*")) if path.is_dir() else [path]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if candidate.suffix == ".json":
            json.loads(candidate.read_text(encoding="utf-8"))
            checked.append(str(candidate))
        elif candidate.suffix == ".xml":
            ET.parse(candidate)
            checked.append(str(candidate))
        elif candidate.suffix == ".html":
            text = candidate.read_text(encoding="utf-8")
            if '<html lang="en">' not in text:
                raise WorkerWorldsError(f"HTML report lacks language metadata: {candidate}")
            checked.append(str(candidate))
    payload = _success(args, artifacts_valid=len(checked), path=str(path))
    _emit(args, payload, [f"artifacts_valid={len(checked)}", f"path={path}"])
    return 0


def _extract_global_flags(argv: list[str]) -> tuple[list[str], dict[str, bool]]:
    """Accept global boolean flags before or after any subcommand."""
    names = {
        "--json": "json",
        "--quiet": "quiet",
        "--verbose": "verbose",
        "--dry-run": "dry_run",
        "--overwrite": "overwrite",
    }
    flags = {value: False for value in names.values()}
    cleaned: list[str] = []
    for item in argv:
        key = names.get(item)
        if key is None:
            cleaned.append(item)
        else:
            flags[key] = True
    if flags["quiet"] and flags["verbose"]:
        raise WorkerWorldsError("--quiet and --verbose are mutually exclusive")
    return cleaned, flags


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its process status."""
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        cleaned, flags = _extract_global_flags(raw)
        args = _parser().parse_args(cleaned)
        for name, value in flags.items():
            setattr(args, name, value)
        if args.command == "run":
            return asyncio.run(_run(args))
        if args.command == "doctor":
            return asyncio.run(_doctor(args))
        if args.command == "migrate":
            return asyncio.run(_migrate(args))
        if args.command == "suite":
            return asyncio.run(_suite(args))
        if args.command == "baseline":
            return _baseline(args)
        if args.command == "compare":
            return asyncio.run(_compare(args))
        if args.command == "scenario":
            return _scenario(args)
        if args.command == "version":
            from worker_worlds import __version__

            payload = _success(args, version=__version__)
            _emit(args, payload, [__version__])
            return 0
        if args.command == "config":
            config, source = load_config(args.config)
            payload = _success(
                args,
                source=str(source) if source else None,
                hash=config.configuration_hash(),
                config=config.redacted(),
            )
            _emit(args, payload, [json.dumps(payload, sort_keys=True, indent=2)])
            return 0
        if args.command == "schema":
            return _schema(args)
        if args.command == "report":
            return _report(args)
        return 2
    except (
        WorkerWorldsError,
        ValidationError,
        ValueError,
        OSError,
        json.JSONDecodeError,
        ET.ParseError,
    ) as exc:
        json_mode = "--json" in raw
        quiet = "--quiet" in raw
        payload = {
            "status": "error",
            "exit_code": 2,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        if json_mode:
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        elif not quiet or str(exc):
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
