"""Deterministic independently reviewable commerce scenario package."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
from pathlib import Path

from worker_worlds.contracts import Scenario
from worker_worlds.scenario_release import scenario_filename

REVIEW_OUTCOMES = (
    "pending",
    "approved",
    "approved_with_correction",
    "rejected",
    "needs_subject_matter_clarification",
)
_REVIEW_KEYS = {"review_status", "reviewer", "reviewed_at", "review_notes", "approval_status"}


def executable_hash(scenario: Scenario) -> str:
    """Hash executable content while excluding external review annotations."""
    data = scenario.model_dump(mode="json", exclude_none=True)
    metadata = data.get("metadata", {})
    if isinstance(metadata, dict):
        data["metadata"] = {
            key: value for key, value in metadata.items() if key not in _REVIEW_KEYS
        }
    for assertion in data.get("assertions", []):
        if isinstance(assertion, dict):
            assertion.pop("source_file", None)
            assertion.pop("source_index", None)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def generate_review_package(scenarios: tuple[Scenario, ...], output: Path) -> tuple[Path, ...]:
    """Write static HTML, JSON/CSV manifests, and an unsigned review ledger."""
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for scenario in sorted(scenarios, key=lambda item: str(item.id)):
        metadata = scenario.metadata
        records.append(
            {
                "scenario_id": str(scenario.id),
                "filename": scenario_filename(scenario),
                "family": metadata.get("family"),
                "risk": metadata.get("risk"),
                "expected_outcome": metadata.get("expected_business_outcome"),
                "initial_facts": metadata.get("required_initial_facts", []),
                "assertions": [item.id for item in scenario.assertions],
                "policies": metadata.get("policy_coverage", []),
                "severity": metadata.get("severity"),
                "tools": metadata.get("tools_involved", []),
                "mutant": metadata.get("mutant_killed"),
                "open_questions": [
                    "Does the expected outcome match current commerce operations practice?",
                    "Are valid alternative trajectories permitted by these assertions?",
                ],
                "approval_status": "pending",
                "executable_hash": executable_hash(scenario),
            }
        )
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps(records, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    buffer = io.StringIO()
    fields = (
        "scenario_id",
        "filename",
        "family",
        "risk",
        "expected_outcome",
        "severity",
        "tools",
        "policies",
        "mutant",
        "approval_status",
        "executable_hash",
    )
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                key: json.dumps(record[key]) if isinstance(record[key], list) else record[key]
                for key in fields
            }
        )
    csv_path = output / "manifest.csv"
    csv_path.write_text(buffer.getvalue(), encoding="utf-8")
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(record[key]))}</td>"
            for key in (
                "scenario_id",
                "family",
                "risk",
                "expected_outcome",
                "policies",
                "severity",
                "tools",
                "mutant",
                "approval_status",
            )
        )
        + "</tr>"
        for record in records
    )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Worker Worlds domain review</title><style>body{{font:14px system-ui;margin:1rem}}table{{border-collapse:collapse}}th,td{{border:1px solid #777;padding:.4rem;vertical-align:top}}thead{{position:sticky;top:0;background:white}}tr:target{{outline:3px solid #0563c1}}</style></head><body><h1>Worker Worlds commerce scenario review</h1><p>All 200 scenarios are pending independent domain approval. Record decisions in <code>review-status.json</code>; do not edit executable YAML for review-only notes.</p><table><thead><tr><th>ID</th><th>Family</th><th>Risk</th><th>Expected outcome</th><th>Policies</th><th>Severity</th><th>Tools</th><th>Mutant</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""
    html_path = output / "index.html"
    html_path.write_text(page, encoding="utf-8")
    statuses = {
        "schema_version": "1.0",
        "allowed_outcomes": REVIEW_OUTCOMES,
        "scenarios": {
            str(item.id): {"status": "pending", "reviewer": None, "notes": None}
            for item in scenarios
        },
    }
    status_path = output / "review-status.json"
    if not status_path.exists():
        status_path.write_text(
            json.dumps(statuses, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    checklist = output / "REVIEWER_INSTRUCTIONS.md"
    checklist.write_text(
        "# Commerce domain review\n\nReview each linked YAML and manifest row. Confirm risk, initial facts, expected outcome, valid alternatives, assertions, policy, severity, and mutant. Record exactly one allowed outcome in `review-status.json`: `approved`, `approved_with_correction`, `rejected`, or `needs_subject_matter_clarification`. Leave `pending` when undecided. Corrections to executable facts or assertions require a YAML change and therefore a new executable hash; review notes alone do not.\n",
        encoding="utf-8",
    )
    return manifest, csv_path, html_path, status_path, checklist
