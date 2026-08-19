"""Generate and verify checked-in JSON contract schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from worker_worlds.config import WorkerWorldsConfig
from worker_worlds.contracts import (
    AuthorizationContext,
    BaselineManifest,
    ComparisonConfig,
    ComparisonReport,
    ComparisonVerdict,
    DiffResult,
    DistributionSummary,
    EvidenceReference,
    FailureModeDelta,
    OutcomeSignature,
    RunRecord,
    Scenario,
    ScenarioAggregate,
    ScenarioComparison,
    ScheduledInjection,
    SuiteRecord,
    ToolCall,
    ToolResult,
    ToolSpec,
    Verdict,
    WorkerTurn,
    WorldEvent,
    WorldSnapshot,
)

SCHEMA_DIRECTORY = Path("schemas/v1")
MODELS: tuple[type[BaseModel], ...] = (
    Scenario,
    ScheduledInjection,
    WorldEvent,
    WorldSnapshot,
    AuthorizationContext,
    ToolSpec,
    ToolCall,
    ToolResult,
    WorkerTurn,
    RunRecord,
    Verdict,
    DiffResult,
    EvidenceReference,
    ScenarioAggregate,
    SuiteRecord,
    BaselineManifest,
    ComparisonConfig,
    OutcomeSignature,
    DistributionSummary,
    FailureModeDelta,
    ScenarioComparison,
    ComparisonVerdict,
    ComparisonReport,
    WorkerWorldsConfig,
)


def generated_schemas() -> dict[str, str]:
    """Return canonical schema text keyed by stable filename."""
    return {
        f"{model.__name__}.schema.json": json.dumps(
            model.model_json_schema(), sort_keys=True, indent=2, ensure_ascii=False
        )
        + "\n"
        for model in MODELS
    }


def generate(directory: Path = SCHEMA_DIRECTORY) -> None:
    """Write all current public schemas."""
    directory.mkdir(parents=True, exist_ok=True)
    for filename, content in generated_schemas().items():
        (directory / filename).write_text(content, encoding="utf-8")


def check(directory: Path = SCHEMA_DIRECTORY) -> list[str]:
    """Return filenames that are missing, stale, or unexpectedly present."""
    expected = generated_schemas()
    actual_files = {path.name for path in directory.glob("*.schema.json")}
    drift = [
        filename
        for filename, content in expected.items()
        if not (directory / filename).exists()
        or (directory / filename).read_text(encoding="utf-8") != content
    ]
    drift.extend(sorted(actual_files - expected.keys()))
    return sorted(set(drift))


def main(argv: list[str] | None = None) -> int:
    """Run schema generation or drift detection."""
    parser = argparse.ArgumentParser(prog="worker-worlds-schema")
    parser.add_argument("command", choices=("generate", "check"))
    args = parser.parse_args(argv)
    if args.command == "generate":
        generate()
        print(f"generated {len(MODELS)} schemas in {SCHEMA_DIRECTORY}")
        return 0
    drift = check()
    if drift:
        print("schema drift detected: " + ", ".join(drift))
        return 1
    print(f"schemas current: {len(MODELS)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
