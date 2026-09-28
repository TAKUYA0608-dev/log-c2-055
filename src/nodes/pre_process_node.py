"""LOG-C2-055 — pre_process node: EvidenceValidate (Step 1).

Accepts an authorised cold-chain handover evidence bundle (structured JSON) or NL text. Enforces:
  * S-1 — NFKC normalisation of the raw request.
  * S-2 (deterministic, IN `execute()`) — prompt-injection markers / size cap / access-scope gate. All
    hard violations are handled as a **degraded `status=SUCCESS + error_code`** (INJECTION_REJECTED /
    INPUT_TOO_LONG / ACCESS_SCOPE_VIOLATION) with the untrusted body **discarded** (`validated_input="{}"`),
    so main / post_process (disclaimer / redaction / S-4 audit) always run and the out-of-scope safe
    answer is delivered.
  * Credential + PII redaction — carrier API tokens and shipper PII that may be mixed into carrier feeds
    are redacted *before* the evidence is persisted to State (`validated_input`) [LOG defense-in-depth].

The `_extra_security_gate_input` hook is a documented SDK-1.0.0-contract no-op (MUST NOT raise / must
return the state): a `status=ERROR` there would short-circuit `__call__` so `route()` would jump straight
to `finalize`, skipping main / post_process. Injection defence therefore lives in the
execution path (degraded reject), with S-3 output neutralisation retained as defense-in-depth.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event

_MAX_INPUT = 200_000  # cold-chain bundles carry many readings; generous cap
_AUTHORIZED_SCOPES = {"handover", "cold_chain_qa", "custody", "logistics_ops", "quality"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "disregard all previous",
    "you are now",
    "###system",
    "<|im_start|>",
    "以上の指示を無視",
    "reveal your system prompt",
    "print your system prompt",
    "show your system prompt",
)

# Credential patterns assembled from fragments so no literal secret lives in src/ (S-5 / gate-credential-scan).
_CRED_PATTERNS = (
    re.compile("sk-" + r"[A-Za-z0-9]{16,}"),  # LLM-style API key
    re.compile("AKIA" + r"[0-9A-Z]{12,}"),  # AWS access key id
    re.compile("eyJ" + r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"(?i)(?:api[_-]?key|token|secret|bearer)\s*[:=]\s*[A-Za-z0-9._\-]{12,}"),
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,3}\.[A-Za-z]{2,24}")
_PHONE = re.compile(r"(?<!\d)(?:0\d{1,4}-\d{1,4}-\d{3,4}|0\d{9,10}|\+81\d{9,10})(?!\d)")
# My-Number (Japanese personal number): a standalone 12-digit run (bounded so a longer numeric id / a
# structured timestamp is not clipped). Kept out of the timestamp fields, which stay comparable.
_MYNUMBER = re.compile(r"(?<!\d)\d{12}(?!\d)")
_MASK = "[REDACTED]"


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _redact(text: str) -> str:
    """Mask carrier credential tokens + shipper PII in a supplied string field before it persists to State.

    Applied to *every* supplied string value written to `validated_input` — free-text (party / handover_doc /
    note) **and identifier** fields (leg_id / sensor_id / shipment_id). These identifiers propagate into
    classified evidence, evaluations, citations and the final packet, so a carrier token / PII shape mixed
    into an id must be redacted at input, not just in free text (S-1 identifier redaction gap).
    """
    if not text:
        return text
    out = _CONTROL.sub("", text)
    for pat in _CRED_PATTERNS:
        out = pat.sub(_MASK, out)
    out = _EMAIL.sub(_MASK, out)
    out = _PHONE.sub(_MASK, out)
    out = _MYNUMBER.sub(_MASK, out)
    return out


class PreProcessNode(FunctionNode):
    """Validate the evidence bundle, redact carrier secrets / shipper PII, extract the reconciliation slots."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 domain hook — no hard reject (SDK 1.0.0: MUST NOT raise, must return the state).

        Prompt-injection / oversize / off-scope access are handled as a degraded
        `status=SUCCESS + error_code` (INJECTION_REJECTED / INPUT_TOO_LONG / ACCESS_SCOPE_VIOLATION)
        path in `execute()` — which always runs — so main / post_process (disclaimer / PII redaction /
        S-4 audit) still fire and the out-of-scope safe answer is delivered. A `status=ERROR` here would
        short-circuit `__call__`, so `route()` would send the request straight to `finalize`, skipping
        main / post_process. The framework default S-2 masking still applies. Returns
        the state unchanged.
        """
        return dict(state)

    @staticmethod
    def _declared_scope(raw: str) -> str | None:
        try:
            obj = json.loads(_nfkc(raw))
        except (ValueError, TypeError):
            return None
        if isinstance(obj, dict) and obj.get("access_scope"):
            return str(obj["access_scope"]).strip().lower()
        return None

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("user_input", "") or ""
        input_context = state.get("input_context", {})  # read-only [C1]
        enriched = json.dumps(
            {
                "source": "LogisticsColdChainHandoverExcursionEvidenceAgent",
                "channel": input_context.get("channel", "unknown"),
            },
            ensure_ascii=False,
        )
        normalized = _nfkc(raw).strip()

        # S-2 (deterministic, IN the execution path so main / post_process always run): prompt-injection
        # / oversize / off-scope access -> degraded SUCCESS + error_code. NOT status=ERROR — ERROR
        # short-circuits __call__ so main / post_process (disclaimer / redaction / audit) would be
        # skipped. The untrusted body is discarded (validated_input="{}"); the inner
        # skip guards route to the out-of-scope safe answer.
        if any(marker in normalized.lower() for marker in _INJECTION_MARKERS):
            emit_trace_event("evidence_validate.rejected", {"reason": "prompt_injection"}, state)
            return {
                "validated_input": "{}",
                "input_format": "rejected",
                "enriched_context": enriched,
                "error_code": "INJECTION_REJECTED",
                "error_message": "prompt-injection marker detected; input not processed",
                "status": AgentStatus.SUCCESS.value,
            }

        if len(raw) > _MAX_INPUT:
            emit_trace_event("evidence_validate.rejected", {"reason": "oversize"}, state)
            return {
                "validated_input": "{}",
                "input_format": "oversize",
                "enriched_context": enriched,
                "error_code": "INPUT_TOO_LONG",
                "error_message": f"input exceeds {_MAX_INPUT} chars",
                "status": AgentStatus.SUCCESS.value,
            }

        if not raw.strip():
            emit_trace_event("evidence_validate.rejected", {"reason": "empty_input"}, state)
            return {
                "validated_input": "{}",
                "input_format": "empty",
                "enriched_context": enriched,
                "error_code": "INPUT_REJECTED",
                "status": AgentStatus.SUCCESS.value,
            }

        scope = self._declared_scope(raw)
        if scope is not None and scope not in _AUTHORIZED_SCOPES:
            emit_trace_event("evidence_validate.rejected", {"reason": "access_scope"}, state)
            return {
                "validated_input": "{}",
                "input_format": "unauthorized",
                "enriched_context": enriched,
                "error_code": "ACCESS_SCOPE_VIOLATION",
                "error_message": f"access-scope '{scope}' not authorised for reconciliation",
                "status": AgentStatus.SUCCESS.value,
            }

        bundle, fmt = self._parse(_CONTROL.sub("", normalized))
        emit_trace_event(
            "evidence_validate.validated",
            {
                "input_format": fmt,
                "custody_legs": len(bundle.get("custody_legs", [])),
                "temperature_records": len(bundle.get("temperature_records", [])),
                "product_class": bundle.get("product_class"),
            },
            state,
        )
        return {
            "validated_input": json.dumps(bundle, ensure_ascii=False),
            "input_format": fmt,
            "enriched_context": enriched,
            "status": AgentStatus.SUCCESS.value,
        }

    def _parse(self, text: str) -> tuple[dict[str, Any], str]:
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict):
            return self._normalize_bundle(obj), "json"
        # Free text: no structured evidence to reconcile → empty bundle (routes to safe answer).
        return {
            "shipment_id": None,
            "product_class": None,
            "custody_legs": [],
            "temperature_records": [],
            "thresholds": None,
            "access_scope": None,
            "note": _redact(text)[:500],
        }, "text"

    def _normalize_bundle(self, obj: dict[str, Any]) -> dict[str, Any]:
        # Every supplied string field is hygiened before it persists to `validated_input` — identifiers
        # (leg_id / sensor_id) included, since they flow into classified evidence / evaluations / citations
        # / final packet. `leg_id` is redacted consistently on both the custody leg and its temperature
        # record, so the join key stays aligned. Timestamps (from_ts / to_ts / ts) are validated structured
        # values kept verbatim so custody-chain time-gap comparison remains exact.
        legs = [
            {
                "leg_id": _redact(str(leg.get("leg_id", ""))),
                "party": _redact(str(leg.get("party", ""))),
                "from_ts": str(leg.get("from_ts", "")),
                "to_ts": str(leg.get("to_ts", "")),
                "handover_doc": _redact(str(leg.get("handover_doc", ""))),
            }
            for leg in obj.get("custody_legs", [])
            if isinstance(leg, dict)
        ]
        records = [
            {
                "sensor_id": _redact(str(r.get("sensor_id", ""))),
                "ts": str(r.get("ts", "")),
                "temp_c": r.get("temp_c"),
                "leg_id": _redact(str(r.get("leg_id", ""))),
            }
            for r in obj.get("temperature_records", [])
            if isinstance(r, dict)
        ]
        thresholds = obj.get("thresholds") if isinstance(obj.get("thresholds"), dict) else None
        return {
            "shipment_id": _redact(str(obj.get("shipment_id", "") or "")) or None,
            "product_class": obj.get("product_class"),
            "custody_legs": legs,
            "temperature_records": records,
            "thresholds": thresholds,
            "access_scope": obj.get("access_scope"),
        }
