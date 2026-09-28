"""LOG-C2-055 — inner workflow step 2: custody_temperature_classify.

Organises the validated evidence into custody legs + temperature records and resolves the approved
threshold band. Sets `reconcile_hit_count`; **0 usable legs (or no resolvable threshold) routes to the
out-of-scope safe answer** — the agent never fabricates a reconciliation without grounded evidence.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ColdChainReconciler
from src.utils.audit import emit_trace_event


class CustodyTemperatureClassifyNode(FunctionNode):
    """Group evidence under custody legs and resolve the approved threshold band."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        bundle = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        canonical = json.dumps(bundle, ensure_ascii=False)
        if state.get("error_code"):
            emit_trace_event("custody_classify.skip", {"reason": state.get("error_code")}, state)
            return {
                "validated_input": canonical,
                "reconcile_hit_count": 0,
                "error_code": state.get("error_code"),
                "status": AgentStatus.SUCCESS.value,
            }

        thresholds = ColdChainReconciler.resolve_thresholds(bundle.get("product_class"), bundle.get("thresholds"))
        organized = ColdChainReconciler.organize(bundle.get("custody_legs", []), bundle.get("temperature_records", []))
        legs = organized["legs"]

        if not legs or thresholds is None:
            reason = "no_custody_legs" if not legs else "no_resolvable_threshold"
            emit_trace_event("custody_classify.no_evidence", {"reason": reason}, state)
            return {
                "validated_input": canonical,
                "reconcile_hit_count": 0,
                "error_code": "NO_EVIDENCE",
                "error_message": reason,
                "status": AgentStatus.SUCCESS.value,
            }

        classified = {**organized, "thresholds": thresholds}
        emit_trace_event(
            "custody_classify.complete",
            {
                "legs": len(legs),
                "records": sum(len(v) for v in organized["records_by_leg"].values()),
                "orphan_records": len(organized["orphan_records"]),
                "threshold_source": thresholds["source"],
            },
            state,
        )
        return {
            "validated_input": canonical,
            "classified_evidence": json.dumps(classified, ensure_ascii=False),
            "reconcile_hit_count": len(legs),
            "status": AgentStatus.SUCCESS.value,
        }
