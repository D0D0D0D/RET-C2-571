"""AgentCore Platform v1.0 — RET-C2-571 TargetScopingNode (inner step 1).

Confirms the research scope (product/category, target, period, metrics).
If the scope is unambiguous it proceeds; if ambiguous it raises a D6 HITL
interrupt() to confirm with a human. hitl_draft guards against LLM/logic
re-execution on resume.
"""

from __future__ import annotations

from typing import ClassVar

from langgraph.types import interrupt

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

REQUIRED_SCOPE_KEYS = ("topic",)
DEFAULT_PERIOD_DAYS = 30


class TargetScopingNode(FunctionNode):
    """Resolve the research scope; confirm via HITL only when ambiguous."""

    # S-1: declared explicitly by design, not inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        request = self._resolve_request(state)
        topic = (request.get("topic") or "").strip()

        scope = {
            "topic": topic,
            "target": request.get("target") or "自社",
            "period_days": int(request.get("period_days") or DEFAULT_PERIOD_DAYS),
            "metrics": request.get("metrics") or ["sales", "trend"],
        }

        # Ambiguous when there is no usable topic OR the request explicitly asks
        # for human confirmation of scope (e.g. caller is unsure of target).
        ambiguous = (not topic) or bool(request.get("needs_confirmation"))

        if ambiguous:
            # Idempotency guard: keep the draft so resume does not recompute.
            draft = {"reason": "topic is missing or empty", "partial_scope": scope}
            # MANDATORY hitl_allowed guard: inside an
            # automated pipeline (parent GraphNode sets hitl_allowed=False) an
            # unconditional interrupt() would deadlock. Proceed with the partial
            # scope (default target/period/metrics) instead of pausing for a human.
            if not state.get("hitl_allowed", True):
                emit_trace_event("scope_hitl_skipped_not_allowed",
                                 {"reason": "hitl_allowed=false"}, state)
                request["topic"] = scope.get("topic", "")
                return {"request": request, "scope": scope, "hitl_draft": draft,
                        "status": AgentStatus.SUCCESS.value}
            feedback = interrupt({"draft": draft, "prompt": "調査トピックを指定してください"})
            # On resume `feedback` carries the human-provided scope/topic.
            confirmed = feedback if isinstance(feedback, dict) else {"topic": str(feedback)}
            # Only overwrite scope with truthy human values; falsy entries (0,
            # "") are treated as "not provided" and keep the existing default.
            scope.update({k: v for k, v in confirmed.items() if v})
            request["topic"] = scope.get("topic", "")
            emit_trace_event("scope_confirmed_via_hitl", {"topic": scope.get("topic")}, state)
            return {"request": request, "scope": scope, "hitl_draft": draft, "status": AgentStatus.SUCCESS.value}

        emit_trace_event("scope_resolved", {"topic": topic, "target": scope["target"]}, state)
        # Write request back into inner state so downstream nodes can read it.
        return {"request": request, "scope": scope, "status": AgentStatus.SUCCESS.value}

    def _resolve_request(self, state: dict) -> dict:
        """Get the request from state, or rebuild it from the inner user_input.

        Cat 2 inner graphs receive a JSON-serialized request as user_input
        (see graph.py extract_input). On the first inner node we decode it.
        """
        request = state.get("request")
        if isinstance(request, dict) and request:
            return dict(request)
        raw = state.get("user_input", "")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            import json

            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return {"topic": str(raw).strip()} if raw else {}
