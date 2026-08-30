"""Deterministic Insurance world for claims-adjuster and claims-analyst evaluations."""

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


class RecommendationKind(StrEnum):
    """Non-binding analyst disposition recommendation."""

    APPROVE = "approve"
    PARTIAL_APPROVE = "partial_approve"
    DENY = "deny"
    INVESTIGATE = "investigate"
    MORE_INFORMATION = "more_information"


class EvidenceVerificationState(StrEnum):
    """Verification lifecycle of a submitted evidence item."""

    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


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
    exclusions: tuple[str, ...] = ()
    waiting_period_days: Annotated[int, Field(ge=0)] = 0
    sublimits: dict[str, Annotated[int, Field(gt=0)]] = Field(default_factory=dict)


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
    claim_type: str | None = None
    loss_date: datetime | None = None
    report_date: datetime | None = None
    location: str | None = None
    cause: str | None = None
    claimed_minor: Annotated[int, Field(ge=0)] | None = None
    assigned_analyst_id: str | None = None
    related_claim_ids: tuple[str, ...] = ()


class Incident(InsuranceModel):
    """Loss event backing a claim."""

    id: str
    claim_id: str
    occurred_at: datetime
    description: str


class EvidenceStatus(StrEnum):
    """Lifecycle of a requested or submitted evidence item."""

    REQUESTED = "requested"
    RECEIVED = "received"


class EvidenceItem(InsuranceModel):
    """One evidence document attached to a claim."""

    id: str
    claim_id: str
    document_type: str
    status: EvidenceStatus
    source: str
    verification_state: EvidenceVerificationState
    trust_classification: str


class RiskSeverity(StrEnum):
    """Bounded fraud/anomaly risk-flag severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskFlag(InsuranceModel):
    """A fraud/anomaly referral raised against a claim."""

    id: str
    claim_id: str
    reason_code: str
    severity: RiskSeverity


class AnalystRecommendation(InsuranceModel):
    """A non-binding analyst disposition recommendation."""

    id: str
    claim_id: str
    recommendation: RecommendationKind
    reason_code: str
    recommended_minor: Annotated[int, Field(ge=0)]
    binding: bool


class CoverageAnalysisResult(InsuranceModel):
    """Deterministic coverage-exposure calculation for one claim."""

    claim_id: str
    coverage_id: str
    requested_minor: Annotated[int, Field(ge=0)]
    limit_minor: Annotated[int, Field(gt=0)]
    deductible_minor: Annotated[int, Field(ge=0)]
    sublimit_minor: Annotated[int, Field(gt=0)] | None
    payable_minor: Annotated[int, Field(ge=0)]
    exceeds_limit: bool
    exceeds_sublimit: bool
    within_deductible: bool
    policy_active: bool
    within_policy_period: bool
    chronology_valid: bool
    eligible: bool


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


# --- Claims Analyst tool inputs -------------------------------------------------


class SearchClaimsInput(InsuranceToolInput):
    """Search claims within the caller's authorized boundary."""

    customer_id: str | None = None
    status: str | None = None


class GetCoverageInput(InsuranceToolInput):
    """Coverage lookup request."""

    coverage_id: str


class InspectIncidentInput(InsuranceToolInput):
    """Incident lookup request."""

    claim_id: str


class ListClaimEvidenceInput(InsuranceToolInput):
    """List evidence submitted for a claim."""

    claim_id: str


class GetEvidenceInput(InsuranceToolInput):
    """Single evidence-item lookup request."""

    document_id: str


class GetRelatedClaimsInput(InsuranceToolInput):
    """Lookup of claims linked to a claim as related or duplicate."""

    claim_id: str


class CalculateCoverageAnalysisInput(InsuranceToolInput):
    """Deterministic coverage-analysis calculation request."""

    claim_id: str


class AddAnalystNoteInput(InsuranceToolInput):
    """Audited analyst note distinct from adjuster notes."""

    claim_id: str
    note: str = Field(min_length=1, max_length=5_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class RecordClaimRecommendationInput(InsuranceToolInput):
    """Non-binding analyst disposition recommendation."""

    claim_id: str
    recommendation: str = Field(
        pattern="^(approve|partial_approve|deny|investigate|more_information)$"
    )
    reason_code: str = Field(min_length=1, max_length=100)
    recommended_minor: Annotated[int, Field(ge=0)] = 0
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class FlagClaimForReviewInput(InsuranceToolInput):
    """Fraud/anomaly risk flag referral."""

    claim_id: str
    reason_code: str = Field(min_length=1, max_length=100)
    severity: str = Field(pattern="^(low|medium|high)$", default="medium")
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


# Seeds at or above this value select the richer Claims Analyst fixture. Seeds
# below it always build the original single-claim Claims Adjuster fixture,
# byte-for-byte unchanged, so existing claims-adjuster scenarios never drift.
ANALYST_SEED_FLOOR = 8000


def build_insurance_state(seed: int) -> dict[str, JsonValue]:
    """Build canonical synthetic insurance state from an integer seed."""
    if seed >= ANALYST_SEED_FLOOR:
        return _build_claims_analyst_state(seed)
    return _build_legacy_insurance_state(seed)


def _build_legacy_insurance_state(seed: int) -> dict[str, JsonValue]:
    """Build the original claims-adjuster fixture, unchanged since release."""
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
                Incident(
                    id=identifier("inc"),
                    claim_id=claim.id,
                    occurred_at=base,
                    description="Synthetic vehicle collision",
                ).model_dump(mode="json")
            ],
            "documents": [],
            "payments": [],
            "investigations": [],
            "notes": [],
            "analyst_notes": [],
            "recommendations": [],
            "risk_flags": [],
        },
    )


def _build_claims_analyst_state(seed: int) -> dict[str, JsonValue]:
    """Build a richer deterministic fixture for Claims Analyst scenarios.

    Adds a second, related claim on the same policy (for duplicate/related-claim
    and fraud-triage scenarios), coverage exclusions/sublimits/waiting period,
    claim loss/report facts, an analyst assignment, and one pre-submitted
    evidence item -- all still pure functions of ``seed``.
    """
    base = datetime(2026, 3, 1, tzinfo=UTC) + timedelta(days=seed % 300)

    def identifier(prefix: str, salt: str) -> str:
        identity = deterministic_ulid(
            int(base.timestamp() * 1000), f"insurance:1.0:{seed}:{prefix}:{salt}"
        )
        return f"{prefix}_{identity}"

    # Seed bands select a fixture *shape* for the primary claim (clm_100),
    # keeping every band a pure function of seed. Seeds below 8500 keep the
    # original baseline shape byte-for-byte (existing scenarios 001-025 and
    # any future baseline-band scenario). Bands above it deliberately produce
    # boundary conditions -- below deductible, above limit, above sublimit,
    # non-OPEN lifecycle status, outside the policy period, an invalid
    # loss/report chronology, or a shared-incident duplicate -- so read tools
    # exercise real boundary evidence rather than restating fixed numbers in
    # prose. Status/timing bands (8800+) don't change claimed/requested
    # amounts, only the axis they're named for.
    primary_status = ClaimStatus.OPEN
    policy_effective_offset_days = 60
    chronology_invalid = False
    shared_incident = False
    policy_status = PolicyStatus.ACTIVE
    if 8500 <= seed < 8600:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 20_000 + (seed % 5) * 2_000  # always < 50_000 deductible
    elif 8600 <= seed < 8700:
        claim_type = "collision"
        cause = "multi_vehicle_pileup"
        primary_requested = 600_000 + (seed % 5) * 5_000  # always > coverage limit
    elif 8700 <= seed < 8800:
        claim_type = "glass"
        cause = "windshield_cracked_by_road_debris"
        primary_requested = 130_000 + (seed % 5) * 2_000  # always > 100_000 sublimit
    elif 8800 <= seed < 8900:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        primary_status = ClaimStatus.EVIDENCE_REQUESTED  # lifecycle: awaiting evidence
    elif 8900 <= seed < 9000:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        primary_status = ClaimStatus.INVESTIGATING  # lifecycle: under review
    elif 9000 <= seed < 9100:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        primary_status = ClaimStatus.APPROVED  # lifecycle: already decided
    elif 9100 <= seed < 9200:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        primary_status = ClaimStatus.REJECTED  # lifecycle: closed
    elif 9200 <= seed < 9300:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        policy_effective_offset_days = -20  # policy became effective AFTER the loss
    elif 9300 <= seed < 9400:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        chronology_invalid = True  # loss reported before it occurred
    elif 9400 <= seed < 9500:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        shared_incident = True  # related claim reports the same incident
    elif 9500 <= seed < 9600:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000
        policy_status = PolicyStatus.LAPSED  # policy coverage/eligibility: lapsed policy
    else:
        claim_type = "collision"
        cause = "rear_end_collision"
        primary_requested = 200_000 + (seed % 5) * 1_000

    customer = InsuranceCustomer(id="ins_cus_102", name="Avery Policyholder")
    policy = Policy(
        id="pol_900",
        customer_id=customer.id,
        status=policy_status,
        currency="USD",
        effective_at=base - timedelta(days=policy_effective_offset_days),
        expires_at=base + timedelta(days=305),
    )
    coverage = Coverage(
        id="cov_collision",
        policy_id=policy.id,
        kind="collision",
        limit_minor=500_000 + (seed % 3) * 10_000,
        deductible_minor=50_000,
        currency="USD",
        exclusions=("racing", "wear_and_tear"),
        waiting_period_days=14,
        sublimits={"glass": 100_000},
    )
    # A normal claim reports the loss before it's reported, before it's opened.
    # `chronology_invalid` inverts loss/report so `inspect_incident` can flag
    # an impossible narrative purely from state, no prose required.
    claim_loss_date = base - timedelta(days=2) if chronology_invalid else base - timedelta(days=3)
    claim_report_date = base - timedelta(days=3) if chronology_invalid else base - timedelta(days=2)
    related_loss_date = claim_loss_date if shared_incident else base - timedelta(days=203)
    related_location = "Austin, TX"
    claim = Claim(
        id="clm_100",
        policy_id=policy.id,
        customer_id=customer.id,
        coverage_id=coverage.id,
        status=primary_status,
        requested_minor=primary_requested,
        approved_minor=(
            min(primary_requested, 500_000 + (seed % 3) * 10_000)
            if primary_status == ClaimStatus.APPROVED
            else 0
        ),
        paid_minor=0,
        currency="USD",
        opened_at=base,
        claim_type=claim_type,
        loss_date=claim_loss_date,
        report_date=claim_report_date,
        location="Austin, TX",
        cause=cause,
        claimed_minor=primary_requested,
        assigned_analyst_id="analyst_erin_kade",
        related_claim_ids=("clm_101",),
    )
    related_claim = Claim(
        id="clm_101",
        policy_id=policy.id,
        customer_id=customer.id,
        coverage_id=coverage.id,
        status=ClaimStatus.OPEN,
        requested_minor=185_000,
        approved_minor=0,
        paid_minor=0,
        currency="USD",
        opened_at=base - timedelta(days=200) if not shared_incident else base,
        claim_type="collision",
        loss_date=related_loss_date,
        report_date=base - timedelta(days=202) if not shared_incident else base - timedelta(days=1),
        location=related_location,
        cause="rear_end_collision",
        claimed_minor=185_000,
        assigned_analyst_id="analyst_erin_kade",
        related_claim_ids=("clm_100",),
    )
    evidence_id = identifier("doc", "repair-estimate")
    return cast(
        dict[str, JsonValue],
        {
            "customers": [customer.model_dump(mode="json")],
            "policies": [policy.model_dump(mode="json")],
            "coverages": [coverage.model_dump(mode="json")],
            "claims": [claim.model_dump(mode="json"), related_claim.model_dump(mode="json")],
            "incidents": [
                Incident(
                    id=identifier("inc", "clm-100"),
                    claim_id=claim.id,
                    occurred_at=claim_loss_date,
                    description="Synthetic rear-end collision, Austin TX",
                ).model_dump(mode="json"),
                Incident(
                    id=identifier("inc", "clm-101"),
                    claim_id=related_claim.id,
                    occurred_at=related_loss_date,
                    description="Synthetic rear-end collision, Austin TX (prior claim)",
                ).model_dump(mode="json"),
            ],
            "documents": [
                EvidenceItem(
                    id=evidence_id,
                    claim_id=claim.id,
                    document_type="repair_estimate",
                    status=EvidenceStatus.RECEIVED,
                    source="repair_shop",
                    verification_state=EvidenceVerificationState.VERIFIED,
                    trust_classification="verified_third_party",
                ).model_dump(mode="json")
            ],
            "payments": [],
            "investigations": [],
            "notes": [],
            "analyst_notes": [],
            "recommendations": [],
            "risk_flags": [],
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
        "search_claims": SearchClaimsInput,
        "get_coverage": GetCoverageInput,
        "inspect_incident": InspectIncidentInput,
        "list_claim_evidence": ListClaimEvidenceInput,
        "get_evidence": GetEvidenceInput,
        "get_related_claims": GetRelatedClaimsInput,
        "calculate_coverage_analysis": CalculateCoverageAnalysisInput,
        "add_analyst_note": AddAnalystNoteInput,
        "record_claim_recommendation": RecordClaimRecommendationInput,
        "flag_claim_for_review": FlagClaimForReviewInput,
    }
    mutations = frozenset(
        {
            "request_evidence",
            "add_adjuster_note",
            "decide_claim",
            "escalate_investigation",
            "issue_claim_payment",
            "add_analyst_note",
            "record_claim_recommendation",
            "flag_claim_for_review",
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
        if name == "inspect_incident":
            incident_request = cast(InspectIncidentInput, data)
            claim = self._entity(state, "claims", incident_request.claim_id)
            self._authorize_read(call, str(claim["customer_id"]))
            incident = next(
                (
                    item
                    for item in cast(list[dict[str, Any]], state["incidents"])
                    if item["claim_id"] == incident_request.claim_id
                ),
                None,
            )
            if incident is None:
                raise JsonWorldRejection("EntityNotFound", "claim incident not found")
            return {"incident": cast(JsonValue, incident)}, None
        if name == "search_claims":
            search_request = cast(SearchClaimsInput, data)
            if search_request.customer_id is not None:
                self._authorize_read(call, search_request.customer_id)
            elif (
                call.authorization.customer_id is None
                and "claim:read" not in call.authorization.scopes
            ):
                raise JsonWorldRejection(
                    "AuthorizationDenied", "search requires a customer scope or claim:read"
                )
            effective_customer = search_request.customer_id or call.authorization.customer_id
            matches = [
                item
                for item in cast(list[dict[str, Any]], state["claims"])
                if (effective_customer is None or item["customer_id"] == effective_customer)
                and (search_request.status is None or item["status"] == search_request.status)
            ]
            return {"claims": cast(JsonValue, matches)}, None
        if name == "get_coverage":
            coverage_request = cast(GetCoverageInput, data)
            coverage = self._entity(state, "coverages", coverage_request.coverage_id)
            policy = self._entity(state, "policies", str(coverage["policy_id"]))
            self._authorize_read(call, str(policy["customer_id"]))
            return {"coverage": cast(JsonValue, coverage)}, None
        if name == "list_claim_evidence":
            evidence_list_request = cast(ListClaimEvidenceInput, data)
            claim = self._entity(state, "claims", evidence_list_request.claim_id)
            self._authorize_read(call, str(claim["customer_id"]))
            documents = [
                item
                for item in cast(list[dict[str, Any]], state["documents"])
                if item["claim_id"] == evidence_list_request.claim_id
            ]
            return {"documents": cast(JsonValue, documents)}, None
        if name == "get_evidence":
            evidence_lookup = cast(GetEvidenceInput, data)
            document = self._entity(state, "documents", evidence_lookup.document_id)
            claim = self._entity(state, "claims", str(document["claim_id"]))
            self._authorize_read(call, str(claim["customer_id"]))
            return {"document": cast(JsonValue, document)}, None
        if name == "get_related_claims":
            related_request = cast(GetRelatedClaimsInput, data)
            claim = self._entity(state, "claims", related_request.claim_id)
            self._authorize_read(call, str(claim["customer_id"]))
            related_ids = cast(list[str], claim.get("related_claim_ids", []))
            related = [
                item
                for item in cast(list[dict[str, Any]], state["claims"])
                if item["id"] in related_ids
            ]
            return {"related_claims": cast(JsonValue, related)}, None
        if name == "calculate_coverage_analysis":
            analysis_request = cast(CalculateCoverageAnalysisInput, data)
            claim = self._entity(state, "claims", analysis_request.claim_id)
            self._authorize_read(call, str(claim["customer_id"]))
            coverage = self._entity(state, "coverages", str(claim["coverage_id"]))
            policy = self._entity(state, "policies", str(claim["policy_id"]))
            requested = int(claim["requested_minor"])
            limit = int(coverage["limit_minor"])
            deductible = int(coverage["deductible_minor"])
            sublimits = cast(dict[str, int], coverage.get("sublimits") or {})
            claim_type = claim.get("claim_type")
            sublimit = sublimits.get(str(claim_type)) if claim_type is not None else None
            capped = min(requested, limit)
            exceeds_sublimit = sublimit is not None and requested > int(sublimit)
            if sublimit is not None:
                capped = min(capped, int(sublimit))
            payable = max(0, capped - deductible)
            opened_at = datetime.fromisoformat(str(claim["opened_at"]).replace("Z", "+00:00"))
            effective_at = datetime.fromisoformat(
                str(policy["effective_at"]).replace("Z", "+00:00")
            )
            expires_at = datetime.fromisoformat(str(policy["expires_at"]).replace("Z", "+00:00"))
            within_policy_period = effective_at <= opened_at <= expires_at
            policy_active = policy["status"] == PolicyStatus.ACTIVE.value
            loss_date_raw = claim.get("loss_date")
            report_date_raw = claim.get("report_date")
            chronology_valid = True
            if loss_date_raw is not None and report_date_raw is not None:
                loss_date = datetime.fromisoformat(str(loss_date_raw).replace("Z", "+00:00"))
                report_date = datetime.fromisoformat(str(report_date_raw).replace("Z", "+00:00"))
                chronology_valid = loss_date <= report_date <= opened_at
            analysis = CoverageAnalysisResult(
                claim_id=str(claim["id"]),
                coverage_id=str(coverage["id"]),
                requested_minor=requested,
                limit_minor=limit,
                deductible_minor=deductible,
                sublimit_minor=sublimit,
                payable_minor=payable,
                exceeds_limit=requested > limit,
                exceeds_sublimit=exceeds_sublimit,
                within_deductible=requested <= deductible,
                policy_active=policy_active,
                within_policy_period=within_policy_period,
                chronology_valid=chronology_valid,
                eligible=(
                    policy_active
                    and within_policy_period
                    and chronology_valid
                    and requested > deductible
                ),
            )
            return cast(dict[str, JsonValue], analysis.model_dump(mode="json")), None
        if name == "add_analyst_note":
            self._require_scope(call, "claim:analyst-note")
            analyst_note_request = cast(AddAnalystNoteInput, data)
            claim = self._entity(state, "claims", analyst_note_request.claim_id)
            self._authorize_mutation(call, str(claim["customer_id"]))
            entity_id = self._entity_id(
                "anote", len(cast(list[JsonValue], state["analyst_notes"])) + 1
            )
            analyst_note_after: dict[str, JsonValue] = {
                "id": entity_id,
                "claim_id": analyst_note_request.claim_id,
                "note": analyst_note_request.note,
            }
            cast(list[JsonValue], state["analyst_notes"]).append(analyst_note_after)
            return analyst_note_after, (
                "claim.analyst_note_added",
                "claim",
                analyst_note_request.claim_id,
                None,
                analyst_note_after,
                {"rule": "analyst_note_requires_scope"},
            )
        if name == "record_claim_recommendation":
            self._require_scope(call, "claim:recommend")
            recommendation_request = cast(RecordClaimRecommendationInput, data)
            claim = self._entity(state, "claims", recommendation_request.claim_id)
            self._authorize_mutation(call, str(claim["customer_id"]))
            entity_id = self._entity_id(
                "rec", len(cast(list[JsonValue], state["recommendations"])) + 1
            )
            recommendation_after = cast(
                dict[str, JsonValue],
                AnalystRecommendation(
                    id=entity_id,
                    claim_id=recommendation_request.claim_id,
                    recommendation=RecommendationKind(recommendation_request.recommendation),
                    reason_code=recommendation_request.reason_code,
                    recommended_minor=recommendation_request.recommended_minor,
                    binding=False,
                ).model_dump(mode="json"),
            )
            cast(list[JsonValue], state["recommendations"]).append(recommendation_after)
            return recommendation_after, (
                "claim.recommendation_recorded",
                "claim",
                recommendation_request.claim_id,
                None,
                recommendation_after,
                {"rule": "recommendation_is_non_binding"},
            )
        if name == "flag_claim_for_review":
            self._require_scope(call, "claim:flag")
            flag_request = cast(FlagClaimForReviewInput, data)
            claim = self._entity(state, "claims", flag_request.claim_id)
            self._authorize_mutation(call, str(claim["customer_id"]))
            entity_id = self._entity_id("flag", len(cast(list[JsonValue], state["risk_flags"])) + 1)
            flag_after = cast(
                dict[str, JsonValue],
                RiskFlag(
                    id=entity_id,
                    claim_id=flag_request.claim_id,
                    reason_code=flag_request.reason_code,
                    severity=RiskSeverity(flag_request.severity),
                ).model_dump(mode="json"),
            )
            cast(list[JsonValue], state["risk_flags"]).append(flag_after)
            return flag_after, (
                "claim.risk_flagged",
                "claim",
                flag_request.claim_id,
                None,
                flag_after,
                {"rule": "risk_flag_requires_scope"},
            )
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
            evidence_after = cast(
                dict[str, JsonValue],
                EvidenceItem(
                    id=entity_id,
                    claim_id=evidence_request.claim_id,
                    document_type=evidence_request.document_type,
                    status=EvidenceStatus.REQUESTED,
                    source="customer",
                    verification_state=EvidenceVerificationState.PENDING,
                    trust_classification="untrusted",
                ).model_dump(mode="json"),
            )
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
