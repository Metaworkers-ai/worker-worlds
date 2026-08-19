import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from worker_worlds.cli import main
from worker_worlds.contracts import RunRecord


def _cli(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "worker_worlds.cli", *arguments],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


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


@pytest.mark.parametrize(
    "arguments",
    [
        ("version",),
        ("config", "show"),
        ("schema", "check"),
        ("scenario", "validate", "scenarios/release"),
        ("scenario", "export", "scenarios/release", "--check"),
    ],
)
def test_cli_json_is_exactly_one_document(arguments: tuple[str, ...]) -> None:
    result = _cli(*arguments, "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 0
    assert payload["status"]
    assert result.stdout.count("\n") == 1


def test_cli_quiet_verbose_and_redaction() -> None:
    quiet = _cli("version", "--quiet")
    assert quiet.returncode == 0 and quiet.stdout == "" and quiet.stderr == ""
    secret = "postgresql://worker_worlds:do-not-print@127.0.0.1:55432/worker_worlds_test"
    verbose = _cli(
        "config",
        "show",
        "--verbose",
        "--json",
        env={"WORKER_WORLDS_DATABASE_URL": secret},
    )
    assert verbose.returncode == 0
    assert "do-not-print" not in verbose.stdout + verbose.stderr
    assert "component=cli" in verbose.stderr


def test_cli_dry_run_has_no_filesystem_effect(tmp_path: Path) -> None:
    output = tmp_path / "never-created"
    result = _cli(
        "run",
        "examples/scenarios/refund_happy.yaml",
        "--output",
        str(output),
        "--dry-run",
        "--json",
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["dry_run"] is True
    assert not output.exists()
    schema_output = tmp_path / "schemas"
    result = _cli("schema", "generate", "--directory", str(schema_output), "--dry-run", "--json")
    assert result.returncode == 0 and not schema_output.exists()


def test_cli_overwrite_protection_and_machine_error(tmp_path: Path) -> None:
    output = tmp_path / "suite"
    first = _cli(
        "suite",
        "examples/scenarios/refund_happy.yaml",
        "--repetitions",
        "1",
        "--output",
        str(output),
        "--quiet",
    )
    assert first.returncode == 0
    rejected = _cli(
        "suite",
        "examples/scenarios/refund_happy.yaml",
        "--repetitions",
        "1",
        "--output",
        str(output),
        "--json",
    )
    payload = json.loads(rejected.stdout)
    assert rejected.returncode == 2 and payload["exit_code"] == 2
    replaced = _cli(
        "suite",
        "examples/scenarios/refund_happy.yaml",
        "--repetitions",
        "1",
        "--output",
        str(output),
        "--overwrite",
        "--quiet",
    )
    assert replaced.returncode == 0


def test_report_validation_subcommand(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "result.json").write_text('{"schema_version":"1.0"}\n')
    (report / "junit.xml").write_text('<testsuite tests="0"/>\n')
    (report / "report.html").write_text('<html lang="en"><main>ok</main></html>\n')
    result = _cli("report", str(report), "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["artifacts_valid"] == 3
