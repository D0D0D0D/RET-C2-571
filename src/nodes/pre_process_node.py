"""AgentCore Platform v1.0 — RET-C2-571 pre_process (RequestNormalizeNode)."""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via state.get("input_context", {}) — read-only [C1]

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# Upper bound on the raw user_input the outer backbone forwards to the inner
# graph. Cat-2 outer pre_process is the first S-2 enforcement point; an
# oversized payload is rejected here before any inner dispatch.
_MAX_INPUT_LEN = 20_000


class PreProcessNode(FunctionNode):
    """Validate input and normalize it into a research request dict.

    Cat-2 outer backbone entry: this is the first
    security layer before the inner graph runs, so it declares the S-1 trust
    level and adds an S-2 input check on top of the framework PII scan.
    """

    # S-1 trust gate: outer backbone entry — a verified caller is required
    # before the inner domain graph (external data + LLM) is dispatched.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        """S-2 domain check: bound raw user_input size before inner dispatch.

        Per the hook contract this MUST NOT raise — it sets status=error and
        appends to error_log; __call__() then short-circuits before execute().
        """
        user_input = state.get("user_input", "")
        if isinstance(user_input, str) and len(user_input) > _MAX_INPUT_LEN:
            return {
                **state,
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    *state.get("error_log", []),
                    f"PreProcessNode: user_input exceeds {_MAX_INPUT_LEN} chars",
                ],
            }
        return state

    def execute(self, state: dict) -> dict:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only [C1]

        if not user_input or not str(user_input).strip():
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input is empty or missing"],
            }

        request = self._build_request(user_input, input_context)
        # S-4: audit the outer-backbone entry (input accepted into the pipeline).
        emit_trace_event(
            "request_normalized",
            {"topic": request.get("topic", ""), "has_inline_data": "inline_data" in request},
            state,
        )
        return {
            "validated_input": str(user_input).strip(),
            "request": request,
            "status": AgentStatus.SUCCESS.value,
        }

    def _build_request(self, user_input, input_context: dict) -> dict:
        """Normalize either a JSON request payload or a plain topic string."""
        request: dict = {
            "topic": "",
            "target": "自社",
            "period_days": 30,
            "sources": ["free"],
            "output_format": "ringi",
        }
        # JSON payload (structured request) takes precedence
        if isinstance(user_input, str) and user_input.strip().startswith("{"):
            try:
                payload = json.loads(user_input)
                if isinstance(payload, dict):
                    request.update({k: v for k, v in payload.items() if v is not None})
            except (json.JSONDecodeError, ValueError):
                request["topic"] = user_input.strip()
        else:
            request["topic"] = str(user_input).strip()

        # inline data / data_url may arrive via input_context (read-only)
        for key in ("inline_data", "data_url"):
            if key in input_context:
                request[key] = input_context[key]
        return request
