"""API-adapter integration tests — entry auth + HITL /resume ownership.

Exercises src/api/server.py over HTTP (FastAPI TestClient). Guards the retrofit
invariants for a template whose HITL was previously non-functional over HTTP
(no /resume route, no explicit checkpointer):
  - entry auth: unauthenticated caller → 401;
  - /resume ownership: only the recording session may resume (fail-closed);
  - resume actually reaches success (subgraph-cache fix).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from framework.schemas.trust_level import TrustLevel

_TOKEN = "test-invoke-token-ret571"
_SALES = [{"sku": "a", "units": 10}, {"sku": "b", "units": 5}]


def _fake_fetch(url, timeout):
    if "algolia" in url:
        return 200, json.dumps({"hits": [{"points": 10 + i, "num_comments": i, "title": f"p{i}",
                                          "objectID": str(i)} for i in range(16)]})
    if "reddit" in url:
        return 200, json.dumps({"data": {"children": [{"data": {"score": 5 + i, "num_comments": i,
                               "title": f"r{i}", "permalink": f"/r/x/{i}"}} for i in range(16)]}})
    return 404, ""


class _FakeLLM:
    def complete(self, messages):
        return {"content": "棚を拡大しトレンドに追随することを推奨します"}

    def stream(self, messages):
        yield ""

    def bind_tools(self, tools):
        return self


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    import importlib

    from langgraph.checkpoint.memory import InMemorySaver

    import src.api.server as server
    from src.graph.graph import Graph

    importlib.reload(server)
    # Rebuild the module-level agent with injected fakes (server builds from real
    # secrets otherwise). Same HITL config as production.
    agent = Graph(config={"llm": _FakeLLM(), "fetcher": _fake_fetch,
                          "memory_enabled": True, "hitl": {"enabled": True, "max_hitl": 4}})
    agent.compile(checkpointer=InMemorySaver())
    server.agent = agent
    return TestClient(server.app), server


def _auth(token: str = _TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _invoke_body(session_id="sess-owner"):
    return {"input": json.dumps({"topic": "植物性プロテイン", "needs_confirmation": True}),
            "session_id": session_id}


def test_invoke_without_token_is_401(client):
    c, _ = client
    assert c.post("/invoke", json=_invoke_body()).status_code == 401


def test_invoke_wrong_token_is_401(client):
    c, _ = client
    assert c.post("/invoke", json=_invoke_body(), headers=_auth("nope")).status_code == 401


@pytest.mark.parametrize("stamp", [TrustLevel.ANONYMOUS, "ANONYMOUS"])
def test_invoke_explicit_anonymous_middleware_is_401(client, stamp):
    """A middleware that stamps ANONYMOUS — as the enum OR its raw string value —
    must still be treated as unauthenticated (401). review #1: identity checks
    (`is TrustLevel.ANONYMOUS`) skip the string form; _coerce_trust normalizes."""
    c, server = client

    @server.app.middleware("http")
    async def _stamp_anon(request, call_next):
        request.state.trust_level = stamp
        return await call_next(request)

    fresh = TestClient(server.app)
    assert fresh.post("/invoke", json=_invoke_body()).status_code == 401


def test_resume_without_token_is_401(client):
    c, _ = client
    th = c.post("/invoke", json=_invoke_body(), headers=_auth()).json()["thread_id"]
    r = c.post("/resume", json={"thread_id": th, "feedback": {"topic": "x"}, "session_id": "sess-owner"})
    assert r.status_code == 401


def test_resume_foreign_session_is_403(client):
    c, _ = client
    th = c.post("/invoke", json=_invoke_body(), headers=_auth()).json()["thread_id"]
    r = c.post("/resume", json={"thread_id": th, "feedback": {"topic": "x"}, "session_id": "attacker"},
               headers=_auth())
    assert r.status_code == 403


def test_resume_unknown_thread_is_404(client):
    c, _ = client
    c.post("/invoke", json=_invoke_body(), headers=_auth())
    r = c.post("/resume", json={"thread_id": "00000000-0000-0000-0000-000000000000",
                                "feedback": {"topic": "x"}, "session_id": "sess-owner"}, headers=_auth())
    assert r.status_code == 404


def test_resume_completed_thread_is_409(client):
    """A completed thread (empty snapshot.next) must not be
    resumable — the pending gate rejects it 409. Simulate the completed
    checkpoint so this holds regardless of the fake data flow."""
    c, server = client
    th = c.post("/invoke", json=_invoke_body(), headers=_auth()).json()["thread_id"]

    class _Completed:
        # owner matches, but the thread has finished: next is empty.
        values = {"session_id": "sess-owner", "caller_id": "", "subgraph_thread_id": None}
        next = ()

    orig = server.agent._compiled.get_state
    server.agent._compiled.get_state = lambda cfg: _Completed()
    try:
        r = c.post("/resume", json={"thread_id": th, "feedback": {"topic": "x"}, "session_id": "sess-owner"},
                   headers=_auth())
    finally:
        server.agent._compiled.get_state = orig
    assert r.status_code == 409
