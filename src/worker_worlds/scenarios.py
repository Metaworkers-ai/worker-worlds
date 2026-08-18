"""Scenario loading with source-aware validation errors."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from worker_worlds.contracts import Scenario
from worker_worlds.errors import ScenarioLoadError


def load_scenario(path: Path) -> Scenario:
    """Load and validate one YAML scenario."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ScenarioLoadError(f"{path}: scenario root must be a mapping")
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
