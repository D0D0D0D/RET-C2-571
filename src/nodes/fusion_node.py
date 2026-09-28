"""AgentCore Platform v1.0 — RET-C2-571 FusionNode (inner step 4, core value).

Runs the quantitative fusion of internal sales/price data and external trend
signals (src.services.fusion). This is what makes the template an Agent rather
than a "paste-only" Tool.

Error boundary (design-confirmed): if internal sales data is unavailable,
fusion is impossible — return status=error and propagate. We do NOT emit a
market-trend-only report (degradation prevention).
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services import fusion


class FusionNode(FunctionNode):
    """Fuse internal performance with external trend; gate on missing data."""

    # S-1: declared explicitly by design, not inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        acquired = state.get("acquired_data", {})
        trend = state.get("trend_data", {})

        sales_ok = acquired.get("status") == "ok" and acquired.get("sales_series")
        if not sales_ok:
            # Both-source-fail rule: no internal data => fusion impossible.
            emit_trace_event(
                "fusion_aborted_no_sales",
                {"acquired_status": acquired.get("status")},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "FusionNode: acquired_data unavailable — fusion impossible "
                    "(market-trend-only report is not produced)"
                ],
            }

        fusion_report = fusion.fuse(acquired, trend)
        emit_trace_event(
            "fusion_completed",
            {
                "correlation_determinable": bool(
                    fusion_report["correlations"].get("determinable")
                ),
                "divergences": len(fusion_report["divergences"]),
                "inflections": len(fusion_report["inflections"]),
                "gaps": len(fusion_report["gaps"]),
            },
            state,
        )
        return {"fusion_report": fusion_report, "status": AgentStatus.SUCCESS.value}
