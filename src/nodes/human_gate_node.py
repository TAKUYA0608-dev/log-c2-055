"""LOG-C2-055 — inner workflow step 5: human_gate.

Deterministic human-in-the-loop gate: flags exceptions that require an authorised human review
(high severity / unassigned owner / evidence gap) and sets `human_review_status`. Material decisions —
owner confirmation and product release / disposition — are never made by the agent.

This is the final inner node; it assembles the reconciliation `result` (the Exception Evidence
Reconciliation Packet content) or, on the rejected / 0-evidence branch, the out-of-scope safe answer
(`citations=[]` — no fabricated reconciliation).
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import PARTY_NAME_MASKED, SENSOR_ID_MASKED, masked_party_legs, masked_sensor_ids
from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "認可済みの受け渡し証跡（custody legs / temperature records / 承認済み閾値 or product_class）が"
    "見つからないため、cold-chain 逸脱 evidence の突合パケットを生成できませんでした。shipment_id・"
    "custody_legs・temperature_records と product_class（frozen / chilled / pharma_cold 等）または"
    "thresholds を含む認可済み証跡バンドルを投入してください。"
)


class HumanGateNode(FunctionNode):
    """Flag exceptions for authorised human review and assemble the reconciliation result."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("reconcile_hit_count", 0) == 0:
            emit_trace_event("human_gate.safe", {"reason": state.get("error_code") or "no_evidence"}, state)
            report: dict[str, Any] = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "exceptions": [],
                "evidence_gaps": [],
                "citations": [],
            }
            return {
                "result": json.dumps(report, ensure_ascii=False),
                "human_review_status": "not_applicable",
                "status": AgentStatus.SUCCESS.value,
            }

        classified = json.loads(state.get("classified_evidence") or "{}")
        reconciliation = json.loads(state.get("reconciliation") or "{}")
        exceptions = json.loads(state.get("exceptions") or "[]")
        # Legs whose party the platform masked before reconciliation (see service.PLATFORM_MASK_TOKEN):
        # the accountable party is unknown, exactly like an unassigned owner, so their exceptions go to
        # a person. Every leg is checked, not only the first or the most severe one.
        masked_legs = masked_party_legs(classified.get("legs", []))
        # Temperature records whose sensor id the platform masked; organise() already qualified each
        # one with its record position, so readings and citations stay distinct.
        masked_sensors = masked_sensor_ids(classified)

        review_items = [
            {
                "leg_id": e.get("leg_id"),
                "excursion_type": e.get("excursion_type"),
                "severity": e.get("severity"),
                "reason": self._review_reason(e, masked_legs),
            }
            for e in exceptions
            if self._needs_review(e, masked_legs)
        ]
        human_review_status = "pending_review" if exceptions else "no_exceptions"

        # A grounded reconciliation must cite the evidence it reconciled — every evaluated reading, not only
        # the exceptions. Union evaluation citations with exception citations (de-duplicated) so an
        # all-in-range shipment still cites the readings it verified, i.e. citation completeness is not
        # vacuously satisfied by "no exceptions" (citation-completeness gap).
        citations: list[dict[str, Any]] = []
        _seen: set[tuple[Any, Any, Any]] = set()
        raw_cites = [
            {"leg_id": ev.get("leg_id"), "sensor_id": ev.get("sensor_id"), "ts": ev.get("ts")}
            for ev in reconciliation.get("evaluations", [])
        ] + [c for e in exceptions for c in e.get("citations", [])]
        for c in raw_cites:
            key = (c.get("leg_id"), c.get("sensor_id"), c.get("ts"))
            if key not in _seen:
                _seen.add(key)
                citations.append({"leg_id": c.get("leg_id"), "sensor_id": c.get("sensor_id"), "ts": c.get("ts")})

        report = {
            "status_kind": "reconciliation",
            "shipment_id": classified.get("shipment_id")
            or json.loads(state.get("validated_input") or "{}").get("shipment_id"),
            "threshold": classified.get("thresholds"),
            "custody_timeline": classified.get("legs", []),
            "evaluations": reconciliation.get("evaluations", []),
            "evidence_gaps": reconciliation.get("evidence_gaps", []),
            "exceptions": exceptions,
            # A masked owner is qualified with its leg, so two different masked carriers are not shown
            # as one owner.
            "candidate_owners": sorted(
                {
                    f"{e['candidate_owner']} (leg {e.get('leg_id')})"
                    if e.get("leg_id") in masked_legs
                    else e["candidate_owner"]
                    for e in exceptions
                    if e.get("candidate_owner")
                }
            ),
            "human_review_status": human_review_status,
            "review_items": review_items,
            "citations": citations,
            # `limitations` carries stable codes only; the explanation is in `message`.
            "limitations": ([PARTY_NAME_MASKED] if masked_legs else [])
            + ([SENSOR_ID_MASKED] if masked_sensors else []),
        }
        notes: list[str] = []
        if masked_legs:
            notes.append(
                "プラットフォームの個人情報保護機能により、custody leg "
                + ", ".join(masked_legs)
                + " の party 名が突合前に [MASKED] に置き換えられました。温度判定・重大度・証跡欠落は名前に依存しないため"
                "変わりませんが、これらの leg の責任主体は特定できません。元の受け渡し記録（hand-over record）から人手で"
                "確認してください。"
            )
        if masked_sensors:
            notes.append(
                "プラットフォームの個人情報保護機能により、温度記録 "
                + str(len(masked_sensors))
                + " 件の sensor_id が [MASKED] に置き換えられました。読み取り値と引用は temperature_records 内の位置"
                "（例: [MASKED] (record 2)）で区別しています。センサーの識別は元の温度記録で確認してください。"
            )
        if notes:
            report["message"] = "".join(notes)
        emit_trace_event(
            "human_gate.complete",
            {
                "exceptions": len(exceptions),
                "review_items": len(review_items),
                "human_review_status": human_review_status,
                "masked_party_legs": len(masked_legs),
                "masked_sensor_ids": len(masked_sensors),
            },
            state,
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "human_review_status": human_review_status,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _needs_review(exc: dict[str, Any], masked_legs: Collection[str] = ()) -> bool:
        owner = str(exc.get("candidate_owner", ""))
        return (
            exc.get("severity") == "high"
            or exc.get("excursion_type") == "evidence_gap"
            or owner.startswith("UNASSIGNED")
            or exc.get("leg_id") in masked_legs
        )

    @staticmethod
    def _review_reason(exc: dict[str, Any], masked_legs: Collection[str] = ()) -> str:
        if str(exc.get("candidate_owner", "")).startswith("UNASSIGNED"):
            return "owner unassigned — confirm accountable party (human)"
        if exc.get("leg_id") in masked_legs:
            return "owner name masked by the platform — confirm accountable party from the hand-over record (human)"
        if exc.get("excursion_type") == "evidence_gap":
            return "evidence gap — confirm missing custody/temperature evidence (human)"
        return "high-severity excursion — confirm impact / disposition (human)"
