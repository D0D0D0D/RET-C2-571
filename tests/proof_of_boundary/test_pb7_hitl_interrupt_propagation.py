# tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py
#
# PB-7: HITL Interrupt Propagation — exact-name file required by the
# scaffold-integrity check for HITL-enabled templates.
#
# This template enables HITL (TargetScopingNode calls interrupt() on ambiguous
# scope), so the assertions below run for real. In addition to the suspend check
# in test_hitl_interrupt.py, this file verifies:
#   (a) interrupt() surfaces as awaiting_human, not error;
#   (b) resume() reaches a terminal (non-awaiting) state — catches the lost-
#       checkpoint reinterrupt loop;
#   (c) the hitl_allowed=False guard skips the interrupt (no deadlock).

from __future__ import annotations

import json
import pathlib
import warnings

import pytest

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import Graph

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _hitl_enabled() -> bool:
    """Return True when config/config.yaml declares hitl.enabled: true.

    An absent or unreadable config.yaml warns instead of skipping silently —
    either case is ambiguous (never shipped / broken
    vs. genuinely non-HITL) and should not look like a clean auto-waiver.
    """
    if not _CONFIG_PATH.exists():
        warnings.warn(
            f"{_CONFIG_PATH} not found — PB-7 skipped without verifying hitl.enabled. "
            "If this template calls interrupt(), ship config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    try:
        import yaml  # pyyaml==6.0.1 — pinned in scaffold/pyproject.toml dependencies

        data = yaml.safe_load(_CONFIG_PATH.read_text())
    except Exception as exc:
        warnings.warn(
            f"{_CONFIG_PATH} could not be read as YAML ({exc}) — PB-7 skipped without "
            "verifying hitl.enabled. If this template calls interrupt(), fix config/config.yaml "
            "before release.",
            stacklevel=2,
        )
        return False
    hitl = (data or {}).get("hitl", {}) if isinstance(data, dict) else None
    if not isinstance(hitl, dict):
        warnings.warn(
            f"{_CONFIG_PATH} does not have the expected 'hitl:' mapping shape — PB-7 skipped "
            "without verifying hitl.enabled. If this template calls interrupt(), fix "
            "config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    return bool(hitl.get("enabled", False))


pytestmark = pytest.mark.skipif(
    not _hitl_enabled(),
    reason="config/config.yaml does not set hitl.enabled: true — PB-7 not applicable",
)


def _status(v):
    return v.value if hasattr(v, "value") else v


def _fake_fetch(url, timeout):
    # Mirror tests/integration/test_end_to_end.py — the downstream fusion/report
    # nodes need enough data points to produce a non-empty result on resume.
    if "algolia" in url:
        return 200, json.dumps(
            {"hits": [{"points": 10 + i, "num_comments": i, "title": f"p{i}", "objectID": str(i)}
                      for i in range(16)]}
        )
    if "reddit" in url:
        return 200, json.dumps(
            {"data": {"children": [{"data": {"score": 5 + i, "num_comments": i, "title": f"r{i}",
                                             "permalink": f"/r/x/{i}"}} for i in range(16)]}}
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
    g = Graph(config={
        "llm": _FakeLLM(),
        "fetcher": _fake_fetch,
        "memory_enabled": True,
        "hitl": {"enabled": True, "max_hitl": 4},
    })
    from langgraph.checkpoint.memory import InMemorySaver

    g.compile(checkpointer=InMemorySaver())
    return g


def _ctx(session):
    return InvocationContext(session_id=session, caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def test_pb7_interrupt_surfaces_as_awaiting_human_not_error():
    payload = json.dumps({"topic": "植物性プロテイン", "needs_confirmation": True})
    out = _graph().invoke(payload, ctx=_ctx("pb7-suspend"),
                          input_context={"inline_data": [{"sku": "a", "units": 1}]})
    assert _status(out.get("status")) != "error", f"interrupt must not become error: {out!r}"
    assert _status(out.get("status")) == "awaiting_human" or out.get("__interrupt__")


def test_pb7_resume_reaches_success():
    """resume() must reach SUCCESS — catches the fresh-subgraph-per-call bug
    where get_subgraph() rebuilt the inner graph (with an empty checkpointer) on
    resume and lost the suspend checkpoint, re-interrupting forever / erroring.
    Fixed by caching the compiled inner graph with its own checkpointer."""
    g = _graph()
    sales = [{"sku": "a", "units": 10}, {"sku": "b", "units": 5}]
    payload = json.dumps({"topic": "植物性プロテイン", "needs_confirmation": True})
    out = g.invoke(payload, ctx=_ctx("pb7-resume"), input_context={"inline_data": sales})
    assert _status(out.get("status")) == "awaiting_human"
    final = g.resume(thread_id=out["thread_id"], feedback={"topic": "植物性プロテイン飲料"})
    assert _status(final.get("status")) == "success", f"resume must reach success: {final!r}"
    assert final.get("output")


def test_pb7_hitl_allowed_false_skips_interrupt():
    """hitl_allowed=False must skip interrupt() and proceed — no deadlock."""
    from src.nodes.target_scoping_node import TargetScopingNode

    node = TargetScopingNode()
    state = {
        "request": {"topic": "", "needs_confirmation": True},
        "hitl_allowed": False,
    }
    out = node.execute(state)
    assert _status(out.get("status")) == "success"
    assert "scope" in out
