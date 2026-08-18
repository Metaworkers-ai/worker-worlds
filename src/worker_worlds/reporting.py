"""Machine-readable reporters."""

import asyncio
import html
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from worker_worlds.contracts import RunRecord, SuiteRecord, VerdictStatus

_SECRET_KEYS = frozenset({"secret", "password", "api_key", "access_token", "authorization_token"})


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _canonical_redacted(model: RunRecord | SuiteRecord) -> str:
    return json.dumps(
        _redact(model.model_dump(mode="json")),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


class JsonReporter:
    """Write one canonical RunRecord JSON artifact."""

    def __init__(self, output_directory: Path) -> None:
        """Configure the artifact directory."""
        self.output_directory = output_directory
        self.output_path: Path | None = None

    async def report(self, record: RunRecord) -> None:
        """Persist canonical JSON without reinterpreting verdicts."""
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_directory / f"{record.id}.json"
        await asyncio.to_thread(
            self.output_path.write_text, _canonical_redacted(record) + "\n", encoding="utf-8"
        )


class SuiteJsonReporter:
    """Write canonical suite JSON with stable relative run references."""

    async def report(self, suite: SuiteRecord, output_directory: Path) -> Path:
        """Write source runs and their canonical suite envelope."""
        await asyncio.to_thread(output_directory.mkdir, parents=True, exist_ok=True)
        run_directory = output_directory / "runs"
        await asyncio.to_thread(run_directory.mkdir, exist_ok=True)
        for record in suite.runs:
            await asyncio.to_thread(
                (run_directory / f"{record.id}.json").write_text,
                _canonical_redacted(record) + "\n",
                encoding="utf-8",
            )
        references = {str(record.id): f"runs/{record.id}.json" for record in suite.runs}
        enriched = suite.model_copy(update={"artifact_references": references})
        path = output_directory / "suite.json"
        await asyncio.to_thread(
            path.write_text, _canonical_redacted(enriched) + "\n", encoding="utf-8"
        )
        return path


class JUnitReporter:
    """Generate standards-compatible JUnit XML without trace dumps."""

    async def report(self, suite: SuiteRecord, output_directory: Path) -> Path:
        """Map repetitions to test cases with failures and infrastructure errors."""
        await asyncio.to_thread(output_directory.mkdir, parents=True, exist_ok=True)
        failures = sum(
            not record.passed and not record.incomplete_evidence for record in suite.runs
        )
        errors = sum(record.incomplete_evidence for record in suite.runs)
        root = ET.Element(
            "testsuite",
            {
                "name": f"Worker Worlds: {suite.name}",
                "tests": str(len(suite.runs)),
                "failures": str(failures),
                "errors": str(errors),
                "time": f"{sum(record.total_duration_ms for record in suite.runs) / 1000:.3f}",
            },
        )
        for record in suite.runs:
            case = ET.SubElement(
                root,
                "testcase",
                {
                    "classname": str(record.scenario_id),
                    "name": f"repetition-{record.repetition}",
                    "time": f"{record.total_duration_ms / 1000:.3f}",
                },
            )
            properties = ET.SubElement(case, "properties")
            ET.SubElement(properties, "property", {"name": "run_id", "value": str(record.id)})
            ET.SubElement(
                properties, "property", {"name": "scenario_id", "value": str(record.scenario_id)}
            )
            ET.SubElement(
                properties, "property", {"name": "artifact", "value": f"runs/{record.id}.json"}
            )
            summary = "; ".join(
                f"{verdict.severity.value}:{verdict.assertion_id}:{verdict.reason_code}"
                for verdict in record.verdicts
                if verdict.status is not VerdictStatus.PASS
            )[:4000]
            if record.incomplete_evidence or record.terminal_reason.value in {
                "infrastructure_error",
                "scenario_error",
            }:
                ET.SubElement(
                    case, "error", {"message": record.error_message or "incomplete evidence"}
                ).text = summary
            elif not record.passed:
                ET.SubElement(
                    case, "failure", {"message": "deterministic assertion failure"}
                ).text = summary
            info = [
                verdict.message for verdict in record.verdicts if verdict.severity.value == "info"
            ]
            ET.SubElement(case, "system-out").text = "\n".join(info)[:4000]
        ET.indent(root)
        path = output_directory / "junit.xml"
        data = ET.tostring(root, encoding="unicode", xml_declaration=True)
        await asyncio.to_thread(path.write_text, data, encoding="utf-8")
        return path


class HtmlReporter:
    """Generate one escaped semantic static HTML report with inline CSS."""

    async def report(self, suite: SuiteRecord, output_directory: Path) -> Path:
        """Render summary, failures, details, evidence, and reproducibility."""
        await asyncio.to_thread(output_directory.mkdir, parents=True, exist_ok=True)
        total = len(suite.runs)
        passed = sum(record.passed for record in suite.runs)
        severity: dict[str, int] = {key: 0 for key in ("critical", "high", "medium", "info")}
        for record in suite.runs:
            for verdict in record.verdicts:
                severity[verdict.severity.value] += 1
        rows = []
        details = []
        for record in suite.runs:
            failures = [
                verdict for verdict in record.verdicts if verdict.status is not VerdictStatus.PASS
            ]
            if failures:
                rows.append(
                    "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                        html.escape(str(record.scenario_id)),
                        record.repetition,
                        html.escape(record.terminal_reason.value),
                        html.escape(
                            ", ".join(
                                f"{item.severity.value}: {item.reason_code}" for item in failures
                            )
                        ),
                    )
                )
            detail_payload = {
                "trigger": record.scenario_trigger,
                "scenario_tags": record.scenario_tags,
                "initial_snapshot": (
                    record.initial_snapshot.model_dump(mode="json")
                    if record.initial_snapshot is not None
                    else None
                ),
                "final_snapshot": (
                    record.final_snapshot.model_dump(mode="json")
                    if record.final_snapshot is not None
                    else None
                ),
                "turns": [turn.model_dump(mode="json") for turn in record.turns],
                "events": [event.model_dump(mode="json") for event in record.events],
                "verdicts": [verdict.model_dump(mode="json") for verdict in record.verdicts],
                "resources": {
                    "duration_ms": record.total_duration_ms,
                    "tool_calls": record.tool_call_count,
                    "model_tokens": record.model_tokens,
                    "cost_minor": record.cost_minor,
                },
            }
            details.append(
                f'<details id="{html.escape(str(record.id))}"><summary>{html.escape(str(record.scenario_id))} '
                f"repetition {record.repetition} - {'PASS' if record.passed else 'FAIL'}</summary>"
                f"<pre>{html.escape(json.dumps(_redact(detail_payload), sort_keys=True, indent=2))}</pre></details>"
            )
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Worker Worlds - {html.escape(suite.name)}</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:auto;padding:2rem;line-height:1.5}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #777;padding:.5rem;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:1rem}}.status{{font-weight:700}}a:focus,summary:focus{{outline:3px solid #005fcc}}</style></head>
<body><header><h1>Worker Worlds suite: {html.escape(suite.name)}</h1><p class="status">Passed {passed} of {total} repetitions ({(passed / total * 100 if total else 0):.1f}%).</p></header>
<main><section aria-labelledby="summary"><h2 id="summary">Summary</h2><dl><dt>Worker</dt><dd>{html.escape(suite.worker)} {html.escape(suite.worker_version)}</dd><dt>World</dt><dd>{html.escape(suite.world)}</dd><dt>Verdicts</dt><dd>{html.escape(str(severity))}</dd><dt>Duration</dt><dd>{sum(record.total_duration_ms for record in suite.runs)} ms</dd><dt>Tool calls</dt><dd>{sum(record.tool_call_count for record in suite.runs)}</dd></dl></section>
<section aria-labelledby="failures"><h2 id="failures">Failure index</h2><table><thead><tr><th>Scenario</th><th>Repetition</th><th>Terminal reason</th><th>Evidence code</th></tr></thead><tbody>{"".join(rows) or '<tr><td colspan="4">No failures</td></tr>'}</tbody></table></section>
<section aria-labelledby="details"><h2 id="details">Scenario details</h2>{"".join(details)}</section>
<section aria-labelledby="repro"><h2 id="repro">Reproducibility</h2><dl><dt>Schema</dt><dd>{html.escape(suite.schema_version)}</dd><dt>Configuration hash</dt><dd>{html.escape(suite.configuration_hash)}</dd><dt>Suite ID</dt><dd>{html.escape(suite.id)}</dd></dl><p>Reproduce with <code>worker-worlds suite &lt;scenario-path&gt; --repetitions 5</code>.</p></section></main></body></html>"""
        path = output_directory / "report.html"
        await asyncio.to_thread(path.write_text, document, encoding="utf-8")
        return path
