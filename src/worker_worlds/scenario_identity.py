"""Stable executable scenario identity independent of loader provenance."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from worker_worlds.contracts import Scenario


def scenario_content_hash(scenario: Scenario) -> str:
    """Hash executable content while excluding local assertion source locations."""
    payload = scenario.model_dump(mode="json")
    assertions = cast(list[dict[str, Any]], payload.get("assertions", []))
    for assertion in assertions:
        assertion["source_file"] = None
        assertion["source_index"] = None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
