"""AgentCore Platform v1.0 — RET-C2-571 ReportGenNode (inner step 5).

Builds the buyer decision report (ringi format) from the fusion result and
citations. The LLM is injected (BaseLLM) so tests run with a fake. S-3 output
hook layers a 景表法 expression check on top of the framework credential scan
(preservation variant: annotate rather than delete).
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.base_llm import BaseLLM
from shared.utils.audit_logger import emit_trace_event
from src.services import keihyo_gate, report


class ReportGenNode(FunctionNode):
    """Generate the final report; S-3 annotates risky 景表法 expressions."""

    # S-1 trust gate: invokes an external LLM to
    # generate the report — a privileged op requiring a verified caller.
    # Enforced per-node by BaseNode.__call__.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: BaseLLM | None = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict) -> dict:
        scope = state.get("scope", {})
        fusion_report = state.get("fusion_report", {})
        trend = state.get("trend_data", {})
        request = state.get("request", {})

        text = report.build_report(
            scope=scope,
            fusion_report=fusion_report,
            citations=trend.get("citations", []),
            degraded_sources=trend.get("degraded_sources", []),
            output_format=request.get("output_format", "ringi"),
            llm=self._llm,
        )
        emit_trace_event("report_generated", {"length": len(text)}, state)
        return {"output": text, "result": text, "status": AgentStatus.SUCCESS.value}

    def _extra_security_gate_output(self, result: dict) -> dict:
        """S-3 domain check: annotate 景表法-risky expressions (preservation).

        Runs after the framework @final credential scan. Non-destructive:
        appends a caution footer so a human reviewer sees claim + warning.
        """
        for key in ("output", "result"):
            val = result.get(key)
            if isinstance(val, str):
                result[key] = keihyo_gate.annotate_if_risky(val)
        return result
