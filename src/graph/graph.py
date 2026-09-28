"""AgentCore Platform v1.0 — RET-C2-571 outer graph (Cat 2).

Cat 2: AgentBaseGraph fixed 5-node backbone with a GraphNode in the `main`
slot wrapping the inner CategoryIntelligenceWorkflow (src/graph/
domain_workflow_graph.py).

    START → initialize → pre_process → main → {route} → post_process → finalize → END

Domain complexity lives inside CategoryIntelligenceGraphNode.get_subgraph().
Dependency injection (llm / secrets_get / fetcher) flows from this graph's
config into the inner graph via _parent_config(), then into the nodes.
add_edges() is NOT overridden — backbone wiring belongs to the framework.
"""

from __future__ import annotations

from typing import ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class CategoryIntelligenceGraphNode(GraphNode):
    """`main` slot — wraps the inner CategoryIntelligenceWorkflow."""

    # Re-raise inner errors so the backbone surfaces them (fail fast). The
    # fusion "no internal sales -> error" outcome is propagated this way.
    error_strategy: ClassVar[str] = "propagate"

    # TargetScoping HITL stays inside the inner graph by default; surface it
    # to the outer caller so the backbone can pause/resume (hitl.enabled).
    propagate_hitl: ClassVar[bool] = True

    def __init__(self, parent_config: dict | None = None) -> None:
        super().__init__()
        self._injected = parent_config or {}
        # EAGERLY build+compile the inner graph ONCE at construction (not lazily
        # in get_subgraph). Lazy init is unsafe: GraphNode.execute() may run
        # concurrently, and two cold-start invokes could each compile a distinct
        # inner graph/saver — a suspend created with one saver would then resume
        # through the other and lose its checkpoint. The node is constructed once
        # in register_nodes() before any invoke, so eager init is race-free.
        self._subgraph = self._build_subgraph()

    def _build_subgraph(self):
        from langgraph.checkpoint.memory import InMemorySaver

        from src.graph.domain_workflow_graph import CategoryIntelligenceWorkflow

        sub = CategoryIntelligenceWorkflow(config=self._parent_config())
        # OWN checkpointer instance — distinct from the outer graph's saver, or
        # their thread namespaces collide. Compiled once so suspend + resume hit
        # the SAME saver (a fresh graph+saver per call loses the checkpoint and
        # re-interrupts forever / surfaces as status=error).
        sub.compile(checkpointer=InMemorySaver())
        return sub

    def get_subgraph(self):
        """Return the cached, compiled inner graph (built eagerly in __init__)."""
        return self._subgraph

    def extract_input(self, state: AgentState) -> str:
        """Serialize the normalized request into the inner graph's user_input.

        Cat 2 inner graphs receive only a string user_input + ctx — the outer
        `request`/`input_context` is NOT auto-propagated. We JSON-encode the
        full request so the inner TargetScopingNode can rebuild it. Falls back
        to the plain topic string if request is absent.
        """
        import json

        request = state.get("request")
        if isinstance(request, dict) and request:
            return json.dumps(request, ensure_ascii=False, default=str)
        return state.get("validated_input", state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: dict) -> dict:
        return {
            "output": sub_result.get("output"),
            "result": sub_result.get("output"),
            "fusion_report": sub_result.get("fusion_report", {}),
            "status": sub_result.get("status"),
            "error_log": sub_result.get("error_log", []),
        }

    def _parent_config(self) -> dict:
        # Forward injected dependencies (llm / secrets_get / fetcher) to the
        # inner graph. request fields are carried in State, not here.
        #
        # HITL also flows down: TargetScopingNode calls interrupt() inside the
        # inner graph, so the INNER compile() must attach a checkpointer. That
        # only happens when the inner config carries memory_enabled/hitl, so we
        # propagate them (defaulting on, since this template uses HITL).
        return {
            "llm": self._injected.get("llm"),
            "secrets_get": self._injected.get("secrets_get"),
            "fetcher": self._injected.get("fetcher"),
            "memory_enabled": self._injected.get("memory_enabled", True),
            "hitl": self._injected.get("hitl", {"enabled": True, "max_hitl": 4}),
        }


class CategoryIntelligenceOrchestratorAgent(AgentBaseGraph):
    """Retail Category Intelligence Orchestrator — Cat 2 outer graph.

    Exported class name matches config/agent.yaml `class:`.
    """

    # S-1 trust gate: declare the minimum caller trust
    # level in the class body so the framework enforces it — config/agent.yaml
    # is registry metadata only and is NOT read by the S-1 gate. VERIFIED_EXTERNAL
    # is the minimum for standard business operations (external data + LLM calls).
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    @property
    def name(self) -> str:
        return "Retail Category Intelligence Orchestrator"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = CategoryIntelligenceGraphNode(parent_config=self.config)
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


# Backwards-compatible alias (some tooling imports `Graph`).
Graph = CategoryIntelligenceOrchestratorAgent
