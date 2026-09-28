"""AgentCore Platform v1.0 — RET-C2-571 inner CategoryIntelligenceWorkflow.

Cat 2 inner graph (BaseGraph, fully custom topology). Instantiated by
CategoryIntelligenceGraphNode.get_subgraph() in graph.py.

Pipeline:
    START -> target_scoping -> data_acquire -> trend_research
          -> fusion -> {route} -> report_gen -> END
                          \\-> END (when fusion sets status=error: no sales data)

Dependency injection: config may carry `llm` (BaseLLM), `secrets_get`
(ctx.secrets.get), and `fetcher` (test seam). These are threaded into the
nodes that need them so unit/integration tests run offline.
"""

from __future__ import annotations

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.nodes.data_acquire_node import DataAcquireNode
from src.nodes.fusion_node import FusionNode
from src.nodes.report_gen_node import ReportGenNode
from src.nodes.target_scoping_node import TargetScopingNode
from src.nodes.trend_research_node import TrendResearchNode
from src.schemas.state import State


class CategoryIntelligenceWorkflow(BaseGraph):
    """Inner multi-step domain workflow for category intelligence."""

    @property
    def name(self) -> str:
        return "CategoryIntelligenceWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        # No mandatory config: llm/secrets_get/fetcher are all optional
        # (no LLM -> code summary; no secrets -> free tier only; no fetcher
        # -> real urllib egress).
        pass

    def register_nodes(self) -> None:
        # No super() call — BaseGraph.register_nodes() is abstract.
        llm = self.config.get("llm")
        secrets_get = self.config.get("secrets_get")
        fetcher = self.config.get("fetcher")

        self._nodes["target_scoping"] = TargetScopingNode()
        self._nodes["data_acquire"] = DataAcquireNode()
        self._nodes["trend_research"] = TrendResearchNode(secrets_get=secrets_get, fetcher=fetcher)
        self._nodes["fusion"] = FusionNode()
        self._nodes["report_gen"] = ReportGenNode(llm=llm)

    def add_edges(self) -> None:
        self._sg.add_edge(START, "target_scoping")
        self._sg.add_edge("target_scoping", "data_acquire")
        self._sg.add_edge("data_acquire", "trend_research")
        self._sg.add_edge("trend_research", "fusion")
        # fusion may abort (no internal sales) -> skip report, go to END
        self._sg.add_conditional_edges("fusion", self.route, {"report_gen": "report_gen", END: END})
        self._sg.add_edge("report_gen", END)

    def route(self, state) -> str:
        """After fusion: error (no sales data) ends the workflow; else report."""
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "report_gen"

    def get_output(self, state) -> dict:
        return {
            "output": state.get("output") or state.get("result"),
            "status": state.get("status"),
            "fusion_report": state.get("fusion_report", {}),
            "error_log": state.get("error_log", []),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
