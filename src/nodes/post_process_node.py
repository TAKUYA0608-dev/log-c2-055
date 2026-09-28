"""LOG-C2-055 — post_process node: ExceptionPacketCompose (Step 6 — S-3 output gate + S-4 audit).

Composes the final **Exception Evidence Reconciliation Packet** envelope. S-3: neutralise prompt-injection
markers that may have propagated from free-text custody notes, redact any residual carrier credential /
shipper PII (defense-in-depth), verify citation completeness (a grounded reconciliation must cite its
evidence), and append the mandatory DRAFT advisory disclaimer. S-4: emit an audit event (leg / exception /
gap counts only — no PII). Runs on both the full reconciliation and the out-of-scope safe branch.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import PLATFORM_MASK_TOKEN
from src.utils.audit import emit_trace_event

_DISCLAIMER = (
    "【DRAFT】本パケットは受け渡し後（post-handover）の cold-chain 逸脱 evidence を突合した参考ドキュメントです。"
    "候補 owner は placeholder であり、責任配分の確定・製品可否（release）/ 廃棄（disposition）判断・最終的な"
    "品質判断は認可済みの人手レビュー（HumanGate）で行ってください。本エージェントは突合・候補提示のみを行い、"
    "live 温度監視・HACCP 対応・ルート最適化・本番システムの変更は行いません。"
)

# S-3 output-side prompt-injection neutralisation (input is authorised evidence; injection risk is on the
# composed deliverable, e.g. a free-text custody note carrying an instruction).
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
    "###system",
    "<|im_start|>",
    "以上の指示を無視",
)
_INJECTION_NEUTRALISED = "[neutralized]"

# Defense-in-depth credential / PII redaction (patterns assembled from fragments — S-5 safe).
_CRED_PATTERNS = (
    re.compile("sk-" + r"[A-Za-z0-9]{16,}"),
    re.compile("AKIA" + r"[0-9A-Z]{12,}"),
    re.compile("eyJ" + r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(?:api[_-]?key|token|secret|bearer)\s*[:=]\s*[A-Za-z0-9._\-]{12,}"),
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,3}\.[A-Za-z]{2,24}")
_MASK = "[REDACTED]"


def _sanitize_output(text: str) -> str:
    """S-3: neutralise injection markers + redact residual credentials / PII in the composed packet."""
    if not text:
        return text
    out = text
    low = out.lower()
    for marker in _INJECTION_MARKERS:
        if marker in low:
            out = re.sub(re.escape(marker), _INJECTION_NEUTRALISED, out, flags=re.IGNORECASE)
            low = out.lower()
    for pat in _CRED_PATTERNS:
        out = pat.sub(_MASK, out)
    return _EMAIL.sub(_MASK, out)


class PostProcessNode(FunctionNode):
    """Compose the exception packet, sanitise the output, append the DRAFT disclaimer, emit audit."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 preservation check: the mandatory DRAFT advisory disclaimer must be present.

        SDK 1.0.0 contract: receives the **result dict from `execute()`**; returns the (possibly
        filtered) result. MAY raise to block an output missing the mandatory disclaimer.
        """
        out = result.get("formatted_output", "")
        if out and ("DRAFT" not in out or "HumanGate" not in out):
            raise ValueError("S-3: mandatory DRAFT advisory disclaimer missing from output")
        return dict(result)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report: dict[str, Any] = json.loads(state.get("result", "{}") or "{}")

        exceptions = report.get("exceptions", [])
        evaluations = report.get("evaluations", [])
        citations = report.get("citations", [])
        # Citation completeness is judged against the reconciliation itself (its evaluations + exceptions),
        # not merely whether exceptions exist. A grounded reconciliation must cite the evidence it
        # reconciled: every evaluated reading AND every exception must be backed by a citation. An
        # all-in-range shipment (no exceptions) is therefore NOT vacuously "complete" — it must still cite
        # the readings it verified (completeness was vacuously true when exceptions were absent). The
        # out-of-scope safe answer has no grounded evidence to cite (citations=[]) and is trivially complete.
        grounded = report.get("status_kind") == "reconciliation"
        if grounded:
            cited = {(c.get("leg_id"), c.get("sensor_id"), c.get("ts")) for c in citations}
            evaluations_cited = all(
                (ev.get("leg_id"), ev.get("sensor_id"), ev.get("ts")) in cited for ev in evaluations
            )
            exceptions_cited = all(bool(e.get("citations")) for e in exceptions)
            has_evidence = bool(evaluations) or bool(exceptions) or bool(report.get("evidence_gaps"))
            # Two readings that share a masked citation key (same leg, same "[MASKED]" sensor id, same
            # time) are cited by one entry: neither is individually traceable, so that is not complete.
            masked_keys = [
                (ev.get("leg_id"), ev.get("sensor_id"), ev.get("ts"))
                for ev in evaluations
                if PLATFORM_MASK_TOKEN in str(ev.get("sensor_id", ""))
            ]
            masked_citations_merged = len(masked_keys) != len(set(masked_keys))
            citation_complete = (
                (bool(citations) if has_evidence else True)
                and evaluations_cited
                and exceptions_cited
                and not masked_citations_merged
            )
        else:
            citation_complete = True

        envelope = {
            "status_kind": report.get("status_kind"),
            "shipment_id": report.get("shipment_id"),
            "threshold": report.get("threshold"),
            "custody_timeline": report.get("custody_timeline", []),
            "evaluations": report.get("evaluations", []),
            "exceptions": exceptions,
            "evidence_gaps": report.get("evidence_gaps", []),
            "candidate_owners": report.get("candidate_owners", []),
            "human_review_status": report.get("human_review_status", state.get("human_review_status")),
            "review_items": report.get("review_items", []),
            "citations": citations,
            "citation_complete": citation_complete,
            "message": report.get("message"),
            "limitations": report.get("limitations", []),
            "disclaimer": _DISCLAIMER,
        }
        formatted = _sanitize_output(json.dumps(envelope, ensure_ascii=False))

        emit_trace_event(
            "exception_packet_compose.complete",
            {
                "status_kind": report.get("status_kind"),
                "exception_count": len(exceptions),
                "gap_count": len(report.get("evidence_gaps", [])),
                "citation_complete": citation_complete,
                "human_review_status": envelope["human_review_status"],
                "error_code": state.get("error_code"),
            },
            state,
        )
        return {
            "formatted_output": formatted,
            "disclaimer": _DISCLAIMER,
            "human_review_status": envelope["human_review_status"],
            "audit_logged": True,
            "status": AgentStatus.SUCCESS.value,
        }
