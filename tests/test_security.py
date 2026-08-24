from pathlib import Path

import pytest

from worker_worlds.errors import ScenarioLoadError
from worker_worlds.grading import _safe_fullmatch
from worker_worlds.scenarios import MAX_SCENARIO_BYTES, load_scenario


def test_scenario_loader_rejects_symlink_and_oversize(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    with pytest.raises(ScenarioLoadError, match="symbolic-link"):
        load_scenario(link)
    large = tmp_path / "large.yaml"
    large.write_bytes(b"x" * (MAX_SCENARIO_BYTES + 1))
    with pytest.raises(ScenarioLoadError, match="exceeds"):
        load_scenario(large)


def test_regex_limits_reject_nested_quantifier_and_large_input() -> None:
    with pytest.raises(ValueError, match="nested quantifier"):
        # This adversarial expression verifies rejection before the regex engine executes it.
        _safe_fullmatch("(a+)+$", "a" * 100)  # lgtm[py/redos]
    with pytest.raises(ValueError, match="safe evaluation limits"):
        _safe_fullmatch("a+", "a" * 10_001)
    assert _safe_fullmatch(r"[A-Z]{3}", "USD")
