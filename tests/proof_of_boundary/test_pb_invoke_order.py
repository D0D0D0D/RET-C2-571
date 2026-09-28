# PB-6: invoke execution order + S-1 trust gate contract
#
# PB-6 requires the framework execution order to hold on every
# node invocation:
#
#     S-1 trust gate → S-4 node_start → S-2 input gate → execute()
#     → S-3 output gate → S-4 node_complete
#
# That order is owned by framework's BaseNode.__call__ (never overridden here),
# so we verify the two observable contracts this template depends on:
#
#   1. ORDER — for each node, the framework's `node_start` audit event fires
#      BEFORE any domain `emit_trace_event()` call made inside execute(), and a
#      terminal `node_complete` follows. (node_start is emitted after S-1/before
#      execute; domain events come from inside execute.) We capture the event
#      stream via the `agentcore.audit` logger that emit_trace_event writes to.
#
#   2. S-1 TRUST GATE — a caller below the declared required_trust_level
#      (VERIFIED_EXTERNAL, set on CategoryIntelligenceOrchestratorAgent)
#      is denied: status=error with an "S-1 trust gate
#      denied" entry, and execute() is never reached (no domain event fires).
#
# These are framework-boundary assertions, not business-logic tests, so they
# live in proof_of_boundary/.

import json
import logging

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import CategoryIntelligenceOrchestratorAgent
from typing import ClassVar
from framework.nodes.base_node import BaseNode


def _fake_fetch(url, timeout):
    if "algolia" in url:
        return 200, json.dumps({"hits": []})
    return 200, json.dumps({"data": {"children": []}})


class _FakeLLM:
    def complete(self, messages):
        return {"content": "x"}

    def stream(self, messages):
        yield ""

    def bind_tools(self, tools):
        return self


class _AuditCapture(logging.Handler):
    """Capture audit records emitted to the agentcore.audit logger."""

    def __init__(self):
        super().__init__()
        self.events: list[str] = []      # event_type stream (ordered)
        self.records: list[dict] = []    # full decoded records (event_type + payload)

    def emit(self, record):
        try:
            data = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return
        et = data.get("event_type")
        if et:
            self.events.append(et)
            self.records.append(data)


def _graph():
    return CategoryIntelligenceOrchestratorAgent(
        config={
            "llm": _FakeLLM(),
            "fetcher": _fake_fetch,
            "memory_enabled": True,
            "hitl": {"enabled": True, "max_hitl": 4},
        }
    )


def _ctx(trust: TrustLevel, session: str) -> InvocationContext:
    return InvocationContext(session_id=session, caller_trust_level=trust)


# ── PB-6 S-1 negative boundary（2026-08-17 正本追従で移植）──────────────
# 正本 tests/proof_of_boundary/test_pb_invoke_order.py より。テンプレのノード構成に
# 依存しない常設フィクスチャで「不足 trust では execute() に到達しない」ことを証明する
# （TC-08 の要求。既存の全ノード走査テストは触っていない）。


class _PrivilegedTrustGateFixture(BaseNode):
    """Always-present privileged node used to prove the S-1 negative boundary."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _security_gate_input(self, state):
        return state

    def execute(self, state):
        return {"status": "success"}

    def _security_gate_output(self, result):
        return result


def _trust_predecessor(required: TrustLevel) -> TrustLevel:
    """Return a lower valid trust level; fail loudly if the framework adds one."""
    predecessors = {
        TrustLevel.VERIFIED_EXTERNAL: TrustLevel.ANONYMOUS,
        TrustLevel.INTERNAL: TrustLevel.VERIFIED_EXTERNAL,
    }
    try:
        return predecessors[required]
    except KeyError as exc:
        raise AssertionError(f"no lower trust level defined for {required!r}") from exc


class TestInvokeOrder:
    def test_node_start_precedes_domain_events_and_completes(self):
        cap = _AuditCapture()
        logger = logging.getLogger("agentcore.audit")
        prev_level = logger.level
        logger.addHandler(cap)
        logger.setLevel(logging.INFO)
        try:
            g = _graph()
            g.compile()
            # A clear topic with no sales data runs the pipeline to the fusion
            # guard; that is enough to exercise the node __call__ order.
            g.invoke(
                "coffee",
                ctx=_ctx(TrustLevel.VERIFIED_EXTERNAL, "pb6-order"),
            )
        finally:
            logger.removeHandler(cap)
            logger.setLevel(prev_level)

        # The framework MUST have emitted at least one node_start / node_complete.
        assert "node_start" in cap.events, f"no node_start emitted: {cap.events}"
        assert "node_complete" in cap.events, f"no node_complete emitted: {cap.events}"

        # ORDER: the first audit event of the whole run is a node_start — i.e.
        # the framework's S-4 node_start fires before any domain emit_trace_event
        # made inside execute(). A domain event appearing before the first
        # node_start would mean execute() ran before the framework boundary.
        first_start = cap.events.index("node_start")
        domain_before_start = [
            e for e in cap.events[:first_start]
            if e not in ("node_start", "node_complete", "node_error", "s1_denied")
        ]
        assert not domain_before_start, (
            f"domain event(s) fired before the first node_start: "
            f"{domain_before_start} in {cap.events}"
        )

    def test_s1_trust_gate_denies_insufficient_caller(self):
        cap = _AuditCapture()
        logger = logging.getLogger("agentcore.audit")
        prev_level = logger.level
        logger.addHandler(cap)
        logger.setLevel(logging.INFO)
        try:
            g = _graph()
            g.compile()
            out = g.invoke(
                "coffee",
                ctx=_ctx(TrustLevel.ANONYMOUS, "pb6-trust-denied"),
            )
        finally:
            logger.removeHandler(cap)
            logger.setLevel(prev_level)

        status = out.get("status")
        status = status.value if hasattr(status, "value") else status
        assert status == "error", f"insufficient trust must yield error, got {out!r}"

        # S-1 denial happens BEFORE execute(): the framework emits an s1_denied
        # audit event (per privileged node) and never reaches the node's domain
        # events — this is the framework-level proof of the gate firing. The
        # privileged nodes run inside the inner subgraph, so the denial message
        # is carried by the s1_denied audit record, not the outer error_log.
        assert "s1_denied" in self._event_types(cap), (
            f"expected an s1_denied audit event: {cap.events}"
        )
        denied = [e for e in cap.records if e.get("event_type") == "s1_denied"]
        assert any(
            e["payload"].get("required") == "VERIFIED_EXTERNAL"
            and e["payload"].get("caller") == "ANONYMOUS"
            for e in denied
        ), f"s1_denied payload should show required>caller mismatch: {denied}"

        # The privileged nodes must NOT have produced their domain events
        # (execute never ran behind the gate).
        assert "data_acquired" not in self._event_types(cap), (
            f"DataAcquireNode.execute ran despite S-1 denial: {cap.events}"
        )

    @staticmethod
    def _event_types(cap):
        return [e.get("event_type") for e in cap.records]

    def test_s1_denial_refuses_execution_before_execute(self, monkeypatch):
            """TC-08: an always-present privileged node proves the negative S-1 path."""
            import framework.nodes.base_node as base_node_module

            events: list[str] = []
            execute_calls: list[object] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state: events.append(event_type),
            )
            original_execute = _PrivilegedTrustGateFixture.execute

            def spy_execute(self, state):
                execute_calls.append(state)
                return original_execute(self, state)

            monkeypatch.setattr(_PrivilegedTrustGateFixture, "execute", spy_execute)
            result = _PrivilegedTrustGateFixture()(
                {
                    "caller_trust_level": _trust_predecessor(
                        _PrivilegedTrustGateFixture.required_trust_level
                    ).value,
                    "correlation_id": "tc08-s1-denial",
                }
            )

            assert result["status"] == "error"
            assert "S-1 trust gate denied" in result["error_log"][0]
            assert events == ["s1_denied"]
            assert not execute_calls
