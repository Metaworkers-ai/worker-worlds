import json
from pathlib import Path

from worker_worlds.demonstration import generate_demo


async def test_required_a_b_c_demonstration(tmp_path: Path) -> None:
    source = Path(".worker-worlds/week2-report/suite.json")
    baseline, report = await generate_demo(source, tmp_path)
    assert baseline.exists() and report.exists()
    b = json.loads((tmp_path / "candidate-b-comparison/comparison.json").read_text())
    c = json.loads((tmp_path / "candidate-c-comparison/comparison.json").read_text())
    assert b["verdict"]["passed"] is False
    assert b["verdict"]["new_critical"] == 1
    assert any(item["primary_classification"] == "new_failure" for item in b["scenarios"])
    assert any(item["primary_classification"] == "fixed" for item in c["scenarios"])
    stable = next(item for item in c["scenarios"] if item["scenario_id"] == "demo.authorization")
    assert stable["primary_classification"] == "unchanged_pass"
