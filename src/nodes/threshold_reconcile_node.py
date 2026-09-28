"""LOG-C2-055 — inner workflow step 3: threshold_reconcile.

Reconciles each temperature reading against the approved threshold band (over / under / in-range) and
cross-checks the custody chain to identify evidence gaps (legs without readings, readings without a
leg, custody-chain time gaps). Numeric comparison is deterministic and auditable.

Skips (no-op) on the rejected / 0-evidence branch.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ColdChainReconciler
from src.utils.audit import emit_trace_event


class ThresholdReconcileNode(FunctionNode):
    """Reconcile readings against approved thresholds and surface evidence gaps."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("reconcile_hit_count", 0) == 0:
            # skip guard — still emit a count-only S-4 event on the degraded / 0-evidence path
            emit_trace_event("threshold_reconcile.skipped", {"reason": state.get("error_code") or "no_evidence"}, state)
            return {}
        classified = json.loads(state.get("classified_evidence") or "{}")
        reconciliation = ColdChainReconciler.reconcile(classified, classified["thresholds"])
        excursions = sum(1 for e in reconciliation["evaluations"] if e["verdict"] in ("over_temp", "under_temp"))
        emit_trace_event(
            "threshold_reconcile.complete",
            {
                "evaluations": len(reconciliation["evaluations"]),
                "excursions": excursions,
                "evidence_gaps": len(reconciliation["evidence_gaps"]),
            },
            state,
        )
        return {"reconciliation": json.dumps(reconciliation, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
