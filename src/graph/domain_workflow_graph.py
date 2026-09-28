"""LOG-C2-055 — inner domain workflow graph (Cat 2).

Instantiated by EvidenceReconciliationWorkflowGraphNode.get_subgraph() in graph.py. Linear topology with
per-node skip guards (the portable Cat 2 form; conditional edges don't propagate across the subgraph
boundary):

    START → custody_temperature_classify → threshold_reconcile → exception_synthesis → human_gate → END

On rejected / 0-evidence input, custody_temperature_classify sets reconcile_hit_count=0 (+error_code);
threshold_reconcile and exception_synthesis no-op and human_gate emits the out-of-scope safe answer —
no fabricated reconciliation.
"""

from __future__ import annotations
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState

from src.nodes.custody_temperature_classify_node import CustodyTemperatureClassifyNode
from src.nodes.exception_synthesis_node import ExceptionSynthesisNode
from src.nodes.human_gate_node import HumanGateNode
from src.nodes.threshold_reconcile_node import ThresholdReconcileNode
from src.schemas.state import State


class ColdChainReconciliationWorkflow(BaseGraph):
    """Inner graph: classify → reconcile → synthesis → human_gate."""

    @property
    def name(self) -> str:
        return "ColdChainReconciliationWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract.
        self._nodes["custody_temperature_classify"] = CustodyTemperatureClassifyNode()
        self._nodes["threshold_reconcile"] = ThresholdReconcileNode()
        self._nodes["exception_synthesis"] = ExceptionSynthesisNode()
        self._nodes["human_gate"] = HumanGateNode()

    def add_edges(self) -> None:
        # Static linear backbone; the 0-evidence / rejected skip is handled by per-node guards.
        self._sg.add_edge(START, "custody_temperature_classify")
        self._sg.add_edge("custody_temperature_classify", "threshold_reconcile")
        self._sg.add_edge("threshold_reconcile", "exception_synthesis")
        self._sg.add_edge("exception_synthesis", "human_gate")
        self._sg.add_edge("human_gate", END)

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph ABC. Linear topology → not wired to a conditional edge."""
        if state.get("error_code") or state.get("reconcile_hit_count", 0) == 0:
            return "human_gate"
        return "threshold_reconcile"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "reconcile_hit_count": state.get("reconcile_hit_count", 0),
            "human_review_status": state.get("human_review_status"),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
