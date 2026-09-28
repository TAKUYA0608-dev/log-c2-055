"""LOG-C2-055 — Agent state (Cold-Chain Handover Excursion Evidence Reconciliation, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on read.

Advisory-only (read-only): the agent reconciles *post-handover* cold-chain evidence and composes an
exception packet — it never performs live temperature monitoring, HACCP response, route optimisation,
or product release / disposition decisions (those stay with authorised humans / other systems).

S-2 / S-5 / APPI: carrier API tokens or shipper PII inadvertently mixed into carrier feeds are
redacted before the evidence is persisted to State (``validated_input``); the audit trail carries
aggregate counts only (no PII).

All agent-specific fields are NotRequired (populated progressively; absent at empty-start invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the cold-chain handover evidence-reconciliation workflow."""

    # ── pre_process (EvidenceValidate — S-1 normalise + S-2 provenance/scope gate) ──────────
    validated_input: (
        str  # JSON: {shipment_id, product_class, custody_legs[], temperature_records[], thresholds{}, access_scope}
    )
    input_format: str  # "json" | "text" | "empty"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow (classify → reconcile → synthesis → human_gate) ─────────────────────
    classified_evidence: str  # JSON: {legs[], records_by_leg{}, orphan_records[], thresholds{}}
    reconcile_hit_count: int  # usable custody legs (0 → out-of-scope safe answer)
    reconciliation: str  # JSON: {evaluations[], evidence_gaps[]}
    exceptions: str  # JSON: [{leg_id, candidate_owner, excursion_type, severity, citations[]}]
    result: str  # JSON: assembled Exception Evidence Reconciliation Packet content

    # ── post_process (ExceptionPacketCompose — S-3 gate + S-4 audit) ───────────────────────
    formatted_output: str  # JSON: final packet envelope (+ DRAFT disclaimer)
    disclaimer: str  # mandatory DRAFT / advisory disclaimer
    human_review_status: str  # "pending_review" | "no_exceptions" | "not_applicable"
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ────────────────
    error_code: str  # INPUT_REJECTED | INJECTION_REJECTED | INPUT_TOO_LONG | ACCESS_SCOPE_VIOLATION | NO_EVIDENCE
    error_message: str  # operator-facing detail
