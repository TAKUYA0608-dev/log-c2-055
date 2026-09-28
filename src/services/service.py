"""LOG-C2-055 — deterministic cold-chain reconciliation services (no framework imports).

`ColdChainReconciler`: a seeded knowledge base of approved product-handling temperature thresholds
(frozen / deep-frozen / chilled / pharma-cold / ambient-controlled) plus deterministic, auditable
reconciliation logic:

  * organise      — group temperature records under custody legs; surface orphan records
  * reconcile     — compare each reading against the approved threshold (over / under / in-range) and
                    identify evidence gaps (legs without records, records without a leg, custody-chain time gaps)
  * synthesise    — attribute each excursion / gap to a custody leg and emit an exception item with a
                    **candidate/placeholder** owner + severity + evidence citations

Numeric comparison is deterministic; the production LLM is reserved for narrative phrasing only. The
reconciler never decides product release / disposition — that stays with an authorised human.
"""

from __future__ import annotations

from typing import Any

# ── seeded approved-threshold KB (product handling class → approved temperature band, °C) ──────
# Source: public cold-chain handling standards (GDP / HACCP handling classes). No PII.
THRESHOLD_KB: dict[str, dict[str, Any]] = {
    "frozen": {"min_c": -25.0, "max_c": -18.0, "label": "冷凍 (frozen)"},
    "deep_frozen": {"min_c": -80.0, "max_c": -60.0, "label": "超低温 (deep-frozen)"},
    "chilled": {"min_c": 2.0, "max_c": 8.0, "label": "冷蔵 (chilled)"},
    "pharma_cold": {"min_c": 2.0, "max_c": 8.0, "label": "医薬定温 2-8℃ (pharma cold)"},
    "ambient_controlled": {"min_c": 15.0, "max_c": 25.0, "label": "定温 (ambient-controlled)"},
}

_SEVERITY_HIGH_C = 5.0  # |Δ| ≥ 5.0 °C beyond band → high
_SEVERITY_MED_C = 2.0  # |Δ| ≥ 2.0 °C beyond band → medium

# The platform's S-2 personal-data pass runs before any template code and replaces anything its
# person-name heuristic matches — a run of two or more Title-Case words, which covers most carrier and
# warehouse names ("Nippon Express", "Reefer Co"; "Yamato Transport Co., Ltd." becomes
# "[MASKED]., Ltd.") — with this token. The template cannot switch that pass off (the input gate is
# final), so the accountable party of a masked leg is unknown and is handled like an unassigned owner.
PLATFORM_MASK_TOKEN = "[MASKED]"
# Stable machine-readable limitation codes (the human-readable explanation goes in the packet message).
PARTY_NAME_MASKED = "PARTY_NAME_MASKED"
SENSOR_ID_MASKED = "SENSOR_ID_MASKED"


def sensor_surrogate(sensor_id: str, position: int) -> str:
    """Keep a masked sensor id distinguishable: qualify it with the reading's 1-based position.

    Sensor ids written as Title-Case words ("Front Sensor", "Rear Sensor") are masked too, so two
    sensors read at the same time become the same "[MASKED]" and their citations collapse into one.
    "[MASKED] (record 2)" points at the second entry of the caller's ``temperature_records``, which
    is where a person finds the original id. Unmasked ids are returned unchanged.
    """
    if PLATFORM_MASK_TOKEN not in sensor_id:
        return sensor_id
    return f"{sensor_id} (record {position})"


def masked_sensor_ids(organized: dict[str, Any]) -> list[str]:
    """Surrogate ids of every temperature record whose sensor id was (fully or partly) masked."""
    records = [r for recs in organized.get("records_by_leg", {}).values() for r in recs]
    records += organized.get("orphan_records", [])
    return sorted(
        {str(r.get("sensor_id")) for r in records if PLATFORM_MASK_TOKEN in str(r.get("sensor_id", ""))},
        key=lambda sid: (len(sid), sid),
    )


def masked_party_legs(legs: list[dict[str, Any]]) -> list[str]:
    """Ids of the custody legs whose party name was (fully or partly) masked, in leg order."""
    return [str(leg.get("leg_id")) for leg in legs if PLATFORM_MASK_TOKEN in str(leg.get("party", ""))]


class ColdChainReconciler:
    """Deterministic threshold reconciliation + evidence-gap detection over cold-chain handover evidence."""

    # ── threshold resolution ──────────────────────────────────────────────────────────────
    @staticmethod
    def resolve_thresholds(product_class: str | None, explicit: dict[str, Any] | None) -> dict[str, Any] | None:
        """Explicit thresholds win; else fall back to the product-class KB. None → un-reconcilable."""
        if explicit and isinstance(explicit, dict) and "min_c" in explicit and "max_c" in explicit:
            try:
                return {
                    "min_c": float(explicit["min_c"]),
                    "max_c": float(explicit["max_c"]),
                    "label": str(explicit.get("label", "explicit")),
                    "source": "explicit",
                }
            except (TypeError, ValueError):
                return None
        key = (product_class or "").strip().lower()
        if key in THRESHOLD_KB:
            band = THRESHOLD_KB[key]
            return {
                "min_c": band["min_c"],
                "max_c": band["max_c"],
                "label": band["label"],
                "source": f"product_class:{key}",
            }
        return None

    # ── organise (classify) ───────────────────────────────────────────────────────────────
    @staticmethod
    def organize(custody_legs: list[dict[str, Any]], temperature_records: list[dict[str, Any]]) -> dict[str, Any]:
        """Group temperature records under their custody leg; surface records with no matching leg."""
        legs = [
            {
                "leg_id": str(leg.get("leg_id", f"L{i + 1}")),
                "party": str(leg.get("party", "")),
                "from_ts": str(leg.get("from_ts", "")),
                "to_ts": str(leg.get("to_ts", "")),
                "handover_doc": str(leg.get("handover_doc", "")),
            }
            for i, leg in enumerate(custody_legs)
            if isinstance(leg, dict)
        ]
        leg_ids = {leg["leg_id"] for leg in legs}
        records_by_leg: dict[str, list[dict[str, Any]]] = {leg["leg_id"]: [] for leg in legs}
        orphan_records: list[dict[str, Any]] = []
        for position, r in enumerate(temperature_records, start=1):
            if not isinstance(r, dict):
                continue
            rec = {
                # A masked sensor id is qualified with its position so two masked sensors stay distinct.
                "sensor_id": sensor_surrogate(str(r.get("sensor_id", "")), position),
                "ts": str(r.get("ts", "")),
                "temp_c": r.get("temp_c"),
                "leg_id": str(r.get("leg_id", "")),
            }
            if rec["leg_id"] in leg_ids:
                records_by_leg[rec["leg_id"]].append(rec)
            else:
                orphan_records.append(rec)
        return {"legs": legs, "records_by_leg": records_by_leg, "orphan_records": orphan_records}

    # ── reconcile ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def reconcile(organized: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
        """Compare each reading against the approved band and identify evidence gaps."""
        min_c, max_c = float(thresholds["min_c"]), float(thresholds["max_c"])
        evaluations: list[dict[str, Any]] = []
        for leg_id, records in organized["records_by_leg"].items():
            for rec in records:
                temp = rec.get("temp_c")
                try:
                    temp_f = float(temp)
                except (TypeError, ValueError):
                    evaluations.append(
                        {
                            **{k: rec[k] for k in ("sensor_id", "ts", "leg_id")},
                            "temp_c": temp,
                            "verdict": "unreadable",
                            "delta_c": None,
                        }
                    )
                    continue
                if temp_f > max_c:
                    verdict, delta = "over_temp", round(temp_f - max_c, 3)
                elif temp_f < min_c:
                    verdict, delta = "under_temp", round(min_c - temp_f, 3)
                else:
                    verdict, delta = "in_range", 0.0
                evaluations.append(
                    {
                        "sensor_id": rec["sensor_id"],
                        "ts": rec["ts"],
                        "leg_id": leg_id,
                        "temp_c": temp_f,
                        "verdict": verdict,
                        "delta_c": delta,
                    }
                )

        gaps: list[dict[str, Any]] = []
        for leg_rec in organized["legs"]:
            if not organized["records_by_leg"].get(leg_rec["leg_id"]):
                gaps.append(
                    {
                        "gap_type": "leg_without_temperature_record",
                        "leg_id": leg_rec["leg_id"],
                        "party": leg_rec["party"],
                        "detail": "custody leg has no temperature evidence",
                    }
                )
        for orphan in organized["orphan_records"]:
            gaps.append(
                {
                    "gap_type": "record_without_custody_leg",
                    "leg_id": orphan.get("leg_id") or None,
                    "sensor_id": orphan.get("sensor_id"),
                    "ts": orphan.get("ts"),
                    "detail": "temperature record does not map to any custody leg",
                }
            )
        gaps.extend(ColdChainReconciler._chain_gaps(organized["legs"]))
        return {"evaluations": evaluations, "evidence_gaps": gaps}

    @staticmethod
    def _chain_gaps(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Detect discontinuities in the custody chain (a hand-off time gap between consecutive legs)."""
        ordered = sorted([leg for leg in legs if leg.get("from_ts")], key=lambda leg: leg["from_ts"])
        out: list[dict[str, Any]] = []
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev.get("to_ts") and nxt.get("from_ts") and prev["to_ts"] < nxt["from_ts"]:
                out.append(
                    {
                        "gap_type": "custody_chain_time_gap",
                        "leg_id": prev["leg_id"],
                        "next_leg_id": nxt["leg_id"],
                        "from_ts": prev["to_ts"],
                        "to_ts": nxt["from_ts"],
                        "detail": "custody hand-off has an uncovered time interval",
                    }
                )
        return out

    # ── synthesise exceptions ─────────────────────────────────────────────────────────────
    @staticmethod
    def synthesize(reconciliation: dict[str, Any], organized: dict[str, Any]) -> list[dict[str, Any]]:
        """Attribute excursions / gaps to custody legs; emit exception items with a candidate owner.

        `candidate_owner` is a **placeholder** — owner confirmation and any product-release / disposition
        decision are deferred to an authorised human (HumanGate). This method never decides disposition.
        """
        party_by_leg = {leg["leg_id"]: leg["party"] for leg in organized["legs"]}
        exceptions: list[dict[str, Any]] = []
        for ev in reconciliation["evaluations"]:
            if ev["verdict"] in ("in_range", "unreadable"):
                continue
            delta = ev.get("delta_c") or 0.0
            exceptions.append(
                {
                    "leg_id": ev["leg_id"],
                    "candidate_owner": party_by_leg.get(ev["leg_id"])
                    or "UNASSIGNED (candidate — confirm via HumanGate)",
                    "excursion_type": ev["verdict"],
                    "severity": ColdChainReconciler._severity(delta),
                    "delta_c": delta,
                    "citations": [{"sensor_id": ev["sensor_id"], "ts": ev["ts"], "leg_id": ev["leg_id"]}],
                }
            )
        for gap in reconciliation["evidence_gaps"]:
            leg_id = gap.get("leg_id")
            cite = {"leg_id": leg_id}
            if gap.get("sensor_id"):
                cite["sensor_id"] = gap["sensor_id"]
            if gap.get("ts"):
                cite["ts"] = gap["ts"]
            exceptions.append(
                {
                    "leg_id": leg_id,
                    "candidate_owner": party_by_leg.get(leg_id) or "UNASSIGNED (candidate — confirm via HumanGate)",
                    "excursion_type": "evidence_gap",
                    "severity": "medium",
                    "gap_type": gap["gap_type"],
                    "citations": [cite],
                }
            )
        return exceptions

    @staticmethod
    def _severity(delta_c: float) -> str:
        mag = abs(float(delta_c or 0.0))
        if mag >= _SEVERITY_HIGH_C:
            return "high"
        if mag >= _SEVERITY_MED_C:
            return "medium"
        return "low"
