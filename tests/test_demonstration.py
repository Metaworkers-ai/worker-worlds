import json
from pathlib import Path

from worker_worlds.demonstration import generate_demo
from worker_worlds.grading import DeterministicGrader
from worker_worlds.reporting import SuiteJsonReporter
from worker_worlds.runner import Runner
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld
from worker_worlds.suite import SuiteRunner


async def test_required_a_b_c_demonstration(tmp_path: Path) -> None:
    scenario = load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))
    suite = await SuiteRunner(Runner(DeterministicGrader()), concurrency=3).run(
        "demonstration-source",
        [scenario],
        lambda _scenario: StubWorld(),
        StubWorkerAdapter,
        repetitions=5,
    )
    source = await SuiteJsonReporter().report(suite, tmp_path / "source")
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
