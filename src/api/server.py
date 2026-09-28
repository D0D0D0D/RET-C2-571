"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

from typing import Any
import threading
import weakref
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.entry_adapter.auth import _require_verified_trust, _resolve_trust
from src.graph.graph import CategoryIntelligenceOrchestratorAgent


def _load_runtime_config() -> dict:
    """Read HITL/memory settings from config/config.yaml so the graph (and its
    inner workflow) attach a checkpointer. TargetScopingNode uses interrupt();
    without these keys the compiled graph cannot pause/resume.
    """
    import os

    cfg: dict = {"memory_enabled": True, "hitl": {"enabled": True, "max_hitl": 4}}
    path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml")
    try:
        import yaml  # provided by the framework runtime

        with open(path, encoding="utf-8") as f:
            runtime = yaml.safe_load(f) or {}
        if "memory_enabled" in runtime:
            cfg["memory_enabled"] = runtime["memory_enabled"]
        if "hitl" in runtime:
            cfg["hitl"] = runtime["hitl"]
    except Exception:  # noqa: BLE001 — fall back to the HITL-on defaults above
        pass
    return cfg


app = FastAPI(title="Agent")

agent = CategoryIntelligenceOrchestratorAgent(config=_load_runtime_config())
# EXPLICIT checkpointer — the 1.0.0 wheel does NOT auto-derive one, so without
# this the outer graph cannot suspend/resume the TargetScopingNode interrupt()
# (HITL resume over HTTP would target a non-persisted thread).
agent.compile(checkpointer=InMemorySaver())
# Replace namespace/agent_name to match the agent's manifest values.
agent.provision_secrets(secrets_factory(namespace="ret", agent_name="ret_c2_571"))


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


class ResumeRequest(BaseModel):
    thread_id: str
    feedback: Any
    session_id: str = ""


def _authenticated_ctx(request: Request, session_id: str) -> InvocationContext:
    """Build the InvocationContext AFTER entry authentication.

    Entry auth resolves verified middleware trust as-is; INVOKE_AUTH_TOKEN Bearer auth
    promotes ANONYMOUS → VERIFIED_EXTERNAL (401 on mismatch). An unauthenticated
    caller (still ANONYMOUS, no token promotion) → 401 — replacing the old
    fallback that let ANONYMOUS callers reach the graph where S-1 then rejected
    them. This template has NO tenant/org scope model, so the entry-adapter
    scope layer is intentionally not wired (only the auth core).
    """
    trust, token_authenticated = _resolve_trust(request)
    if trust is TrustLevel.ANONYMOUS and not token_authenticated:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    _require_verified_trust(trust)
    return InvocationContext(
        session_id=session_id or str(uuid4()),
        caller_trust_level=trust,
        caller_id=getattr(request.state, "caller_id", ""),
    )


def _require_resume_ownership(thread_id: str, ctx: InvocationContext) -> None:
    """Fail-closed checkpoint ownership check for HITL resume.

    Any authenticated caller could replay another session's thread_id; verify
    the suspended checkpoint exists (404 if not) and its recorded session_id
    matches (403 on mismatch, and a checkpoint with no session_id is
    un-resumable). session_id is the primary owner key (token callers share
    caller_id=""); caller_id is defense-in-depth when the checkpoint recorded one.
    """
    compiled = getattr(agent, "_compiled", None)
    if compiled is None:
        raise HTTPException(status_code=409, detail="No suspended session for this thread.")
    snapshot = compiled.get_state({"configurable": {"thread_id": thread_id}})
    values = getattr(snapshot, "values", None) or {}
    if not values:
        raise HTTPException(status_code=404, detail="No suspended session for this thread.")
    owner_session = values.get("session_id")
    if not owner_session or owner_session != ctx.session_id:
        raise HTTPException(status_code=403, detail="Session does not own this thread.")
    owner_caller = values.get("caller_id") or ""
    if owner_caller and owner_caller != (ctx.caller_id or ""):
        raise HTTPException(status_code=403, detail="Caller does not own this thread.")
    # Pending-HITL gate (replay protection): a completed thread has an empty
    # snapshot.next, so a resume replayed after completion is rejected 409.
    # propagate_hitl=True → the outer pending checkpoint carries subgraph_thread_id.
    if not getattr(snapshot, "next", None) or not values.get("subgraph_thread_id"):
        raise HTTPException(status_code=409, detail="Thread is not awaiting human input.")


# Per-thread locks serialize the ownership-check + resume for one thread within
# this process (resume TOCTOU). A durable, multi-process deployment
# must use a per-thread lease/CAS instead.
_RESUME_LOCKS: "weakref.WeakValueDictionary[str, threading.Lock]" = weakref.WeakValueDictionary()
_RESUME_LOCKS_GUARD = threading.Lock()


def _resume_lock(thread_id: str) -> threading.Lock:
    with _RESUME_LOCKS_GUARD:
        lock = _RESUME_LOCKS.get(thread_id)
        if lock is None:
            lock = threading.Lock()
            _RESUME_LOCKS[thread_id] = lock
        return lock


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request):
    ctx = _authenticated_ctx(request, req.session_id)
    with bound_secrets(agent._secrets_provider):
        return agent.invoke(req.input, ctx=ctx)


@app.post("/resume")
async def resume(req: ResumeRequest, request: Request):
    ctx = _authenticated_ctx(request, req.session_id)
    # Serialize check + resume per thread (resume TOCTOU).
    with _resume_lock(req.thread_id):
        _require_resume_ownership(req.thread_id, ctx)
        with bound_secrets(agent._secrets_provider):
            return agent.resume(thread_id=req.thread_id, feedback=req.feedback)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "Retail Category Intelligence Orchestrator"}
