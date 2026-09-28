# LOG-C2-055 — Logistics Cold-Chain Handover Excursion Evidence Reconciliation Agent

> **Category**: Cat 2 (domain workflow (a job to be done))
> **Industry**: Logistics

## Overview

Reconciles cold-chain custody and temperature evidence after a handover. Given a JSON payload with the shipment id, product class, access scope, custody legs (party, from and to timestamps, handover document) and temperature records (sensor, timestamp, temperature, leg), the agent organises the legs and records, classifies excursion candidates (over-temperature, under-temperature, gap), reconciles the readings against the product-class thresholds from a seeded knowledge base or explicit limits, cross-checks the custody chain for legs without records, records without a leg and time gaps, attributes excursions to legs with a candidate owner placeholder and a severity, and returns a custody_timeline, evaluations, exceptions, evidence_gaps, candidate_owners, citations and a draft disclaimer. Numeric comparison and gap logic are deterministic; no LLM is called. Material and unassigned exceptions are flagged for a person, credentials and shipper personal data in carrier feeds are redacted, the agent never decides product disposition, and a payload with no usable leg yields an out-of-scope answer. The threshold knowledge base shipped here is a small seeded sample.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | 3.11 or later (`requires-python = ">=3.11"`) |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Known limitations

**Carrier and site names can be masked by the platform.** AGENTIC STAR's personal-data protection
runs before this template's code and replaces any run of two or more Title-Case words with
`[MASKED]` — which covers most carrier names. The template cannot switch it off. Temperature
comparison, severities and evidence gaps do not depend on names and are unchanged, but the
accountable party of a masked leg is unknown, so it is treated like an unassigned owner: the
leg's exceptions go to `review_items`, `candidate_owners` lists `[MASKED] (leg L1)` per leg so two
masked carriers are not shown as one owner, `limitations` is `["PARTY_NAME_MASKED"]`, and `message`
names the affected legs. Measured on AgentCore 1.0.3 (frozen shipment, L1 medium and L2 low
over-temperature excursion):

| Parties sent | Reach the agent as | Result |
|---|---|---|
| `Nippon Express` / `Sagawa Express` | `[MASKED]` / `[MASKED]` | L1 and L2 in `review_items` — confirm the owner from the hand-over record; owners `[MASKED] (leg L1)`, `[MASKED] (leg L2)` (without this rule: one owner `[MASKED]`, no review item) |
| `NIPPON EXPRESS` / `Sagawa Express` | `NIPPON EXPRESS` / `[MASKED]` | L2 in `review_items`; L1 stays a known owner |
| `Yamato Transport Co., Ltd.` | `[MASKED]., Ltd.` | review item |
| `Nichirei Logistics Group` / `Yamato Holdings` | unchanged (a recognised designator is inside the run) | normal, `limitations: []` |

Sensor ids written as Title-Case words (`Front Sensor`, `Rear Sensor`) are masked too. Two such
sensors read at the same time would become the same `[MASKED]` and share one citation, so each
masked sensor id is qualified with its position in `temperature_records` (`[MASKED] (record 2)`),
every reading keeps its own citation, and `limitations` includes `SENSOR_ID_MASKED` (with
`PARTY_NAME_MASKED` first when a party was masked as well). `citation_complete` is never reported
as `true` for readings that share one masked citation. Document names (`Handover Receipt`) are
masked the same way. Use codes such as `S1` / `DOC-1` to avoid all of this.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
