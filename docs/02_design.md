# Template Design Specification — LOG-C2-055

Logistics Cold-Chain Handover Excursion Evidence Reconciliation Agent (Cat 2).

## Position in AgentCore Architecture

- **Agent Class**: `LogisticsColdChainHandoverExcursionEvidenceAgent` (module-level alias of `Graph`)
- **L1 Base**: **AgentBaseGraph** (Cat 2 — outer 5-node backbone; direct L1 inheritance, no L2)
- **Category**: Cat 2 — a multi-step domain workflow (validate → classify → reconcile → synthesise →
  human-gate → compose) producing a single traceable deliverable (Exception Evidence Reconciliation
  Packet); LOG industry (cold-chain / 3PL)
- **Three-Layer Separation**: State = flat TypedDict; Node = L1 inheritance (`execute` override only);
  Graph = outer `AgentBaseGraph` + **`GraphNode` in the `main` slot** wrapping an inner `BaseGraph`

## Architecture Overview

Cat 2 pattern — the `main` slot is a **`GraphNode`** (`EvidenceReconciliationWorkflowGraphNode`,
**subgraph cached**) that wraps the inner `ColdChainReconciliationWorkflow` (`BaseGraph`). The inner
graph is a **static linear backbone with per-node skip guards** (conditional edges don't propagate
across the subgraph boundary). **Advisory-only (read-only): post-handover reconciliation only — no live
monitoring, HACCP response, route optimisation, or product release / disposition (owner confirmation
and product disposition stay with authorised humans via a mandatory HumanGate).**

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version, session_id, trust_level | user_input | (framework) | InitializeNode (default) |
| pre_process | `PreProcessNode` (EvidenceValidate) — S-1 NFKC normalise + provenance/format validation + credential/PII redaction; S-2 (in `execute()`) prompt-injection markers + size cap + access-scope gate → **degraded SUCCESS + error_code** (untrusted body discarded) | user_input | validated_input, input_format, enriched_context | FunctionNode.execute |
| main | `EvidenceReconciliationWorkflowGraphNode` (GraphNode) → inner workflow | validated_input | result, reconcile_hit_count, human_review_status | GraphNode |
| post_process | `PostProcessNode` (ExceptionPacketCompose) — S-3 output sanitisation (injection neutralise + credential/PII redaction) + citation completeness + mandatory DRAFT disclaimer + S-4 audit | result | formatted_output, disclaimer, audit_logged | FunctionNode.execute |
| finalize | response_metadata, total_time_ms | | (framework) | FinalizeNode (default) |

**Inner workflow (`ColdChainReconciliationWorkflow` : BaseGraph):**

```
START → custody_temperature_classify → threshold_reconcile → exception_synthesis → human_gate → END
```

| Inner node | Responsibility |
|---|---|
| CustodyTemperatureClassify | organise custody legs + temperature records; classify excursion candidates (sensor / leg / type: over-temp / under-temp / gap). Sets `reconcile_hit_count`; **0 usable legs → out-of-scope safe answer** |
| ThresholdReconcile | **reconcile** temperature records against approved thresholds (product-class KB or explicit) + cross-check the custody chain to **identify evidence gaps** (legs w/o records, records w/o leg, chain time gaps). Numeric comparison is deterministic |
| ExceptionSynthesis | **attribute** excursions to custody legs and synthesise exception items with a **candidate/placeholder** owner + severity + evidence citations. Never decides product disposition |
| HumanGate | **deterministic gate** — flag material / low-confidence / unassigned-owner exceptions for authorised human review; set `human_review_status`; assemble the reconciliation `result` (or the out-of-scope safe answer) |

### Data Flow

```
START → initialize → pre_process → main(GraphNode → inner linear workflow) → post_process → finalize → END
                                     ↓ (retry, max 3)
                                   pre_process
```

Rejected / 0-evidence input sets `error_code` + `reconcile_hit_count=0`; reconcile / synthesis no-op and
human_gate emits the out-of-scope safe answer — **no fabricated reconciliation** (`citations=[]`).

### State Definition

| Field | Type | Purpose |
|-------|------|---------|
| validated_input | str (JSON) | `{shipment_id, product_class, custody_legs[], temperature_records[], thresholds{}, access_scope}` (credentials/PII redacted) |
| classified_evidence | str (JSON) | `{legs[], records_by_leg{}, orphan_records[], thresholds{}}` |
| reconcile_hit_count | int | usable custody legs; 0 → out-of-scope safe answer |
| reconciliation | str (JSON) | `{evaluations[], evidence_gaps[]}` |
| exceptions | str (JSON) | `[{leg_id, candidate_owner, excursion_type, severity, citations[]}]` |
| result / formatted_output | str (JSON) | inner packet content / final envelope |
| human_review_status | str | `pending_review` / `no_exceptions` / `not_applicable` |
| disclaimer / audit_logged | str/bool | mandatory DRAFT disclaimer + terminal audit |
| error_code / error_message | str | degraded path (SUCCESS + error_code, never status=ERROR) |

**State Constraints:** flat TypedDict; JSON strings for complex fields; **no credentials / PII persisted**
(carrier tokens + shipper PII redacted at pre_process; audit carries aggregate counts only); `enriched_context`
is a JSON string (ADR-005).

## Framework Utilization

- [x] **GraphNode-in-main** (Cat 2 composition, criterion #9) — `error_strategy="propagate"`, `propagate_hitl=False`, **subgraph cached** (`self._subgraph`)
- [x] S-1 `required_trust_level=VERIFIED_EXTERNAL` on all `FunctionNode` subclasses; input NFKC normalisation
- [x] S-2 (deterministic, IN `execute()`) — prompt-injection markers + size cap + access-scope gate → **degraded `status=SUCCESS + error_code`** (INJECTION_REJECTED / INPUT_TOO_LONG / ACCESS_SCOPE_VIOLATION) with the untrusted body discarded (`validated_input="{}"`). The `_extra_security_gate_input()` hook is a documented **no-op** (SDK 1.0.0: MUST NOT raise, must return the state) — a `status=ERROR` there would short-circuit `__call__` so `route()` jumps to `finalize`, skipping main / post_process (disclaimer / redaction / S-4 audit). **S-2 rejects keep `status=SUCCESS` so post_process always runs.**
- [x] S-3 `_extra_security_gate_output()` (post) — mandatory-DRAFT-disclaimer preservation; **may raise** (SDK 1.0.0). `execute()` redacts residual carrier credential patterns + shipper PII and neutralises injection markers that propagated from free-text custody notes
- [x] S-4 `emit_trace_event()` in every `execute()` (leg / excursion / gap counts only — no PII); terminal audit always fires
- [x] Platform masking of party names (S-2) — the framework's `_security_gate_input()` replaces any run of two or more Title-Case words with `[MASKED]` before any template node runs, which covers most carrier names (`Nippon Express` → `[MASKED]`, `Yamato Transport Co., Ltd.` → `[MASKED]., Ltd.`); the template cannot turn it off. Measured on AgentCore 1.0.3: two different masked carriers were shown as one candidate owner `[MASKED]`, and because a masked owner looked like a known owner their exceptions were not routed to review. Rule (`HumanGateNode`, helper `service.masked_party_legs`): a leg whose party contains `[MASKED]` is treated like an unassigned owner — its exceptions go to `review_items` with the reason "owner name masked by the platform", its candidate owner is qualified with the leg (`[MASKED] (leg L2)`), the packet's `limitations` is `["PARTY_NAME_MASKED"]` and `message` names the affected legs. Every leg is checked, not only the first or the most severe one. Temperature evaluations, severities and evidence gaps are unchanged. Sensor ids are masked the same way (`Front Sensor` / `Rear Sensor` → `[MASKED]`; measured: two such readings at the same time collapsed into one citation while `citation_complete` stayed `true`): `ColdChainReconciler.organize` qualifies each masked sensor id with its 1-based position in `temperature_records` (`service.sensor_surrogate` → `[MASKED] (record 2)`), so readings, exceptions and citations stay distinct; `limitations` then includes `SENSOR_ID_MASKED` (after `PARTY_NAME_MASKED` when both apply) and `message` explains the surrogate. As a guard, `PostProcessNode` reports `citation_complete: false` if two evaluations ever share one masked citation key. `limitations` holds stable machine-readable codes only; unmasked packets keep their previous output apart from `limitations: []`.
- [x] S-5 credential isolation — no literal credentials in `src/`; carrier-token patterns matched via concatenated fragments

> **S-2/S-3 gate behaviour by node type (ADR-017):** `FunctionNode` subclasses run the framework `@final`
> gate automatically (extend via `_extra_security_gate_input/output()` only); the `GraphNode` `main` slot is a
> deliberate no-op (the inner `FunctionNode`s already applied their gates).

## Import Isolation Confirmation
- [x] No `agenticstar` SDK (Level 0) import — PB-4
- [x] Import targets: `framework/`, `langgraph`, and `src.` only

## Design Decision Record

| Decision | Chosen | Rationale |
|----------|--------|-----------|
| L1 base type | AgentBaseGraph | Fixed pipeline, no autonomous loop |
| Composition | **GraphNode-in-main + inner BaseGraph (cached)** | Cat 2 multi-step domain workflow |
| Inner topology | **Linear + per-node skip guards** | Conditional edges don't propagate across the subgraph boundary |
| Reconciliation | **Deterministic threshold + gap logic** | Auditable numeric comparison; LLM reserved for phrasing in production |
| Injection defence | **execute()-level degraded reject (INJECTION_REJECTED) + S-3 output neutralise** | Injection is rejected in the execution path (degraded SUCCESS, body discarded) so post_process always delivers the safe answer + audit; S-3 marker neutralisation is retained as defense-in-depth for markers propagated from free-text custody notes |
| Secrets / PII | **Redacted at pre + post** | Carrier feeds may carry API tokens / shipper PII (LOG defense-in-depth) |
| Disposition | **HumanGate — never decided by the agent** | Product release / disposition + owner confirmation stay with authorised humans |
| Rejection signalling | SUCCESS + error_code | Guarantees post_process S-3/S-4 always run (SDK 1.0.0) |

## Open Items (Stage ③ implementation MR)
- Node implementations (pre + 4 inner + post) + inner workflow graph (shipped in the implementation MR).
- Seeded `ColdChainReconciler` service (product-class threshold KB + deterministic reconcile / gap / exception logic).
- Unit + integration + PB tests; coverage ≥ 89%.
