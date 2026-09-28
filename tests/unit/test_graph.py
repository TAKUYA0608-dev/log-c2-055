# LOG-C2-055 — Unit Tests: Cat 2 graph wiring (outer GraphNode + inner workflow)

import pytest

from src.graph.domain_workflow_graph import ColdChainReconciliationWorkflow
from src.graph.graph import (
    EvidenceReconciliationWorkflowGraphNode,
    Graph,
    LogisticsColdChainHandoverExcursionEvidenceAgent,
)
from src.schemas.state import State


class TestOuterGraph:
    def test_registry_alias(self):
        assert LogisticsColdChainHandoverExcursionEvidenceAgent is Graph

    def test_name_and_state_schema(self):
        g = Graph()
        assert g.name == "LogisticsColdChainHandoverExcursionEvidenceAgent"
        assert g.state_schema is State

    def test_main_slot_is_graphnode(self):
        g = Graph()
        g.register_nodes()
        assert isinstance(g._nodes["main"], EvidenceReconciliationWorkflowGraphNode)
        for slot in ("pre_process", "main", "post_process"):
            assert slot in g._nodes

    def test_error_strategy_propagate(self):
        assert EvidenceReconciliationWorkflowGraphNode.error_strategy == "propagate"

    def test_get_subgraph_is_cached(self):
        node = EvidenceReconciliationWorkflowGraphNode()
        assert node.get_subgraph() is node.get_subgraph()

    def test_extract_input_prefers_validated(self):
        node = EvidenceReconciliationWorkflowGraphNode()
        assert node.extract_input({"validated_input": "V", "user_input": "U"}) == "V"

    def test_merge_output_maps_fields(self):
        node = EvidenceReconciliationWorkflowGraphNode()
        merged = node.merge_output({}, {"output": '{"x":1}', "reconcile_hit_count": 2, "status": "success",
                                        "human_review_status": "pending_review", "error_code": None})
        assert merged["result"] == '{"x":1}' and merged["reconcile_hit_count"] == 2
        assert merged["human_review_status"] == "pending_review"


class TestInnerWorkflow:
    def test_inner_registers_four_nodes(self):
        wf = ColdChainReconciliationWorkflow(config={})
        wf.register_nodes()
        for slot in ("custody_temperature_classify", "threshold_reconcile", "exception_synthesis", "human_gate"):
            assert slot in wf._nodes

    def test_route_zero_hit_to_human_gate(self):
        wf = ColdChainReconciliationWorkflow(config={})
        assert wf.route({"reconcile_hit_count": 0}) == "human_gate"

    def test_route_normal_to_reconcile(self):
        wf = ColdChainReconciliationWorkflow(config={})
        assert wf.route({"reconcile_hit_count": 2}) == "threshold_reconcile"

    def test_get_output_surfaces_result(self):
        wf = ColdChainReconciliationWorkflow(config={})
        out = wf.get_output({"result": "R", "status": "success", "reconcile_hit_count": 1,
                             "human_review_status": "no_exceptions"})
        assert out["output"] == "R" and out["reconcile_hit_count"] == 1


class TestServerModule:
    def test_server_imports(self):
        try:
            import src.api.server as server
        except ModuleNotFoundError as exc:
            pytest.skip(f"platform module unavailable in the local stub env: {exc}")
        assert server.app is not None and server.agent is not None
