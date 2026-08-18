import json
from pathlib import Path

import pytest

from worker_worlds.cli import main
from worker_worlds.contracts import RunRecord


def test_cli_integration(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status = main(
        [
            "run",
            "examples/scenarios/refund_happy.yaml",
            "--worker",
            "stub",
            "--output",
            str(tmp_path),
        ]
    )
    assert status == 0
    output = capsys.readouterr().out
    assert "passed=true" in output
    records = list(tmp_path.glob("run_*.json"))
    assert len(records) == 1
    record = RunRecord.model_validate(json.loads(records[0].read_text()))
    assert record.passed
