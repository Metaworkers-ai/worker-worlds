"""Scenario loading with source-aware validation errors."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from worker_worlds.contracts import Scenario
from worker_worlds.errors import ScenarioLoadError

MAX_SCENARIO_BYTES = 1_000_000
MAX_YAML_DEPTH = 32


def _depth(value: object, current: int = 0) -> int:
    if isinstance(value, dict):
        return max((_depth(item, current + 1) for item in value.values()), default=current)
    if isinstance(value, list):
        return max((_depth(item, current + 1) for item in value), default=current)
    return current


def load_scenario(path: Path) -> Scenario:
    """Load and validate one YAML scenario."""
    try:
        if path.is_symlink():
            raise ScenarioLoadError(f"{path}: symbolic-link scenarios are not accepted")
        size = path.stat().st_size
        if size > MAX_SCENARIO_BYTES:
            raise ScenarioLoadError(f"{path}: scenario exceeds {MAX_SCENARIO_BYTES} bytes")
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ScenarioLoadError(f"{path}: scenario root must be a mapping")
        if _depth(raw) > MAX_YAML_DEPTH:
            raise ScenarioLoadError(f"{path}: scenario nesting exceeds {MAX_YAML_DEPTH} levels")
        assertions = raw.get("assertions")
        if isinstance(assertions, list):
            for index, assertion in enumerate(assertions):
                if isinstance(assertion, dict):
                    assertion.setdefault("source_file", str(path))
                    assertion.setdefault("source_index", index)
        return Scenario.model_validate(raw)
    except OSError as exc:
        raise ScenarioLoadError(f"{path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ScenarioLoadError(f"{path}: invalid YAML: {exc}") from exc
    except ValidationError as exc:
        locations = "; ".join(".".join(str(part) for part in item["loc"]) for item in exc.errors())
        raise ScenarioLoadError(f"{path}: invalid scenario at {locations}: {exc}") from exc
