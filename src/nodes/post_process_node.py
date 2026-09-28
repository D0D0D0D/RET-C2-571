"""AgentCore Platform v1.0 — RET-C2-571 post_process (outer backbone)."""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.security.pii_detector import detect_pii
from shared.utils.audit_logger import emit_trace_event


def _mask_pii(text: str) -> tuple[str, int]:
    """Mask any PII spans (email / phone / etc.) found in the final report.

    RET-C2-571 aggregates external UGC (Reddit, X, YouTube, HN) that may carry
    third-party PII; the outer S-3 gate scrubs it before delivery. Returns the
    masked text and the number of spans masked. Masking is applied right-to-left
    so earlier spans' offsets stay valid.
    """
    hits = detect_pii(text)
    if not hits:
        return text, 0
    for h in sorted(hits, key=lambda x: x.get("start", 0), reverse=True):
        start, end = h.get("start"), h.get("end")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
            text = text[:start] + f"[{h.get('type', 'pii')}-redacted]" + text[end:]
    return text, len(hits)


class PostProcessNode(FunctionNode):
    """Project the inner-graph report into the final formatted output.

    The inner CategoryIntelligenceWorkflow already produced the ringi-format
    report (mapped into `result`/`output` by the GraphNode.merge_output()).
    This node finalizes the projection. If the inner graph propagated an
    error (e.g. fusion impossible — no internal sales data), route() sends the
    state to finalize before reaching here, so post_process only runs on the
    success path; we still guard defensively.

    Cat-2 outer backbone exit: this is the last
    security layer before delivery, so it declares the S-1 trust level and adds
    an S-3 output check (PII scrub of external-source content) on top of the
    framework credential scan.
    """

    # S-1 trust gate: outer backbone exit — same verified-caller requirement as
    # the entry node; the final report is only delivered to a verified caller.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict) -> dict:
        """S-3 domain check: scrub external-source PII from the final report.

        Runs after the framework @final credential scan. Masks email/phone/etc.
        spans so third-party PII from aggregated UGC is not delivered.
        """
        for key in ("output", "formatted_output", "result"):
            val = result.get(key)
            if isinstance(val, str) and val:
                masked, _ = _mask_pii(val)
                result[key] = masked
        return result

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            # Preserve upstream error — do not fabricate output.
            return {"status": AgentStatus.ERROR.value}

        report = state.get("output") or state.get("result") or ""
        masked, n_pii = _mask_pii(report) if isinstance(report, str) else (report, 0)
        # S-4: audit the outer-backbone exit (final delivery), noting any PII scrub.
        emit_trace_event(
            "report_delivered",
            {"length": len(masked) if isinstance(masked, str) else 0, "pii_masked": n_pii},
            state,
        )
        return {
            "formatted_output": masked,
            "output": masked,
            "status": AgentStatus.SUCCESS.value,
        }
