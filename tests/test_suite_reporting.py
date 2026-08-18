from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from worker_worlds.contracts import SuiteRecord
from worker_worlds.grading import DeterministicGrader
from worker_worlds.reporting import HtmlReporter, JUnitReporter, SuiteJsonReporter
from worker_worlds.runner import Runner
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld
from worker_worlds.suite import SuiteRunner


async def _suite() -> SuiteRecord:
    scenario = load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))
    return await SuiteRunner(Runner(DeterministicGrader()), concurrency=3).run(
        "refund-suite", [scenario], lambda item: StubWorld(), StubWorkerAdapter, repetitions=5
    )


async def test_five_run_aggregate_preserves_records() -> None:
    suite = await _suite()
    assert len(suite.runs) == 5
    assert [record.repetition for record in suite.runs] == [0, 1, 2, 3, 4]
    aggregate = suite.aggregates[0]
    assert aggregate.completed_repetitions == 5
    assert aggregate.passed == 5
    assert aggregate.insufficient_sample
    assert len(aggregate.run_ids) == 5


async def test_all_report_formats_are_reloadable_safe_and_portable(tmp_path: Path) -> None:
    suite = await _suite()
    malicious_turn = (
        suite.runs[0].turns[-1].model_copy(update={"message": "<script>alert(1)</script>"})
    )
    malicious_run = suite.runs[0].model_copy(
        update={"turns": (*suite.runs[0].turns[:-1], malicious_turn)}
    )
    suite = suite.model_copy(update={"runs": (malicious_run, *suite.runs[1:])})
    json_path = await SuiteJsonReporter().report(suite, tmp_path)
    junit_path = await JUnitReporter().report(suite, tmp_path)
    html_path = await HtmlReporter().report(suite, tmp_path)
    loaded = SuiteRecord.model_validate(json.loads(json_path.read_text()))
    assert len(loaded.runs) == 5
    root = ET.parse(junit_path).getroot()
    assert root.tag == "testsuite" and len(root.findall("testcase")) == 5
    rendered = html_path.read_text()
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "http://" not in rendered and "https://" not in rendered
    assert "<main>" in rendered and "Reproducibility" in rendered
