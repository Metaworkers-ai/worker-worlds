"""Immutable local baseline artifact management."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from worker_worlds import __version__
from worker_worlds.contracts import BaselineManifest, SuiteRecord
from worker_worlds.errors import InfrastructureError
from worker_worlds.policies import POLICY_VERSION


def load_suite(path: Path) -> SuiteRecord:
    """Load and validate a canonical suite artifact."""
    try:
        return SuiteRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InfrastructureError(f"unable to load suite artifact {path}: {exc}") from exc


def create_baseline(suite_path: Path, name: str, directory: Path) -> Path:
    """Create a bundled content-addressed baseline without mutating its suite."""
    suite = load_suite(suite_path)
    scenario_hashes = {str(record.scenario_id): record.scenario_hash or "" for record in suite.runs}
    suite_hash = suite.canonical_hash()
    payload_hash = hashlib.sha256(f"{name}:{suite_hash}".encode()).hexdigest()
    manifest = BaselineManifest(
        name=name,
        created_at=suite.ended_at,
        suite_hash=suite_hash,
        content_hash=payload_hash,
        source=suite_path.name,
        package_version=__version__,
        world=suite.world,
        worker=suite.worker,
        worker_version=suite.worker_version,
        adapter_names=tuple(sorted({record.adapter for record in suite.runs})),
        scenario_hashes=dict(sorted(scenario_hashes.items())),
        policy_versions={"commerce": POLICY_VERSION},
        suite=suite,
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{payload_hash[:12]}.json"
    if path.exists() and path.read_text(encoding="utf-8").strip() != manifest.canonical_json():
        raise InfrastructureError(f"immutable baseline collision at {path}")
    path.write_text(manifest.canonical_json() + "\n", encoding="utf-8")
    alias = directory / f"{name}.json"
    if alias.exists() and alias.read_text(encoding="utf-8").strip() != manifest.canonical_json():
        raise InfrastructureError(
            f"baseline name {name!r} is immutable; choose a new name for changed content"
        )
    alias.write_text(manifest.canonical_json() + "\n", encoding="utf-8")
    return path


def load_baseline(path: Path) -> BaselineManifest:
    """Load a baseline and verify its bundled suite and content identities."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        manifest = BaselineManifest.model_validate(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise InfrastructureError(f"unable to load baseline {path}: {exc}") from exc
    raw_suite = raw.get("suite") if isinstance(raw, dict) else None
    if not isinstance(raw_suite, dict):
        raise InfrastructureError("baseline suite payload is missing")
    raw_suite_json = json.dumps(
        raw_suite, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    raw_suite_hash = hashlib.sha256(raw_suite_json.encode()).hexdigest()
    if raw_suite_hash != manifest.suite_hash:
        raise InfrastructureError("baseline suite hash verification failed")
    expected = hashlib.sha256(f"{manifest.name}:{manifest.suite_hash}".encode()).hexdigest()
    if expected != manifest.content_hash:
        raise InfrastructureError("baseline manifest content hash verification failed")
    return manifest


def inspect_baseline(path: Path) -> dict[str, object]:
    """Return stable human-readable baseline metadata after verification."""
    manifest = load_baseline(path)
    return {
        "name": manifest.name,
        "content_hash": manifest.content_hash,
        "suite_hash": manifest.suite_hash,
        "worker": f"{manifest.worker}@{manifest.worker_version}",
        "world": manifest.world,
        "scenarios": len(manifest.suite.scenarios),
        "runs": len(manifest.suite.runs),
    }
