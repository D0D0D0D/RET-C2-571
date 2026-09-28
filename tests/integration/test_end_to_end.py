# RET-C2-571 — Integration Tests: full graph invoke (free-tier, offline)

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import Graph


def _fake_fetch(url, timeout):
    if "algolia" in url:
        return 200, json.dumps(
            {"hits": [{"points": 10 + i, "num_comments": i, "title": f"p{i}", "objectID": str(i)} for i in range(16)]}
        )
    if "reddit" in url:
        return 200, json.dumps(
            {"data": {"children": [{"data": {"score": 5 + i, "num_comments": i, "title": f"r{i}", "permalink": f"/r/x/{i}"}} for i in range(16)]}}
        )
    return 404, ""


class _FakeLLM:
    def complete(self, messages):
        return {"content": "棚を拡大しトレンドに追随することを推奨します"}

    def stream(self, messages):
        yield ""

    def bind_tools(self, tools):
        return self


def _graph():
    g = Graph(config={"llm": _FakeLLM(), "fetcher": _fake_fetch})
    g.compile()
    return g


def _ctx(session="e2e"):
    # The outer backbone entry/exit (PreProcessNode / PostProcessNode) and the
    # privileged inner nodes (data acquire / trend research / report gen) all
    # declare required_trust_level=VERIFIED_EXTERNAL (S-1), so a full-run
    # integration test must invoke as a verified caller.
    return InvocationContext(session_id=session, caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _status(v):
    return v.value if hasattr(v, "value") else v


def _report(out):
    return out.get("output") or out.get("formatted_output") or ""


class TestEndToEnd:
    def test_free_tier_full_run_produces_ringi_report(self):
        sales = "date,sales\n" + "\n".join(f"2026-01-{i + 1:02d},{100 + i * 3}" for i in range(16))
        out = _graph().invoke("植物性プロテイン", ctx=_ctx("e2e-ringi"), input_context={"inline_data": sales})
        assert _status(out["status"]) == "success"
        rep = _report(out)
        assert "稟議書" in rep
        assert "AI が生成" in rep            # S-5 disclaimer
        assert "棚を拡大" in rep             # LLM narrative
        assert "X_API_KEY" in rep            # paid-tier degraded, surfaced

    def test_node_history_runs_full_backbone(self):
        sales = "date,sales\n" + "\n".join(f"d,{100 + i}" for i in range(16))
        out = _graph().invoke("coffee", ctx=_ctx("e2e-hist"), input_context={"inline_data": sales})
        hist = out.get("node_history", [])
        assert "InitializeNode" in hist
        assert "CategoryIntelligenceGraphNode" in hist
        assert "PostProcessNode" in hist
        assert "FinalizeNode" in hist

    def test_no_sales_data_errors_no_trend_only_report(self):
        # Degradation guard at the graph level: trend exists but no sales.
        # Verified caller so the error is the fusion guard, not the S-1 gate.
        out = _graph().invoke("植物性プロテイン", ctx=_ctx("e2e-nosales"), input_context={})
        assert _status(out["status"]) == "error"
        assert "稟議書" not in _report(out)
