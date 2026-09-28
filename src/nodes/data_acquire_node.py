"""AgentCore Platform v1.0 — RET-C2-571 DataAcquireNode (inner step 2).

Acquires and normalizes sales/price data from user-provided inline data or a
public URL (S-2/SSRF guarded via src.services.safe_fetch). On failure it
records an explicit unavailable status rather than guessing.
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services import data_acquire

_MAX_URL_LEN = 2048


class DataAcquireNode(FunctionNode):
    """Acquire sales/price data; never crash on malformed input."""

    # S-1 trust gate: reaches an external data source
    # (user URL via SSRF-guarded fetch) — a privileged op requiring a verified
    # caller. The S-1 gate is enforced per-node by BaseNode.__call__, so the
    # level is declared here, not only on the graph class / agent.yaml.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        request = state.get("request", {})
        result = data_acquire.acquire(request)

        emit_trace_event(
            "data_acquired",
            {"status": result.get("status"), "rows": result.get("rows", 0)},
            state,
        )
        # Acquisition failure is data-absence, not a node error — Fusion decides
        # whether the overall run becomes an error (both-source-fail rule).
        return {"acquired_data": result, "status": AgentStatus.SUCCESS.value}

    def _extra_security_gate_input(self, state: dict) -> dict:
        """S-2 domain check: bound the data_url length / scheme early.

        The full SSRF allowlist/IP guard lives in safe_fetch (applied at fetch
        time); here we cheaply reject obviously malformed URLs before work.
        Per the hook contract this MUST NOT raise — it sets status=error and
        appends to error_log; __call__() then short-circuits before execute().
        """
        url = (state.get("request", {}) or {}).get("data_url")
        if url and (len(url) > _MAX_URL_LEN or not str(url).lower().startswith(("http://", "https://"))):
            return {
                **state,
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    *state.get("error_log", []),
                    "DataAcquireNode: data_url too long or non-http(s) scheme",
                ],
            }
        return state
