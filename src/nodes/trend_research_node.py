"""AgentCore Platform v1.0 — RET-C2-571 TrendResearchNode (inner step 3).

Collects 30-day external trend signals. Free tier (HN / Reddit public /
Polymarket public) runs with no key and is always attempted. Paid tier
(X / Perplexity) is optional: it is used only when a secrets accessor is
injected and returns a key; otherwise the source is recorded in
trend_data.degraded_sources[] (a normal partial run, not an error).

Injection seams (constructor):
  - secrets_get: callable(key, default) -> str | None  (e.g. ctx.secrets.get)
  - fetcher:     callable(url, timeout) -> (status, body)  (offline tests)
Both default to None; with no secrets_get every paid source degrades, with
no fetcher the real urllib egress (S-2 guarded) is used.
"""

from __future__ import annotations

from typing import Callable, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services import trend_sources


class TrendResearchNode(FunctionNode):
    """Gather external trend signals (free tier + optional paid tier)."""

    # S-1 trust gate: performs outbound HTTP to external
    # trend sources (and optional paid APIs via injected secrets) — a privileged
    # op requiring a verified caller. Enforced per-node by BaseNode.__call__.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(
        self,
        secrets_get: Callable[[str, str | None], str | None] | None = None,
        fetcher: Callable[[str, float], tuple[int, str]] | None = None,
    ) -> None:
        super().__init__()
        self._secrets_get = secrets_get
        self._fetcher = fetcher

    def execute(self, state: dict) -> dict:
        scope = state.get("scope", {})
        topic = (scope.get("topic") or "").strip()

        free = trend_sources.collect_free_tier(topic, fetcher=self._fetcher)

        # Optional paid tier — never raises when keys are missing.
        # Prefer an injected getter (tests); otherwise read the process-bound
        # SecretProvider (current_secrets() returns NullProvider when unbound),
        # so production reads ctx.secrets without threading it through State.
        getter = self._secrets_get or self._bound_secrets_get
        paid = trend_sources.resolve_paid_tier(getter)

        degraded = list(paid["degraded_sources"]) + list(free.get("failed_free_sources", []))

        trend_data = {
            "series": free["series"],
            "citations": free["citations"],
            "sources_used": free["sources_used"],
            "paid_active": paid["active"],
            "degraded_sources": degraded,
        }
        # No trend at all is data-absence (a gap), not an error here — Fusion
        # decides the overall outcome based on whether internal sales exist.
        if not free["series"]:
            trend_data["status"] = "unavailable"

        emit_trace_event(
            "trend_researched",
            {
                "sources_used": free["sources_used"],
                "paid_active": paid["active"],
                "points": len(free["series"]),
            },
            state,
        )
        return {"trend_data": trend_data, "status": AgentStatus.SUCCESS.value}

    @staticmethod
    def _bound_secrets_get(key: str, default: str | None = None) -> str | None:
        """Read an optional secret from the process-bound SecretProvider.

        Uses current_secrets() (NullProvider when unbound), so a missing key
        returns the default instead of raising — paid tier stays optional.
        """
        from framework.secrets.context import current_secrets

        try:
            return current_secrets().get(key, default)
        except Exception:  # noqa: BLE001 — optional secret must never break the run
            return default
