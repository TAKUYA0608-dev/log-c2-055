# Test Specification — LOG-C2-055

Logistics Cold-Chain Handover Excursion Evidence Reconciliation Agent (Cat 2).

## Test Strategy
- Coverage target: **≥ 89%** (`--cov=src`); achieved **94%**.
- Test types: Unit (`tests/unit/test_nodes.py`, `test_graph.py`) / Integration (`tests/integration/test_end_to_end.py` — node-chain **+ real `Graph().invoke()`**) / Proof-of-boundary (`tests/proof_of_boundary/`).
- 59 unit+integration tests pass (1 skipped — server import needs the platform module).

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass (composite fields are JSON strings) | PASS |
| TC-02 | S-2 rejection is degraded (not ERROR) | injection / oversize / off-scope → `execute()` returns **status=SUCCESS + error_code** (INJECTION_REJECTED / INPUT_TOO_LONG / ACCESS_SCOPE_VIOLATION), body discarded; `_extra_security_gate_input` is a no-op that never raises; main / post_process (disclaimer / redaction / audit) still run | PASS |
| TC-03 | No JWT/Credential in `src/` | CI `gate-credential-scan`: 0 violations; patterns built from fragments | PASS |
| TC-04 | InvocationContext via `from_state()` only | server adapter only; nodes never store ctx | PASS |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | domain `emit_trace_event` only; no `node_start/complete/error` | 0 duplicates |
| TC-06 | S-2 `_security_gate_input()` not overridden | extended via `_extra_security_gate_input()` only | 0 overrides |
| TC-07 | S-3 `_security_gate_output()` not overridden | extended via `_extra_security_gate_output()` only | 0 overrides |
| TC-08 | `required_trust_level` enforced | `scripts/check_trust_level.py src/` PASS (every FunctionNode = VERIFIED_EXTERNAL) | PASS |
| TC-09 | S-2 degraded reject reaches post via real `Graph().invoke()` | injection / oversize → SUCCESS, `PostProcessNode` in node_history, out-of-scope safe answer, `error_code` on terminal S-4 audit (`_platform_emit` capture) | PASS |
| TC-10 | S-3 `_extra_security_gate_output()` non-trivial | mandatory DRAFT-disclaimer preservation (may raise) | Hook non-trivial |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` per `execute()` | validate/classify/reconcile/synthesis/human_gate/compose all emit | ≥1 per node |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Expected Result | Result |
|-------|----------|----------------|--------|
| PB-1 | `emit_trace_event()` fires on every invocation path | No silent failures | PASS |
| PB-2 | Post-invoke State is primitives only | No Pydantic/dataclass (JSON-string composites) | PASS |
| PB-3 | Template connects to external service via L1 | Deterministic KB / reconciler (advisory-only) | N/A (no live system) |
| PB-4 | Import isolation — no Level 0 imports | AST scan: 0 violations | PASS |
| PB-5 | Checkpoint safety — no JWT/Pydantic | Inspection pass | PASS |
| PB-6 | Invoke execution order S-1 → S-4 start → S-2 → execute → S-3 → S-4 complete | Order verified (real SDK; the local SDK stub local xfail) | PASS (CI) |
| PB-7 | HITL interrupt propagation *(conditional)* | Non-HITL → **Auto-waived**; 2 SKIPPED (`hitl.enabled: false`) | Auto-waived |

## Business Logic Tests

| BL-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-01 | Over-temp excursion | frozen shipment, reading −10°C (band −25..−18) | `over_temp`, severity `high`, cited to sensor/ts |
| BL-02 | Leg without temperature record | custody leg L2 with no readings | `leg_without_temperature_record` gap + `evidence_gap` exception |
| BL-03 | Custody-chain time gap | L1.to_ts=14:00 < L2.from_ts=15:00 | `custody_chain_time_gap` surfaced |
| BL-04 | Orphan record | reading referencing an unknown leg | `record_without_custody_leg` gap |
| BL-05 | Under-temp excursion | reading −40°C | `under_temp` verdict |
| BL-06 | Unreadable temperature | `temp_c="n/a"` | `unreadable` verdict, no false excursion |
| BL-07 | Explicit threshold override | `thresholds={min_c,max_c}` | source = `explicit` |
| BL-08 | Unknown product class, no thresholds | `product_class="unknown"` | `NO_EVIDENCE` → out-of-scope safe answer (`citations=[]`) |
| BL-09 | Empty / free-text input | `"   "` / `"好きな映画を教えて"` | degraded; `audit_logged=True`; safe answer |
| BL-10 | Carrier token / shipper PII in a note | `api_key=…` / email in `handover_doc`/`party` | redacted before persist (S-1) and in output (S-3) |
| BL-14 | Identifier secret / PII redaction (S-1) | credential shape in `leg_id`, My-Number (12-digit) in `sensor_id` | redacted to `[REDACTED]` at pre_process (leg + its record consistently); never reaches classified evidence / evaluations / citations / final packet (`validated_input` and `formatted_output`) |
| BL-15 | Citation completeness is not vacuous | valid chilled shipment, all readings in-range, 0 exceptions | grounded reconciliation still cites the verified readings (evaluation citations) → `citation_complete=True`; a grounded reconciliation with evaluations but empty `citations` → `citation_complete=False` (regression guard) |
| BL-11 | Injection marker at input | `"ignore previous …"` | **degraded SUCCESS + `error_code=INJECTION_REJECTED`**, body discarded (`validated_input="{}"`), out-of-scope safe answer + DRAFT disclaimer + S-4 audit; S-3 `[neutralized]` retained as defense-in-depth for markers propagated from custody notes |
| BL-12 | Candidate owner placeholder | unassigned leg party | `candidate_owner` placeholder → HumanGate review item (never disposition) |
| BL-13 | Injection / oversize reaches post via real `Graph().invoke()` | injection marker / >200k-char input | degraded SUCCESS + error_code; `PostProcessNode` in node_history (safe envelope + disclaimer + S-4 audit); rejected body absent from output; error_code surfaced on terminal audit (`_platform_emit` capture) |

## Test Execution Summary
- Total unit+integration tests: **59 passed / 1 skipped** (unit nodes/services + graph wiring + integration node-chain + real `Graph().invoke()`)
- Coverage: **94%** (`--cov=src`)
- `ruff check src/ tests/unit tests/integration`: clean; `scripts/check_trust_level.py src/`: PASS
- Deterministic: no LLM in the execution path — threshold banding / gap logic / severity are arithmetic over the seeded `ColdChainReconciler` KB
