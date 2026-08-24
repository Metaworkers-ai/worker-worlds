import json
import os
import shutil
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


def test_scenario_and_agent_discovery_commands() -> None:
    scenarios = _cli("scenarios", "list", "--json")
    assert scenarios.returncode == 0
    scenario_payload = json.loads(scenarios.stdout)
    assert any(item["id"] == "refund.partial.happy" for item in scenario_payload["scenarios"])
    shown = _cli("scenarios", "show", "refund.partial.happy", "--json")
    assert shown.returncode == 0
    assert json.loads(shown.stdout)["scenario_id"] == "refund.partial.happy"
    agents = _cli("agents", "list", "--json", env={"OPENAI_API_KEY": "present"})
    assert agents.returncode == 0
    agent_payload = json.loads(agents.stdout)
    assert {item["id"] for item in agent_payload["agents"]} == {
        "langgraph-project",
        "local-stub",
        "openai-project",
    }
    assert all(item["ready"] for item in agent_payload["agents"])


def test_agent_doctor_missing_environment_and_id_dry_run() -> None:
    doctor = _cli("agents", "doctor", "openai-project", "--json", env={"OPENAI_API_KEY": ""})
    assert doctor.returncode == 1
    item = json.loads(doctor.stdout)["agent"]
    assert item["ready"] is False
    assert item["missing_environment"] == ["OPENAI_API_KEY"]
    dry_run = _cli(
        "run",
        "--scenario",
        "refund.partial.happy",
        "--agent",
        "openai-project",
        "--no-interactive",
        "--dry-run",
        "--json",
        env={"OPENAI_API_KEY": "present"},
    )
    assert dry_run.returncode == 0
    payload = json.loads(dry_run.stdout)
    assert payload["scenario_id"] == "refund.partial.happy"
    assert payload["worker"] == "openai-project"


def test_non_tty_missing_scenario_and_unknown_suggestion() -> None:
    missing = _cli("run", "--no-interactive", "--json")
    assert missing.returncode == 2
    assert "scenario is required" in json.loads(missing.stdout)["message"]
    unknown = _cli("scenarios", "show", "refund.partial.hapy", "--json")
    assert unknown.returncode == 2
    assert "refund.partial.happy" in json.loads(unknown.stdout)["message"]
    unknown_agent = _cli(
        "run",
        "--scenario",
        "refund.partial.happy",
        "--agent",
        "openai-projec",
        "--dry-run",
        "--json",
    )
    assert unknown_agent.returncode == 2
    assert "openai-project" in json.loads(unknown_agent.stdout)["message"]


def test_interactive_selection_prints_reproducible_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class InteractiveInput:
        def isatty(self) -> bool:
            return True

    monkeypatch.setenv("OPENAI_API_KEY", "fake-present")
    selections = iter(["damaged", "1", "langgraph", "1"])
    monkeypatch.setattr(sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: next(selections))
    status = main(["run", "--dry-run"])
    assert status == 0
    output = capsys.readouterr().out
    assert "Refund the damaged item" in output
    assert "langgraph 1.0.0 — ready=true" in output
    assert "reproduce=worker-worlds run --scenario" in output
    assert "--agent langgraph-project" in output
    assert "--no-interactive" in output


def test_interactive_unready_agent_is_visible_but_cannot_be_selected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class InteractiveInput:
        def isatty(self) -> bool:
            return True

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    selections = iter(["damaged", "1", "openai", "1"])
    monkeypatch.setattr(sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: next(selections))
    assert main(["run", "--dry-run"]) == 2
    captured = capsys.readouterr()
    assert "ready=false unavailable" in captured.out
    assert "is not ready" in captured.err


def test_custom_scenario_location_and_completed_interactive_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class InteractiveInput:
        def isatty(self) -> bool:
            return True

    scenarios = tmp_path / "custom-scenarios"
    scenarios.mkdir()
    shutil.copy("tests/fixtures/successful_partial_refund.yaml", scenarios / "custom.yaml")
    config = tmp_path / "worker-worlds.yaml"
    config.write_text(
        "schema_version: '1.0'\n"
        f"execution:\n  scenario_locations: ['{scenarios}']\n"
        "agents:\n"
        "  local-stub:\n"
        "    id: local-stub\n"
        "    version: '1.0.0'\n"
        "    adapter: stub\n",
        encoding="utf-8",
    )
    listed = _cli("scenarios", "list", "--config", str(config), "--json")
    assert listed.returncode == 0
    assert [item["id"] for item in json.loads(listed.stdout)["scenarios"]] == [
        "fixture.refund.success"
    ]

    selections = iter(["refund", "1", "stub", "1"])
    monkeypatch.setattr(sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: next(selections))
    output = tmp_path / "runs"
    status = main(["run", "--config", str(config), "--output", str(output)])
    assert status == 0
    rendered = capsys.readouterr().out
    assert "passed=true" in rendered
    assert "reproduce=worker-worlds run --scenario fixture.refund.success" in rendered
    records = list(output.glob("run_*.json"))
    assert len(records) == 1
    assert RunRecord.model_validate_json(records[0].read_text()).passed


def test_agent_doctor_reports_package_factory_and_database_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "worker-worlds.yaml"
    config.write_text(
        "agents:\n"
        "  diagnosed:\n"
        "    id: diagnosed\n"
        "    version: '1.0.0'\n"
        "    adapter: openai-agents\n"
        "    factory: missing_factory_package:create\n",
        encoding="utf-8",
    )

    async def healthy_database(_settings: object) -> tuple[bool, str]:
        return True, "ready"

    monkeypatch.setattr("worker_worlds.cli.database_health", healthy_database)
    status = main(["agents", "doctor", "diagnosed", "--config", str(config), "--json"])
    assert status == 1
    factory = json.loads(capsys.readouterr().out)["agent"]
    assert factory["package_ready"] is True
    assert factory["factory_ready"] is False
    assert factory["database_ready"] is True

    monkeypatch.setattr("worker_worlds.agent_registry.importlib.util.find_spec", lambda _name: None)
    status = main(["agents", "doctor", "diagnosed", "--config", str(config), "--json"])
    assert status == 1
    package = json.loads(capsys.readouterr().out)["agent"]
    assert package["package_ready"] is False

    stub_config = tmp_path / "stub.yaml"
    stub_config.write_text(
        "agents:\n  diagnosed:\n    id: diagnosed\n    version: '1.0.0'\n    adapter: stub\n",
        encoding="utf-8",
    )

    async def failed_database(_settings: object) -> tuple[bool, str]:
        return False, "connection failed"

    monkeypatch.setattr("worker_worlds.cli.database_health", failed_database)
    status = main(["agents", "doctor", "diagnosed", "--config", str(stub_config), "--json"])
    assert status == 1
    database = json.loads(capsys.readouterr().out)["agent"]
    assert database["package_ready"] is True
    assert database["factory_ready"] is True
    assert database["database_ready"] is False
    assert database["database"] == "connection failed"
