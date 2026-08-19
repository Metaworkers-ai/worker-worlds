"""Pure deterministic assertion grading over immutable run evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from worker_worlds.contracts import (
    AssertionSpec,
    AssertionType,
    EvidenceKind,
    EvidenceReference,
    JsonValue,
    RunRecord,
    Scenario,
    Verdict,
    VerdictId,
    VerdictStatus,
    WorldEvent,
)
from worker_worlds.policies import PolicyEvaluation, evaluate_policy

EVALUATOR_VERSION = "1.0"
_MISSING = object()


@dataclass(frozen=True)
class Evaluation:
    """Internal primitive result before verdict envelope construction."""

    status: VerdictStatus
    code: str
    explanation: str
    expected: JsonValue = None
    observed: JsonValue = None
    evidence: tuple[EvidenceReference, ...] = ()


def resolve_path(value: JsonValue, path: str) -> JsonValue | object:
    """Resolve a dotted mapping/list path while distinguishing missing from null."""
    current: Any = value
    for component in path.split("."):
        if isinstance(current, dict) and component in current:
            current = current[component]
        elif isinstance(current, list) and component.isdigit() and int(component) < len(current):
            current = current[int(component)]
        else:
            return _MISSING
    return cast(JsonValue, current)


def _event_matches(event: WorldEvent, filters: dict[str, JsonValue]) -> bool:
    direct: dict[str, JsonValue] = {
        "event_type": event.event_type,
        "entity_type": event.entity.type,
        "entity_id": event.entity.id,
        "actor_id": event.actor_id,
        "request_id": str(event.request_id) if event.request_id else None,
        "authorization_subject": (
            event.authorization.customer_id if event.authorization is not None else None
        ),
    }
    for key, expected in filters.items():
        if key.startswith("before."):
            actual = resolve_path(event.before, key.removeprefix("before."))
        elif key.startswith("after."):
            actual = resolve_path(event.after, key.removeprefix("after."))
        elif key.startswith("metadata."):
            actual = resolve_path(event.metadata, key.removeprefix("metadata."))
        else:
            actual = direct.get(key, _MISSING)
        if actual is _MISSING or actual != expected:
            return False
    return True


def _event_filters(assertion: AssertionSpec) -> dict[str, JsonValue]:
    filters = assertion.parameters.get("filters", {})
    output = dict(filters) if isinstance(filters, dict) else {}
    event_type = assertion.event or assertion.parameters.get("event_type")
    if isinstance(event_type, str):
        output["event_type"] = event_type
    return output


def _event_refs(events: list[WorldEvent]) -> tuple[EvidenceReference, ...]:
    return tuple(
        EvidenceReference(
            kind=EvidenceKind.EVENT,
            reference=str(event.id),
            sequence=event.sequence,
            value={"event_type": event.event_type, "entity": event.entity.model_dump(mode="json")},
        )
        for event in events
    )


def _changed_entities(record: RunRecord) -> list[dict[str, JsonValue]]:
    if record.initial_snapshot is None or record.final_snapshot is None:
        return []
    changes: list[dict[str, JsonValue]] = []
    types = sorted(set(record.initial_snapshot.state) | set(record.final_snapshot.state))
    for entity_type in types:
        before_rows = record.initial_snapshot.state.get(entity_type, [])
        after_rows = record.final_snapshot.state.get(entity_type, [])
        if not isinstance(before_rows, list) or not isinstance(after_rows, list):
            if before_rows != after_rows:
                changes.append({"entity_type": entity_type, "entity_id": "root", "fields": ["*"]})
            continue
        before = {
            str(row.get("id")): row for row in before_rows if isinstance(row, dict) and "id" in row
        }
        after = {
            str(row.get("id")): row for row in after_rows if isinstance(row, dict) and "id" in row
        }
        for entity_id in sorted(set(before) | set(after)):
            left, right = before.get(entity_id, {}), after.get(entity_id, {})
            fields = sorted(
                key for key in set(left) | set(right) if left.get(key) != right.get(key)
            )
            if fields:
                changes.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "fields": cast(list[JsonValue], fields),
                    }
                )
    return changes


class DeterministicGrader:
    """Evaluate all v1 deterministic assertion primitives and policies."""

    async def grade(self, scenario: Scenario, record: RunRecord) -> list[Verdict]:
        """Return stable verdicts and fail closed on incomplete evidence."""
        if record.incomplete_evidence or record.final_snapshot is None:
            assertion = AssertionSpec(
                id="evidence.complete",
                type=AssertionType.STATE_EQUALS,
                severity="critical",
                path="_evidence",
                required=True,
            )
            return [
                self._verdict(
                    assertion,
                    Evaluation(
                        VerdictStatus.ERROR,
                        "evidence.incomplete",
                        "run evidence is incomplete; grading cannot pass",
                    ),
                )
            ]
        return [self._grade_assertion(assertion, record) for assertion in scenario.assertions]

    def _grade_assertion(self, assertion: AssertionSpec, record: RunRecord) -> Verdict:
        try:
            assertion_type = AssertionType(assertion.type)
            evaluator = {
                AssertionType.STATE_EQUALS: self._state_equals,
                AssertionType.STATE_MATCHES: self._state_matches,
                AssertionType.VALUE_WITHIN: self._value_within,
                AssertionType.ACTION_EXISTS: self._action_exists,
                AssertionType.NO_ACTION: self._no_action,
                AssertionType.ACTION_COUNT: self._action_count,
                AssertionType.SEQUENCE_BEFORE: self._sequence_before,
                AssertionType.CHANGED_ENTITIES_SUBSET: self._changed_subset,
                AssertionType.RESOURCE_WITHIN: self._resource_within,
                AssertionType.POLICY: self._policy,
            }[assertion_type]
            return self._verdict(assertion, evaluator(assertion, record))
        except (KeyError, TypeError, ValueError) as exc:
            return self._verdict(
                assertion,
                Evaluation(
                    VerdictStatus.ERROR,
                    "assertion.authoring_error",
                    f"invalid assertion or unavailable typed evidence: {exc}",
                ),
            )

    def _state_source(self, assertion: AssertionSpec, record: RunRecord) -> tuple[str, JsonValue]:
        source = assertion.parameters.get("snapshot", "final")
        snapshot = record.initial_snapshot if source == "initial" else record.final_snapshot
        if snapshot is None:
            raise ValueError(f"{source} snapshot is unavailable")
        return str(source), snapshot.state

    def _state_equals(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        source, state = self._state_source(assertion, record)
        path = assertion.path or str(assertion.parameters["path"])
        expected = assertion.parameters.get("expected", assertion.value)
        observed = resolve_path(state, path)
        reference = EvidenceReference(
            kind=EvidenceKind.INITIAL_SNAPSHOT
            if source == "initial"
            else EvidenceKind.FINAL_SNAPSHOT,
            reference=source,
            path=path,
            value=None if observed is _MISSING else cast(JsonValue, observed),
        )
        if observed is _MISSING:
            return Evaluation(
                VerdictStatus.ERROR,
                "state.path_missing",
                f"state path {path} is missing",
                expected,
                None,
                (reference,),
            )
        passed = observed == expected
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "state.equal" if passed else "state.not_equal",
            f"{source} snapshot path {path} "
            f"{'equals' if passed else 'does not equal'} expected value",
            expected,
            cast(JsonValue, observed),
            (reference,),
        )

    def _state_matches(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        source, state = self._state_source(assertion, record)
        path = assertion.path or str(assertion.parameters["path"])
        observed = resolve_path(state, path)
        if observed is _MISSING:
            return Evaluation(
                VerdictStatus.ERROR, "state.path_missing", f"state path {path} is missing"
            )
        operation = str(assertion.parameters.get("operation", "subset"))
        expected = assertion.parameters.get("expected", assertion.value)
        if operation == "subset" and isinstance(observed, dict) and isinstance(expected, dict):
            passed = all(observed.get(key, _MISSING) == value for key, value in expected.items())
        elif operation == "contains" and isinstance(observed, list):
            passed = expected in observed
        elif operation == "length" and isinstance(observed, (list, dict, str)):
            passed = len(observed) == expected
        elif operation == "regex" and isinstance(observed, str) and isinstance(expected, str):
            passed = _safe_fullmatch(expected, observed)
        elif operation == "enum_equals":
            passed = observed == expected
        elif operation in {"all", "any"}:
            predicates = assertion.parameters.get("predicates", [])
            if not isinstance(predicates, list):
                raise ValueError("predicate combinations require a list")
            outcomes = [self._bounded_match(cast(JsonValue, observed), item) for item in predicates]
            passed = all(outcomes) if operation == "all" else any(outcomes)
        else:
            raise ValueError(f"invalid state_matches operation: {operation}")
        ref = EvidenceReference(
            kind=EvidenceKind.INITIAL_SNAPSHOT
            if source == "initial"
            else EvidenceKind.FINAL_SNAPSHOT,
            reference=source,
            path=path,
            value=cast(JsonValue, observed),
        )
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "state.matches" if passed else "state.mismatch",
            f"bounded {operation} match {'passed' if passed else 'failed'} at {path}",
            expected,
            cast(JsonValue, observed),
            (ref,),
        )

    @staticmethod
    def _bounded_match(observed: JsonValue, predicate: JsonValue) -> bool:
        if not isinstance(predicate, dict):
            raise ValueError("predicate must be an object")
        operation, expected = predicate.get("operation"), predicate.get("expected")
        if operation == "equals":
            return observed == expected
        if operation == "regex" and isinstance(observed, str) and isinstance(expected, str):
            return _safe_fullmatch(expected, observed)
        raise ValueError(f"unsupported predicate operation: {operation}")

    def _value_within(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        source, state = self._state_source(assertion, record)
        path = assertion.path or str(assertion.parameters["path"])
        observed = resolve_path(state, path)
        if observed is _MISSING:
            return Evaluation(
                VerdictStatus.ERROR, "state.path_missing", f"state path {path} is missing"
            )
        if isinstance(observed, float):
            raise ValueError(
                "floating-point values are not supported for deterministic numeric grading"
            )
        if not isinstance(observed, int) or isinstance(observed, bool):
            raise ValueError("value_within observed value must be an integer")
        minimum, maximum = assertion.parameters.get("minimum"), assertion.parameters.get("maximum")
        tolerance = assertion.parameters.get("tolerance", 0)
        if not all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in (minimum, maximum, tolerance)
        ):
            raise ValueError("minimum, maximum, and tolerance must be integers")
        minimum_int = cast(int, minimum)
        maximum_int = cast(int, maximum)
        tolerance_int = cast(int, tolerance)
        passed = minimum_int - tolerance_int <= observed <= maximum_int + tolerance_int
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "value.within" if passed else "value.out_of_range",
            f"value at {path} is {'within' if passed else 'outside'} the inclusive range",
            {
                "minimum": minimum_int,
                "maximum": maximum_int,
                "tolerance": tolerance_int,
            },
            observed,
            (
                EvidenceReference(
                    kind=EvidenceKind.FINAL_SNAPSHOT
                    if source == "final"
                    else EvidenceKind.INITIAL_SNAPSHOT,
                    reference=source,
                    path=path,
                    value=observed,
                ),
            ),
        )

    def _matching_events(self, assertion: AssertionSpec, record: RunRecord) -> list[WorldEvent]:
        filters = _event_filters(assertion)
        return [event for event in record.events if _event_matches(event, filters)]

    def _action_exists(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        events = self._matching_events(assertion, record)
        passed = bool(events)
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "event.found" if passed else "event.missing",
            f"matching event count={len(events)}",
            {"minimum": 1},
            len(events),
            _event_refs(events[:10]),
        )

    def _no_action(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        events = self._matching_events(assertion, record)
        passed = not events
        evidence = _event_refs(events[:10]) or (
            EvidenceReference(
                kind=EvidenceKind.EVENT,
                reference="event_log",
                facts={"matching_count": 0, "filters": _event_filters(assertion)},
            ),
        )
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "event.absent" if passed else "event.forbidden",
            f"forbidden matching event count={len(events)}",
            0,
            len(events),
            evidence,
        )

    def _action_count(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        events = self._matching_events(assertion, record)
        minimum = assertion.parameters.get("minimum", assertion.parameters.get("count", 0))
        maximum = assertion.parameters.get("maximum", assertion.parameters.get("count", minimum))
        if not isinstance(minimum, int) or not isinstance(maximum, int):
            raise ValueError("action_count bounds must be integers")
        passed = minimum <= len(events) <= maximum
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "event.count_within" if passed else "event.count_outside",
            f"matching event count={len(events)}",
            {"minimum": minimum, "maximum": maximum},
            len(events),
            _event_refs(events[:10]),
        )

    def _sequence_before(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        first_filter = cast(dict[str, JsonValue], assertion.parameters["first"])
        second_filter = cast(dict[str, JsonValue], assertion.parameters["second"])
        first = [event for event in record.events if _event_matches(event, first_filter)]
        second = [event for event in record.events if _event_matches(event, second_filter)]
        if not first:
            return Evaluation(
                VerdictStatus.ERROR, "sequence.first_missing", "first event is missing"
            )
        if not second:
            return Evaluation(
                VerdictStatus.ERROR,
                "sequence.second_missing",
                "second event is missing",
                evidence=_event_refs(first[:1]),
            )
        left, right = first[0], second[0]
        gap = right.sequence - left.sequence
        maximum_gap = assertion.parameters.get("maximum_gap")
        passed = left.sequence < right.sequence and (
            maximum_gap is None or isinstance(maximum_gap, int) and gap <= maximum_gap
        )
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "sequence.ordered" if passed else "sequence.out_of_order",
            f"first sequence={left.sequence}, second sequence={right.sequence}, gap={gap}",
            {"strict": True, "maximum_gap": maximum_gap},
            {"first": left.sequence, "second": right.sequence, "gap": gap},
            _event_refs([left, right]),
        )

    def _changed_subset(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        changes = _changed_entities(record)
        allowed = assertion.parameters.get("allowed", [])
        if not isinstance(allowed, list):
            raise ValueError("changed_entities_subset allowed must be a list")
        unexpected = [change for change in changes if not self._change_allowed(change, allowed)]
        refs = tuple(
            EvidenceReference(
                kind=EvidenceKind.CHANGED_ENTITY,
                reference=f"{change['entity_type']}:{change['entity_id']}",
                facts=change,
            )
            for change in changes[:50]
        )
        passed = not unexpected
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "changes.allowed" if passed else "changes.outside_scope",
            f"changed entities={len(changes)}, unexpected={len(unexpected)}",
            cast(JsonValue, allowed),
            cast(JsonValue, unexpected),
            refs,
        )

    @staticmethod
    def _change_allowed(change: dict[str, JsonValue], allowed: list[JsonValue]) -> bool:
        for rule in allowed:
            if not isinstance(rule, dict) or rule.get("entity_type") != change["entity_type"]:
                continue
            if rule.get("entity_id") not in {None, "*", change["entity_id"]}:
                continue
            fields = rule.get("fields", ["*"])
            if isinstance(fields, list) and (
                "*" in fields
                or set(cast(list[str], change["fields"])).issubset(set(cast(list[str], fields)))
            ):
                return True
        return False

    def _resource_within(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        metric = assertion.parameters.get("metric")
        values: dict[str, int | None] = {
            "wall_time_ms": record.total_duration_ms,
            "model_tokens": record.model_tokens,
            "cost_minor": record.cost_minor,
            "tool_calls": record.tool_call_count,
            "mutations": record.mutation_count,
            "retries": sum(
                turn.tool_result is not None and turn.tool_result.retry_of is not None
                for turn in record.turns
            ),
            "duplicate_calls": self._duplicate_calls(record),
        }
        if not isinstance(metric, str) or metric not in values:
            raise ValueError(f"unknown resource metric: {metric}")
        observed = values[metric]
        if observed is None:
            return Evaluation(
                VerdictStatus.ERROR,
                "resource.unavailable",
                f"required resource metric {metric} is unavailable",
            )
        minimum = assertion.parameters.get("minimum", 0)
        maximum = assertion.parameters.get("maximum")
        if not isinstance(minimum, int) or not isinstance(maximum, int):
            raise ValueError("resource bounds must be integers")
        passed = minimum <= observed <= maximum
        ref = EvidenceReference(kind=EvidenceKind.RESOURCE, reference=metric, value=observed)
        return Evaluation(
            VerdictStatus.PASS if passed else VerdictStatus.FAIL,
            "resource.within" if passed else "resource.exceeded",
            f"resource {metric}={observed}",
            {"minimum": minimum, "maximum": maximum},
            observed,
            (ref,),
        )

    @staticmethod
    def _duplicate_calls(record: RunRecord) -> int:
        fingerprints: list[str] = []
        for turn in record.turns:
            if turn.tool_call is not None:
                fingerprints.append(
                    turn.tool_call.tool_name + ":" + str(sorted(turn.tool_call.arguments.items()))
                )
        return len(fingerprints) - len(set(fingerprints))

    def _policy(self, assertion: AssertionSpec, record: RunRecord) -> Evaluation:
        rule = str(assertion.parameters["rule"])
        result: PolicyEvaluation = evaluate_policy(rule, record)
        refs = tuple(
            EvidenceReference(
                kind=EvidenceKind.POLICY,
                reference=rule,
                facts=result.facts,
            )
            for _ in (0,)
        ) + _event_refs(list(result.events))
        return Evaluation(
            VerdictStatus.PASS if result.passed else VerdictStatus.FAIL,
            result.code,
            result.explanation,
            True,
            result.passed,
            refs,
        )

    @staticmethod
    def _verdict(assertion: AssertionSpec, evaluation: Evaluation) -> Verdict:
        assertion_type = str(assertion.type)
        policy = assertion.parameters.get("rule") if assertion_type == "policy" else None
        identity = (
            f"{assertion.id}:{evaluation.status.value}:{evaluation.code}:"
            f"{evaluation.expected}:{evaluation.observed}"
        )
        return Verdict(
            id=VerdictId(f"ver_{uuid5(NAMESPACE_URL, identity).hex}"),
            assertion_id=assertion.id,
            status=evaluation.status,
            severity=assertion.severity,
            message=evaluation.explanation,
            evidence=tuple(ref.reference for ref in evaluation.evidence),
            reason_code=evaluation.code,
            expected=evaluation.expected,
            observed=evaluation.observed,
            evidence_refs=evaluation.evidence,
            evaluator_version=EVALUATOR_VERSION,
            assertion_type=assertion_type,
            policy_name=str(policy) if policy is not None else None,
            required=assertion.required,
        )


_MAX_REGEX_PATTERN = 256
_MAX_REGEX_INPUT = 10_000
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)[+*{]")


def _safe_fullmatch(pattern: str, value: str) -> bool:
    """Bound core regex evaluation and reject common catastrophic forms."""
    if len(pattern) > _MAX_REGEX_PATTERN or len(value) > _MAX_REGEX_INPUT:
        raise ValueError("regex pattern or input exceeds safe evaluation limits")
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError("regex contains a nested quantifier")
    return re.fullmatch(pattern, value) is not None
