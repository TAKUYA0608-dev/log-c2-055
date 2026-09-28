"""LOG-C2-055 — inner workflow step 4: exception_synthesis.

Attributes each excursion / evidence gap to a custody leg and synthesises exception items with a
**candidate/placeholder** owner, a deterministic severity, and evidence citations. Owner confirmation
and any product-release / disposition decision are deferred to an authorised human (HumanGate) — this
node never decides disposition.

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


class ExceptionSynthesisNode(FunctionNode):
    """Synthesise custody-leg-attributed exception items (candidate owner, never disposition)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("reconcile_hit_count", 0) == 0:
            # skip guard — still emit a count-only S-4 event on the degraded / 0-evidence path
            emit_trace_event("exception_synthesis.skipped", {"reason": state.get("error_code") or "no_evidence"}, state)
            return {}
        classified = json.loads(state.get("classified_evidence") or "{}")
        reconciliation = json.loads(state.get("reconciliation") or "{}")
        exceptions = ColdChainReconciler.synthesize(reconciliation, classified)
        emit_trace_event(
            "exception_synthesis.complete",
            {
                "exceptions": len(exceptions),
                "high": sum(1 for e in exceptions if e.get("severity") == "high"),
                "gap_exceptions": sum(1 for e in exceptions if e.get("excursion_type") == "evidence_gap"),
            },
            state,
        )
        return {"exceptions": json.dumps(exceptions, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
