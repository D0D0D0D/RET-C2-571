# PB-7: HITL interrupt propagation
#
# When TargetScopingNode meets an ambiguous scope it calls interrupt(). Run
# through the compiled graph (with a checkpointer), the call must surface as
# an interrupt — the run pauses (awaiting_human) rather than ending in
# status=error. interrupt() called outside a runnable context raises a plain
# RuntimeError, so this boundary MUST be exercised via the graph, not by
# calling node.execute() directly.

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import Graph


def _status(v):
    return v.value if hasattr(v, "value") else v


def _ctx(session):
    # Outer backbone nodes require VERIFIED_EXTERNAL (S-1, SEC-RET-C2-571-R1-01),
    # so HITL propagation must be exercised as a verified caller.
    return InvocationContext(session_id=session, caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _fake_fetch(url, timeout):
    return 200, json.dumps({"hits": []}) if "algolia" in url else (200, json.dumps({"data": {"children": []}}))


class _FakeLLM:
    def complete(self, messages):
        return {"content": "x"}

    def stream(self, messages):
        yield ""

    def bind_tools(self, tools):
        return self


def _graph():
    # hitl/memory_enabled must be set so BOTH the outer graph and the inner
    # graph attach a checkpointer (propagate_hitl=True surfaces the inner
    # interrupt to the outer graph, which then also needs to pause/resume).
    g = Graph(
        config={
            "llm": _FakeLLM(),
            "fetcher": _fake_fetch,
            "memory_enabled": True,
            "hitl": {"enabled": True, "max_hitl": 4},
        }
    )
    g.compile()
    return g


class TestHitlInterruptPropagation:
    def test_ambiguous_scope_pauses_not_errors(self):
        # A topic that explicitly requests confirmation -> TargetScopingNode
        # calls interrupt() inside the inner graph. (Empty input is rejected by
        # pre_process before reaching TargetScoping, so we use a real topic with
        # needs_confirmation set.)
        payload = json.dumps({"topic": "植物性プロテイン", "needs_confirmation": True})
        out = _graph().invoke(payload, ctx=_ctx("pb7-ambiguous"))
        status = _status(out.get("status"))
        # The interrupt must NOT degrade into an application error.
        assert status != "error", f"interrupt must not become error, got {out!r}"
        # It should signal awaiting-human (pause) or carry interrupt metadata.
        assert (
            status == "awaiting_human"
            or out.get("__interrupt__")
            or out.get("interrupt")
            or status == "pending"
        ), f"expected an awaiting-human pause, got status={status}"

    def test_clear_scope_runs_to_completion(self):
        # A clear topic must NOT pause; it proceeds (and then errors only
        # because no sales data was supplied — that is the fusion guard,
        # not a HITL pause).
        out = _graph().invoke("coffee", ctx=_ctx("pb7-clear"))
        status = _status(out.get("status"))
        assert status != "awaiting_human"
