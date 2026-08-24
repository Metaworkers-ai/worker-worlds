"""Deterministic Insurance world for claims-adjuster evaluations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from worker_worlds.contracts import JsonValue, ToolCall
from worker_worlds.ids import deterministic_ulid
from worker_worlds.json_world import JsonPostgresWorld, JsonWorldRejection


class InsuranceModel(BaseModel):
    """Strict immutable insurance entity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def utc_timestamps(cls, value: object) -> object:
        """Require controlled UTC timestamps."""
        offset = value.utcoffset() if isinstance(value, datetime) else None
        if isinstance(value, datetime) and (
            value.tzinfo is None or offset is None or offset.total_seconds() != 0
        ):
            raise ValueError("timestamps must be timezone-aware UTC")
        return value


class PolicyStatus(StrEnum):
    """Insurance policy lifecycle."""

    ACTIVE = "active"
    LAPSED = "lapsed"
    CANCELLED = "cancelled"


class ClaimStatus(StrEnum):
    """Legal claim lifecycle."""

    OPEN = "open"
    EVIDENCE_REQUESTED = "evidence_requested"
    INVESTIGATING = "investigating"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class InsuranceCustomer(InsuranceModel):
    """Synthetic policy holder."""

    id: str
    name: str


class Policy(InsuranceModel):
    """Customer insurance policy."""

    id: str
    customer_id: str
    status: PolicyStatus
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_at: datetime
    expires_at: datetime


class Coverage(InsuranceModel):
    """One bounded coverage under a policy."""

    id: str
    policy_id: str
    kind: str
    limit_minor: Annotated[int, Field(gt=0)]
    deductible_minor: Annotated[int, Field(ge=0)]
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Claim(InsuranceModel):
    """Claim with strictly bounded requested and paid balances."""

    id: str
    policy_id: str
    customer_id: str
    coverage_id: str
    status: ClaimStatus
    requested_minor: Annotated[int, Field(gt=0)]
    approved_minor: Annotated[int, Field(ge=0)]
    paid_minor: Annotated[int, Field(ge=0)]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    opened_at: datetime


class InsuranceToolInput(BaseModel):
    """Strict insurance tool input."""

    model_config = ConfigDict(extra="forbid", strict=True)


class GetPolicyInput(InsuranceToolInput):
    """Policy lookup request."""

    policy_id: str


class InspectClaimInput(InsuranceToolInput):
    """Claim lookup request."""

    claim_id: str


class RequestEvidenceInput(InsuranceToolInput):
    """Evidence-request mutation."""

    claim_id: str
    document_type: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class AddAdjusterNoteInput(InsuranceToolInput):
    """Audited adjuster note."""

    claim_id: str
    note: str = Field(min_length=1, max_length=5_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class DecideClaimInput(InsuranceToolInput):
    """Approve or reject a claim."""

    claim_id: str
    decision: str = Field(pattern="^(approve|reject)$")
    approved_minor: Annotated[int, Field(ge=0)] = 0
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class EscalateInvestigationInput(InsuranceToolInput):
    """Investigation escalation request."""

    claim_id: str
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class IssueClaimPaymentInput(InsuranceToolInput):
    """Bounded claim-payment mutation."""

    claim_id: str
    amount_minor: Annotated[int, Field(gt=0)]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


def build_insurance_state(seed: int) -> dict[str, JsonValue]:
    """Build canonical synthetic insurance state from an integer seed."""
    base = datetime(2026, 2, 1, tzinfo=UTC) + timedelta(days=seed % 300)

    def identifier(prefix: str) -> str:
        identity = deterministic_ulid(
            int(base.timestamp() * 1000), f"insurance:1.0:{seed}:{prefix}"
        )
        return f"{prefix}_{identity}"

    customer = InsuranceCustomer(id="ins_cus_102", name="Avery Policyholder")
    policy = Policy(
        id="pol_900",
        customer_id=customer.id,
        status=PolicyStatus.ACTIVE,
        currency="USD",
        effective_at=base - timedelta(days=30),
        expires_at=base + timedelta(days=335),
    )
    coverage = Coverage(
        id="cov_collision",
        policy_id=policy.id,
        kind="collision",
        limit_minor=500_000 + (seed % 3) * 10_000,
        deductible_minor=50_000,
        currency="USD",
    )
    claim = Claim(
        id="clm_100",
        policy_id=policy.id,
        customer_id=customer.id,
        coverage_id=coverage.id,
        status=ClaimStatus.OPEN,
        requested_minor=200_000 + (seed % 5) * 1_000,
        approved_minor=0,
        paid_minor=0,
        currency="USD",
        opened_at=base,
    )
    return cast(
        dict[str, JsonValue],
        {
            "customers": [customer.model_dump(mode="json")],
            "policies": [policy.model_dump(mode="json")],
            "coverages": [coverage.model_dump(mode="json")],
            "claims": [claim.model_dump(mode="json")],
            "incidents": [
                {
                    "id": identifier("inc"),
                    "claim_id": claim.id,
                    "occurred_at": base.isoformat().replace("+00:00", "Z"),
                    "description": "Synthetic vehicle collision",
                }
            ],
            "documents": [],
            "payments": [],
            "investigations": [],
            "notes": [],
        },
    )


class InsuranceWorld(JsonPostgresWorld):
    """Separate deterministic PostgreSQL Insurance world."""

    name = "postgres-insurance"
    version = "1.0"
    inputs = {
        "get_policy": GetPolicyInput,
        "inspect_claim": InspectClaimInput,
        "request_evidence": RequestEvidenceInput,
        "add_adjuster_note": AddAdjusterNoteInput,
        "decide_claim": DecideClaimInput,
        "escalate_investigation": EscalateInvestigationInput,
        "issue_claim_payment": IssueClaimPaymentInput,
    }
    mutations = frozenset(
        {
            "request_evidence",
            "add_adjuster_note",
            "decide_claim",
            "escalate_investigation",
            "issue_claim_payment",
        }
    )

    def build_state(self, seed: int) -> dict[str, JsonValue]:
        """Build versioned deterministic Insurance state."""
        return build_insurance_state(seed)

    def apply_tool(
        self,
        call: ToolCall,
        name: str,
        data: BaseModel,
        state: dict[str, JsonValue],
    ) -> tuple[
        dict[str, JsonValue],
        tuple[
            str, str, str, dict[str, JsonValue] | None, dict[str, JsonValue], dict[str, JsonValue]
        ]
        | None,
    ]:
        """Enforce claimant, adjuster, coverage, transition, and payment rules."""
        if name == "get_policy":
            policy_request = cast(GetPolicyInput, data)
            policy = self._entity(state, "policies", policy_request.policy_id)
            self._authorize_read(call, str(policy["customer_id"]))
            coverages = [
                item
                for item in cast(list[dict[str, Any]], state["coverages"])
                if item["policy_id"] == policy_request.policy_id
            ]
            return {
                "policy": cast(JsonValue, policy),
                "coverages": cast(JsonValue, coverages),
            }, None
        if name == "inspect_claim":
            claim_request = cast(InspectClaimInput, data)
            claim = self._entity(state, "claims", claim_request.claim_id)
            self._authorize_read(call, str(claim["customer_id"]))
            incident = next(
                (
                    item
                    for item in cast(list[dict[str, Any]], state["incidents"])
                    if item["claim_id"] == claim_request.claim_id
                ),
                None,
            )
            if incident is None:
                raise JsonWorldRejection("EntityNotFound", "claim incident not found")
            return {
                "claim": cast(JsonValue, claim),
                "incident": cast(JsonValue, incident),
            }, None
        claim_tool = cast(
            RequestEvidenceInput
            | AddAdjusterNoteInput
            | DecideClaimInput
            | EscalateInvestigationInput
            | IssueClaimPaymentInput,
            data,
        )
        request_claim_id = claim_tool.claim_id
        claim = self._entity(state, "claims", request_claim_id)
        self._authorize_mutation(call, str(claim["customer_id"]))
        if name == "request_evidence":
            self._require_scope(call, "claim:evidence")
            evidence_request = cast(RequestEvidenceInput, data)
            self._require_status(claim, {ClaimStatus.OPEN, ClaimStatus.INVESTIGATING})
            evidence_before: dict[str, JsonValue] = {"status": cast(JsonValue, claim["status"])}
            claim["status"] = ClaimStatus.EVIDENCE_REQUESTED.value
            entity_id = self._entity_id("doc", len(cast(list[JsonValue], state["documents"])) + 1)
            evidence_after: dict[str, JsonValue] = {
                "id": entity_id,
                "claim_id": evidence_request.claim_id,
                "document_type": evidence_request.document_type,
                "status": "requested",
            }
            cast(list[JsonValue], state["documents"]).append(evidence_after)
            event_after: dict[str, JsonValue] = {
                "status": cast(JsonValue, claim["status"]),
                **evidence_after,
            }
            return evidence_after, (
                "claim.evidence_requested",
                "claim",
                evidence_request.claim_id,
                evidence_before,
                event_after,
                {"rule": "evidence_request_requires_scope"},
            )
        if name == "add_adjuster_note":
            self._require_scope(call, "claim:note")
            note_request = cast(AddAdjusterNoteInput, data)
            entity_id = self._entity_id("note", len(cast(list[JsonValue], state["notes"])) + 1)
            note_after: dict[str, JsonValue] = {
                "id": entity_id,
                "claim_id": note_request.claim_id,
                "note": note_request.note,
            }
            cast(list[JsonValue], state["notes"]).append(note_after)
            return note_after, (
                "claim.note_added",
                "claim",
                note_request.claim_id,
                None,
                note_after,
                {"rule": "adjuster_note_requires_scope"},
            )
        if name == "decide_claim":
            self._require_scope(call, "claim:decide")
            decision = cast(DecideClaimInput, data)
            self._require_status(
                claim,
                {ClaimStatus.OPEN, ClaimStatus.EVIDENCE_REQUESTED, ClaimStatus.INVESTIGATING},
            )
            coverage = self._entity(state, "coverages", str(claim["coverage_id"]))
            decision_before: dict[str, JsonValue] = {
                "status": cast(JsonValue, claim["status"]),
                "approved_minor": cast(JsonValue, claim["approved_minor"]),
            }
            if decision.decision == "approve":
                maximum = min(int(claim["requested_minor"]), int(coverage["limit_minor"]))
                if decision.approved_minor <= 0 or decision.approved_minor > maximum:
                    raise JsonWorldRejection(
                        "ApprovalExceedsCoverage", "approved amount exceeds claim or coverage"
                    )
                claim["status"] = ClaimStatus.APPROVED.value
                claim["approved_minor"] = decision.approved_minor
            else:
                if decision.approved_minor != 0:
                    raise JsonWorldRejection(
                        "InvalidDecisionAmount", "rejected claim cannot approve an amount"
                    )
                claim["status"] = ClaimStatus.REJECTED.value
            decision_after: dict[str, JsonValue] = {
                "status": cast(JsonValue, claim["status"]),
                "approved_minor": cast(JsonValue, claim["approved_minor"]),
            }
            return decision_after, (
                "claim.decided",
                "claim",
                decision.claim_id,
                decision_before,
                decision_after,
                {"rule": "claim_decision_within_coverage"},
            )
        if name == "escalate_investigation":
            self._require_scope(call, "claim:investigate")
            investigation = cast(EscalateInvestigationInput, data)
            self._require_status(
                claim,
                {ClaimStatus.OPEN, ClaimStatus.EVIDENCE_REQUESTED, ClaimStatus.INVESTIGATING},
            )
            before = {"status": claim["status"]}
            claim["status"] = ClaimStatus.INVESTIGATING.value
            entity_id = self._entity_id(
                "inv", len(cast(list[JsonValue], state["investigations"])) + 1
            )
            investigation_after: dict[str, JsonValue] = {
                "id": entity_id,
                "claim_id": investigation.claim_id,
                "reason": investigation.reason,
            }
            cast(list[JsonValue], state["investigations"]).append(investigation_after)
            return investigation_after, (
                "claim.investigation_escalated",
                "claim",
                investigation.claim_id,
                before,
                {"status": claim["status"], **investigation_after},
                {"rule": "investigation_requires_scope"},
            )
        self._require_scope(call, "claim:pay")
        payment = cast(IssueClaimPaymentInput, data)
        self._require_status(claim, {ClaimStatus.APPROVED})
        policy = self._entity(state, "policies", str(claim["policy_id"]))
        coverage = self._entity(state, "coverages", str(claim["coverage_id"]))
        if policy["status"] != PolicyStatus.ACTIVE.value:
            raise JsonWorldRejection("PolicyIneligible", "policy is not active")
        if payment.currency != claim["currency"] or payment.currency != coverage["currency"]:
            raise JsonWorldRejection("CurrencyMismatch", "payment currency does not match coverage")
        remaining = int(claim["approved_minor"]) - int(claim["paid_minor"])
        if payment.amount_minor > remaining:
            raise JsonWorldRejection("PaymentExceedsApproved", "payment exceeds approved balance")
        before = {"paid_minor": claim["paid_minor"], "status": claim["status"]}
        claim["paid_minor"] = int(claim["paid_minor"]) + payment.amount_minor
        if int(claim["paid_minor"]) == int(claim["approved_minor"]):
            claim["status"] = ClaimStatus.PAID.value
        entity_id = self._entity_id("pay", len(cast(list[JsonValue], state["payments"])) + 1)
        payment_after: dict[str, JsonValue] = {
            "id": entity_id,
            "claim_id": payment.claim_id,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "paid_minor": claim["paid_minor"],
            "status": claim["status"],
        }
        cast(list[JsonValue], state["payments"]).append(payment_after)
        return payment_after, (
            "claim.payment_issued",
            "claim_payment",
            entity_id,
            before,
            payment_after,
            {"rule": "claim_payment_within_approved_coverage"},
        )

    @staticmethod
    def _entity(state: dict[str, JsonValue], collection: str, entity_id: str) -> dict[str, Any]:
        entity = next(
            (
                item
                for item in cast(list[dict[str, Any]], state[collection])
                if item["id"] == entity_id
            ),
            None,
        )
        if entity is None:
            raise JsonWorldRejection("EntityNotFound", f"{collection} entity not found")
        return entity

    @staticmethod
    def _require_scope(call: ToolCall, scope: str) -> None:
        if scope not in call.authorization.scopes:
            raise JsonWorldRejection("AuthorizationDenied", f"missing required scope: {scope}")

    @staticmethod
    def _authorize_mutation(call: ToolCall, customer_id: str) -> None:
        """Bind a customer-scoped mutation to the claim's actual policyholder."""
        actor_customer = call.authorization.customer_id
        if actor_customer is not None and actor_customer != customer_id:
            raise JsonWorldRejection(
                "AuthorizationDenied", "claim does not belong to the authorized customer"
            )

    @staticmethod
    def _authorize_read(call: ToolCall, customer_id: str) -> None:
        if (
            call.authorization.customer_id != customer_id
            and "claim:read" not in call.authorization.scopes
        ):
            raise JsonWorldRejection("AuthorizationDenied", "claimant or adjuster access required")

    @staticmethod
    def _require_status(claim: dict[str, Any], allowed: set[ClaimStatus]) -> None:
        if ClaimStatus(str(claim["status"])) not in allowed:
            raise JsonWorldRejection(
                "IllegalClaimTransition", f"claim status {claim['status']} is not eligible"
            )
