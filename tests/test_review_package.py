import json
from pathlib import Path

from worker_worlds.review import executable_hash, generate_review_package
from worker_worlds.scenario_release import validate_scenario_directory


def test_domain_review_package_is_complete_and_unapproved(tmp_path: Path) -> None:
    scenarios = validate_scenario_directory(Path("scenarios/release"))
    paths = generate_review_package(scenarios, tmp_path)
    assert all(path.exists() for path in paths)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    statuses = json.loads((tmp_path / "review-status.json").read_text())
    assert len(manifest) == 200
    assert set(statuses["scenarios"]) == {str(item.id) for item in scenarios}
    assert {item["status"] for item in statuses["scenarios"].values()} == {"pending"}
    assert all(item["approval_status"] == "pending" for item in manifest)
    page = (tmp_path / "index.html").read_text()
    assert '<html lang="en">' in page and "pending independent domain approval" in page


def test_review_metadata_does_not_change_executable_hash() -> None:
    scenario = validate_scenario_directory(Path("scenarios/release"))[0]
    changed = scenario.model_copy(
        update={
            "metadata": {**scenario.metadata, "review_status": "approved", "reviewer": "expert"}
        }
    )
    assert executable_hash(changed) == executable_hash(scenario)
    executable_change = scenario.model_copy(
        update={
            "trigger": scenario.trigger.model_copy(
                update={"content": "Different executable trigger"}
            )
        }
    )
    assert executable_hash(executable_change) != executable_hash(scenario)
