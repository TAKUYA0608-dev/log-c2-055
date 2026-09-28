# LOG-C2-055 — Integration: the real invoke() path against the shipped SDK, with the platform's
# S-2 personal-data masking active.
#
# The platform's S-2 personal-data pass runs before this template's nodes and replaces any run of two
# or more Title-Case words with "[MASKED]" — which covers most carrier names. Measured 2026-09-23 on
# AgentCore 1.0.3 through this production path: "Nippon Express" and "Sagawa Express" both reached the
# reconciliation as "[MASKED]", were shown as one candidate owner, and — because a masked owner looked
# like a known owner — neither exception was routed to review (an unassigned owner would have been).
# These tests take the production path (real Graph().invoke(), VERIFIED_EXTERNAL caller) and pin the
# fix: a masked party is handled like an unassigned owner, `limitations == ["PARTY_NAME_MASKED"]`.

import json
from typing import Any

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

_CODE = "PARTY_NAME_MASKED"
_MASKED_REASON = "owner name masked by the platform"


def _ctx() -> InvocationContext:
    return InvocationContext(caller_id="integration-test", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _bundle(party_l1: str, party_l2: str, temp_l1: float = -15.0, temp_l2: float = -16.5) -> dict[str, Any]:
    # Frozen band is -25..-18 °C: the defaults give L1 a medium (+3.0 °C) and L2 a low (+1.5 °C)
    # over-temperature excursion — neither is escalated when its owner is known.
    return {
        "shipment_id": "SHP-COLD-001",
        "product_class": "frozen",
        "access_scope": "cold_chain_qa",
        "custody_legs": [
            {"leg_id": "L1", "party": party_l1, "from_ts": "2026-07-01T08:00:00", "to_ts": "2026-07-01T14:00:00"},
            {"leg_id": "L2", "party": party_l2, "from_ts": "2026-07-01T14:00:00", "to_ts": "2026-07-01T20:00:00"},
        ],
        "temperature_records": [
            {"sensor_id": "S1", "ts": "2026-07-01T09:00:00", "temp_c": temp_l1, "leg_id": "L1"},
            {"sensor_id": "S2", "ts": "2026-07-01T16:00:00", "temp_c": temp_l2, "leg_id": "L2"},
        ],
    }


def _packet(bundle: dict[str, Any]) -> dict[str, Any]:
    agent = Graph()
    agent.compile()
    out = agent.invoke(json.dumps(bundle), ctx=_ctx())
    assert str(out.get("status")).lower().endswith("success"), out.get("status")
    packet = json.loads(out["output"]) if isinstance(out["output"], str) else out["output"]
    assert isinstance(packet, dict), packet
    assert packet["status_kind"] == "reconciliation", packet
    return packet


def _parties(packet: dict[str, Any]) -> list[str]:
    return [leg["party"] for leg in packet["custody_timeline"]]


def _reviewed(packet: dict[str, Any]) -> dict[str, str]:
    return {item["leg_id"]: item["reason"] for item in packet["review_items"]}


class TestPlatformMaskedCustodyParty:
    def test_masked_parties_are_routed_to_a_person(self) -> None:
        packet = _packet(_bundle("Nippon Express", "Sagawa Express"))
        assert _parties(packet) == ["[MASKED]", "[MASKED]"], "precondition: the platform masked both parties"
        reviewed = _reviewed(packet)
        assert set(reviewed) == {"L1", "L2"}, packet["review_items"]
        assert all(_MASKED_REASON in reason for reason in reviewed.values()), reviewed
        assert packet["human_review_status"] == "pending_review"
        assert packet["limitations"] == [_CODE], packet
        assert "L1, L2" in packet["message"], packet["message"]

    def test_two_masked_carriers_are_not_shown_as_one_owner(self) -> None:
        packet = _packet(_bundle("Nippon Express", "Sagawa Express"))
        assert _parties(packet) == ["[MASKED]", "[MASKED]"], "precondition: the platform masked both parties"
        assert packet["candidate_owners"] == ["[MASKED] (leg L1)", "[MASKED] (leg L2)"]

    def test_an_unmasked_first_leg_does_not_hide_a_masked_second_one(self) -> None:
        # L1 carries the more severe excursion and its party reaches the agent unchanged (all caps);
        # only L2's party is masked. L2's owner is still unknown and must go to a person.
        clean = _packet(_bundle("NIPPON EXPRESS", "SAGAWA EXPRESS"))
        assert clean["review_items"] == [], "precondition: with both owners known nothing is escalated"
        packet = _packet(_bundle("NIPPON EXPRESS", "Sagawa Express"))
        assert _parties(packet) == ["NIPPON EXPRESS", "[MASKED]"], "precondition: only the second party is masked"
        assert [e["severity"] for e in packet["exceptions"]] == ["medium", "low"]
        reviewed = _reviewed(packet)
        assert list(reviewed) == ["L2"], packet["review_items"]
        assert _MASKED_REASON in reviewed["L2"]
        assert packet["candidate_owners"] == ["NIPPON EXPRESS", "[MASKED] (leg L2)"]
        assert packet["limitations"] == [_CODE], packet

    def test_a_partly_masked_party_counts_as_masked(self) -> None:
        # The designator after the comma is outside the Title-Case run, so only part of the name is
        # replaced; the accountable party is still unknown.
        packet = _packet(_bundle("Yamato Transport Co., Ltd.", "Carrier A"))
        assert _parties(packet) == ["[MASKED]., Ltd.", "Carrier A"], "precondition: the first party is partly masked"
        assert list(_reviewed(packet)) == ["L1"], packet["review_items"]
        assert packet["candidate_owners"] == ["Carrier A", "[MASKED]., Ltd. (leg L1)"]
        assert packet["limitations"] == [_CODE], packet

    def test_numeric_reconciliation_is_unchanged_by_masking(self) -> None:
        masked = _packet(_bundle("Nippon Express", "Sagawa Express"))
        unmasked = _packet(_bundle("NIPPON EXPRESS", "SAGAWA EXPRESS"))  # all caps is not masked
        assert masked["evaluations"] == unmasked["evaluations"]
        assert masked["evidence_gaps"] == unmasked["evidence_gaps"]
        assert [e["severity"] for e in masked["exceptions"]] == [e["severity"] for e in unmasked["exceptions"]]

    def test_masked_parties_without_exceptions_are_still_reported(self) -> None:
        packet = _packet(_bundle("Nippon Express", "Sagawa Express", temp_l1=-20.0, temp_l2=-20.0))
        assert _parties(packet) == ["[MASKED]", "[MASKED]"], "precondition: the platform masked both parties"
        assert packet["exceptions"] == [] and packet["review_items"] == []
        assert packet["human_review_status"] == "no_exceptions"
        assert packet["limitations"] == [_CODE], packet

    def test_unmasked_owners_behave_as_before(self) -> None:
        packet = _packet(_bundle("NIPPON EXPRESS", "SAGAWA EXPRESS"))
        assert _parties(packet) == ["NIPPON EXPRESS", "SAGAWA EXPRESS"], "precondition: nothing is masked"
        assert packet["candidate_owners"] == ["NIPPON EXPRESS", "SAGAWA EXPRESS"]
        assert packet["review_items"] == []  # medium / low excursions with a known owner are not escalated
        assert packet["message"] is None
        assert packet["limitations"] == []


# ── Sensor ids written as Title-Case words are masked too ─────────────────────
#
# Measured on AgentCore 1.0.3 through this production path: two sensors "Front Sensor" and
# "Rear Sensor" read at the same time both reached the reconciliation as "[MASKED]", their two
# citations collapsed into one, `citation_complete` stayed true, and no code said so. Each masked
# sensor id is now qualified with its position in `temperature_records`.

_SENSOR_CODE = "SENSOR_ID_MASKED"


def _sensor_bundle(sensor_1: str, sensor_2: str, party: str = "CARRIER A") -> dict[str, Any]:
    # One leg, two readings at the same time, both over the frozen band (-25..-18 °C).
    return {
        "shipment_id": "SHP-COLD-002",
        "product_class": "frozen",
        "access_scope": "cold_chain_qa",
        "custody_legs": [
            {"leg_id": "L1", "party": party, "from_ts": "2026-07-01T08:00:00", "to_ts": "2026-07-01T14:00:00"}
        ],
        "temperature_records": [
            {"sensor_id": sensor_1, "ts": "2026-07-01T09:00:00", "temp_c": -15.0, "leg_id": "L1"},
            {"sensor_id": sensor_2, "ts": "2026-07-01T09:00:00", "temp_c": -16.0, "leg_id": "L1"},
        ],
    }


def _sensor_ids(packet: dict[str, Any], field: str) -> list[str]:
    return [str(item["sensor_id"]) for item in packet[field]]


class TestPlatformMaskedSensorIds:
    def test_two_masked_sensors_read_at_the_same_time_stay_two_citations(self) -> None:
        packet = _packet(_sensor_bundle("Front Sensor", "Rear Sensor"))
        evaluated = _sensor_ids(packet, "evaluations")
        assert all(sid.startswith("[MASKED]") for sid in evaluated), "precondition: the platform masked both ids"
        assert evaluated == ["[MASKED] (record 1)", "[MASKED] (record 2)"], evaluated
        assert _sensor_ids(packet, "citations") == evaluated, packet["citations"]
        assert packet["citation_complete"] is True
        assert packet["limitations"] == [_SENSOR_CODE], packet
        assert "record" in packet["message"], packet["message"]

    def test_a_readable_sensor_id_does_not_hide_a_masked_one(self) -> None:
        packet = _packet(_sensor_bundle("S1", "Rear Sensor"))
        evaluated = _sensor_ids(packet, "evaluations")
        assert evaluated[0] == "S1", "precondition: the first sensor id reached the agent unchanged"
        assert evaluated == ["S1", "[MASKED] (record 2)"], evaluated
        assert _sensor_ids(packet, "citations") == evaluated
        assert packet["limitations"] == [_SENSOR_CODE], packet

    def test_masked_party_and_sensor_ids_report_both_codes(self) -> None:
        packet = _packet(_sensor_bundle("Front Sensor", "Rear Sensor", party="Nippon Express"))
        assert _parties(packet) == ["[MASKED]"], "precondition: the party was masked too"
        assert packet["limitations"] == [_CODE, _SENSOR_CODE], packet

    def test_unmasked_sensor_ids_are_unchanged(self) -> None:
        packet = _packet(_sensor_bundle("FRONT SENSOR", "REAR SENSOR"))
        assert _sensor_ids(packet, "evaluations") == ["FRONT SENSOR", "REAR SENSOR"]
        assert _sensor_ids(packet, "citations") == ["FRONT SENSOR", "REAR SENSOR"]
        assert packet["citation_complete"] is True
        assert packet["limitations"] == []


class TestMergedMaskedCitationsAreNotComplete:
    """The packet composer's own guard: if two readings ever share one masked citation key, neither is
    individually traceable, so the packet must not claim `citation_complete`."""

    def test_two_readings_behind_one_masked_citation_are_not_complete(self) -> None:
        from src.nodes.post_process_node import PostProcessNode

        reading = {"leg_id": "L1", "sensor_id": "[MASKED]", "ts": "2026-07-01T09:00:00"}
        report = {
            "status_kind": "reconciliation",
            "evaluations": [dict(reading, temp_c=-15.0), dict(reading, temp_c=-16.0)],
            "exceptions": [],
            "evidence_gaps": [],
            "citations": [reading],
        }
        out = PostProcessNode().execute({"result": json.dumps(report)})
        packet = json.loads(out["formatted_output"])
        assert packet["citation_complete"] is False, packet
