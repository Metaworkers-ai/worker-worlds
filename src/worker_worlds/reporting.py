"""Machine-readable reporters."""

import asyncio
import hashlib
import html
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

from worker_worlds.contracts import ComparisonReport, RunRecord, SuiteRecord, VerdictStatus

_SECRET_KEYS = frozenset({"secret", "password", "api_key", "access_token", "authorization_token"})


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _canonical_redacted(model: RunRecord | SuiteRecord | ComparisonReport) -> str:
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
        self.output_path = self.output_directory / f"{record.id}.json"
        await asyncio.to_thread(_atomic_write, self.output_path, _canonical_redacted(record) + "\n")


class SuiteJsonReporter:
    """Write canonical suite JSON with stable relative run references."""

    async def report(self, suite: SuiteRecord, output_directory: Path) -> Path:
        """Write source runs and their canonical suite envelope."""
        await asyncio.to_thread(output_directory.mkdir, parents=True, exist_ok=True)
        run_directory = output_directory / "runs"
        await asyncio.to_thread(run_directory.mkdir, exist_ok=True)
        for record in suite.runs:
            await asyncio.to_thread(
                _atomic_write,
                run_directory / f"{record.id}.json",
                _canonical_redacted(record) + "\n",
            )
        references = {str(record.id): f"runs/{record.id}.json" for record in suite.runs}
        enriched = suite.model_copy(update={"artifact_references": references})
        path = output_directory / "suite.json"
        await asyncio.to_thread(_atomic_write, path, _canonical_redacted(enriched) + "\n")
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

    def __init__(
        self, *, artifact_mode: str = "auto", split_threshold_bytes: int = 500_000
    ) -> None:
        """Configure full, summary, or deterministic automatic split rendering."""
        if artifact_mode not in {"auto", "full", "summary"}:
            raise ValueError("artifact mode must be auto, full, or summary")
        self.artifact_mode = artifact_mode
        self.split_threshold_bytes = split_threshold_bytes

    async def report(self, suite: SuiteRecord, output_directory: Path) -> Path:
        """Render summary, failures, details, evidence, and reproducibility."""
        await asyncio.to_thread(output_directory.mkdir, parents=True, exist_ok=True)
        total = len(suite.runs)
        passed = sum(record.passed for record in suite.runs)
        severity: dict[str, int] = {key: 0 for key in ("critical", "high", "medium", "info")}
        for record in suite.runs:
            for verdict in record.verdicts:
                severity[verdict.severity.value] += 1
        rows: list[str] = []
        details: list[str] = []
        estimated_size = len(_canonical_redacted(suite).encode())
        split = self.artifact_mode == "summary" or (
            self.artifact_mode == "auto" and estimated_size > self.split_threshold_bytes
        )
        evidence_directory = output_directory / "evidence"
        if split:
            await asyncio.to_thread(evidence_directory.mkdir, exist_ok=True)
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
            label = (
                f"{html.escape(str(record.scenario_id))} repetition {record.repetition} - "
                f"{'PASS' if record.passed else 'FAIL'}"
            )
            if split:
                evidence_name = f"{record.id}.html"
                payload = html.escape(json.dumps(_redact(detail_payload), sort_keys=True, indent=2))
                page = (
                    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width"><title>Worker Worlds evidence</title>'
                    "<style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:2rem}"
                    "pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>"
                    f'<p><a href="../report.html">Back to summary</a></p><h1>{label}</h1>'
                    f"<pre>{payload}</pre></body></html>"
                )
                await asyncio.to_thread(
                    (evidence_directory / evidence_name).write_text, page, encoding="utf-8"
                )
                details.append(f'<p><a href="evidence/{evidence_name}">{label}</a></p>')
            else:
                details.append(
                    f'<details id="{html.escape(str(record.id))}"><summary>{label}</summary>'
                    f"<pre>{html.escape(json.dumps(_redact(detail_payload), sort_keys=True, indent=2))}</pre></details>"
                )
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Worker Worlds - {html.escape(suite.name)}</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:auto;padding:2rem;line-height:1.5}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #777;padding:.5rem;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:1rem}}.status{{font-weight:700}}a:focus,summary:focus{{outline:3px solid #005fcc}}</style></head>
<body><header><h1>Worker Worlds suite: {html.escape(suite.name)}</h1><p class="status">Passed {passed} of {total} repetitions ({(passed / total * 100 if total else 0):.1f}%).</p></header>
<main><section aria-labelledby="summary"><h2 id="summary">Summary</h2><dl><dt>Worker</dt><dd>{html.escape(suite.worker)} {html.escape(suite.worker_version)}</dd><dt>World</dt><dd>{html.escape(suite.world)}</dd><dt>Verdicts</dt><dd>{html.escape(str(severity))}</dd><dt>Duration</dt><dd>{sum(record.total_duration_ms for record in suite.runs)} ms</dd><dt>Tool calls</dt><dd>{sum(record.tool_call_count for record in suite.runs)}</dd><dt>Evidence mode</dt><dd>{"linked" if split else "inline"}</dd><dt>Source size</dt><dd>{estimated_size} bytes</dd></dl></section>
<section aria-labelledby="failures"><h2 id="failures">Failure index</h2><table><thead><tr><th>Scenario</th><th>Repetition</th><th>Terminal reason</th><th>Evidence code</th></tr></thead><tbody>{"".join(rows) or '<tr><td colspan="4">No failures</td></tr>'}</tbody></table></section>
<section aria-labelledby="details"><h2 id="details">Scenario details</h2>{"".join(details)}</section>
<section aria-labelledby="repro"><h2 id="repro">Reproducibility</h2><dl><dt>Schema</dt><dd>{html.escape(suite.schema_version)}</dd><dt>Configuration hash</dt><dd>{html.escape(suite.configuration_hash)}</dd><dt>Suite ID</dt><dd>{html.escape(suite.id)}</dd></dl><p>Reproduce with <code>worker-worlds suite &lt;scenario-path&gt; --repetitions 5</code>.</p></section></main></body></html>"""
        path = output_directory / "report.html"
        await asyncio.to_thread(path.write_text, document, encoding="utf-8")
        return path


class ComparisonReporter:
    """Write canonical JSON, JUnit, and accessible static comparison HTML."""

    def __init__(self, *, split_threshold_bytes: int = 500_000) -> None:
        """Configure deterministic split mode for larger reports."""
        self.split_threshold_bytes = split_threshold_bytes

    @staticmethod
    def _scenario_filename(scenario_id: object) -> str:
        digest = hashlib.sha256(str(scenario_id).encode()).hexdigest()[:16]
        return f"scenario-{digest}.html"

    async def report(
        self, report: ComparisonReport, output_directory: Path
    ) -> tuple[Path, Path, Path]:
        """Write all comparison formats with stable relative links."""
        await asyncio.to_thread(output_directory.mkdir, parents=True, exist_ok=True)
        json_path = output_directory / "comparison.json"
        await asyncio.to_thread(
            json_path.write_text, _canonical_redacted(report) + "\n", encoding="utf-8"
        )
        junit_path = output_directory / "comparison.junit.xml"
        await asyncio.to_thread(junit_path.write_bytes, self._junit(report))
        html_path = output_directory / "comparison.html"
        document = self._html(report, split=False)
        if len(document.encode()) > self.split_threshold_bytes:
            detail_directory = output_directory / "scenarios"
            await asyncio.to_thread(detail_directory.mkdir, exist_ok=True)
            for scenario in report.scenarios:
                detail = self._scenario_html(report, scenario)
                await asyncio.to_thread(
                    (detail_directory / self._scenario_filename(scenario.scenario_id)).write_text,
                    detail,
                    encoding="utf-8",
                )
            document = self._html(report, split=True)
        await asyncio.to_thread(html_path.write_text, document, encoding="utf-8")
        return json_path, junit_path, html_path

    def _junit(self, report: ComparisonReport) -> bytes:
        failures = sum(
            item.primary_classification.value
            in {"new_failure", "failure_mode_changed", "infrastructure_health_regressed"}
            for item in report.scenarios
        )
        errors = sum(item.compatibility.value == "incompatible" for item in report.scenarios)
        root = ET.Element(
            "testsuite",
            {
                "name": "Worker Worlds behavioral comparison",
                "tests": str(len(report.scenarios)),
                "failures": str(failures),
                "errors": str(errors),
            },
        )
        for item in report.scenarios:
            case = ET.SubElement(
                root,
                "testcase",
                {"classname": "worker-worlds.compare", "name": str(item.scenario_id)},
            )
            props = ET.SubElement(case, "properties")
            ET.SubElement(props, "property", {"name": "baseline", "value": report.baseline_source})
            ET.SubElement(
                props, "property", {"name": "candidate", "value": report.candidate_source}
            )
            ET.SubElement(
                props,
                "property",
                {"name": "classification", "value": item.primary_classification.value},
            )
            if item.compatibility.value == "incompatible":
                ET.SubElement(case, "error", {"message": "; ".join(item.compatibility_reasons)})
            elif item.primary_classification.value in {
                "new_failure",
                "failure_mode_changed",
                "infrastructure_health_regressed",
            }:
                ET.SubElement(case, "failure", {"message": item.primary_classification.value})
            elif item.primary_classification.value == "fixed":
                ET.SubElement(case, "system-out").text = "behavior fixed"
        return cast("bytes", ET.tostring(root, encoding="utf-8", xml_declaration=True))

    def _html(self, report: ComparisonReport, *, split: bool) -> str:
        rows = []
        for item in report.scenarios:
            label = html.escape(str(item.scenario_id))
            filename = self._scenario_filename(item.scenario_id)
            title = f'<a href="scenarios/{filename}">{label}</a>' if split else label
            rows.append(
                '<tr><th scope="row">'
                + title
                + "</th><td>"
                + html.escape(item.primary_classification.value)
                + "</td><td>"
                + f"{item.baseline.pass_rate:.1%} → {item.candidate.pass_rate:.1%}"
                + "</td><td>"
                + html.escape(", ".join(delta.identity for delta in item.failure_deltas) or "—")
                + "</td></tr>"
            )
        reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in report.verdict.reasons)
        return self._shell(
            "Behavioral comparison",
            f"<h1>Worker Worlds behavioral comparison</h1>"
            f"<p><strong>Gate:</strong> {'PASS' if report.verdict.passed else 'FAIL'}</p>"
            f"<p>Baseline <code>{html.escape(report.baseline_hash)}</code><br>"
            f"Candidate <code>{html.escape(report.candidate_hash)}</code></p>"
            f"<ul>{reasons}</ul><table><thead><tr><th>Scenario</th><th>Classification</th>"
            f"<th>Pass rate</th><th>Evidence-linked changes</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
            f"<h2>Reproduce</h2><pre>{html.escape(chr(10).join(report.reproduction_commands))}</pre>",
        )

    def _scenario_html(self, report: ComparisonReport, scenario: object) -> str:
        data = json.dumps(_redact(scenario.model_dump(mode="json")), sort_keys=True, indent=2)  # type: ignore[attr-defined]
        return self._shell(
            f"Scenario {scenario.scenario_id}",  # type: ignore[attr-defined]
            f'<nav><a href="../comparison.html">Back to comparison</a></nav>'
            f"<h1>{html.escape(str(scenario.scenario_id))}</h1><pre>{html.escape(data)}</pre>",  # type: ignore[attr-defined]
        )

    @staticmethod
    def _shell(title: str, body: str) -> str:
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>body{{font:16px system-ui;max-width:1100px;"
            "margin:auto;padding:2rem;color:#17202a}}table{border-collapse:collapse;width:100%}"
            "th,td{border:1px solid #bbb;padding:.55rem;text-align:left;vertical-align:top}"
            "code,pre{background:#f4f5f6;padding:.25rem;overflow:auto}a:focus{outline:3px solid #06c}"
            "</style></head><body><main>" + body + "</main></body></html>"
        )
