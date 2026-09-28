# PB (outer backbone): Cat-2 outer PreProcess/PostProcess security gates
#
# For a Cat-2 template the OUTER backbone
# nodes are the first and last security layer around the inner graph. This
# proof-of-boundary suite verifies the outer nodes actually enforce:
#
#   PreProcessNode  — S-1 required_trust_level=VERIFIED_EXTERNAL,
#                     S-2 _extra_security_gate_input() bounds raw user_input.
#   PostProcessNode — S-1 required_trust_level=VERIFIED_EXTERNAL,
#                     S-3 _extra_security_gate_output() scrubs external-source PII.
#
# These are framework-boundary assertions on the outer backbone, exercised at
# the node level (and via the full graph for the trust gate), not business logic.

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode, _mask_pii
from src.nodes.pre_process_node import _MAX_INPUT_LEN, PreProcessNode


def _val(v):
    return v.value if hasattr(v, "value") else v


class TestOuterBackboneTrustLevel:
    def test_both_outer_nodes_require_verified_external(self):
        # S-1: the outer entry and exit nodes must not be left at the permissive
        # ANONYMOUS default — they gate the inner graph.
        assert PreProcessNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
        assert PostProcessNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL


class TestPreProcessInputGate:
    def test_oversized_input_rejected_before_execute(self):
        # S-2: _extra_security_gate_input must flag an oversized payload with
        # status=error and MUST NOT raise (hook contract).
        node = PreProcessNode()
        big = "x" * (_MAX_INPUT_LEN + 1)
        out = node._extra_security_gate_input({"user_input": big})
        assert _val(out.get("status")) == "error"
        assert any("exceeds" in e for e in out.get("error_log", []))

    def test_normal_input_passes_gate_unchanged(self):
        node = PreProcessNode()
        state = {"user_input": "coffee"}
        out = node._extra_security_gate_input(state)
        # No status forced to error for a normal payload.
        assert _val(out.get("status")) != "error"


class TestPostProcessOutputGate:
    def test_pii_is_masked_in_final_output(self):
        # S-3: emails / phone numbers from aggregated external UGC must be
        # scrubbed from the delivered report.
        report = "推奨: 棚拡大。出典コメント: contact me at jane@example.com / 090-1234-5678"
        result = {"output": report, "formatted_output": report}
        node = PostProcessNode()
        out = node._extra_security_gate_output(result)
        for key in ("output", "formatted_output"):
            assert "jane@example.com" not in out[key], out[key]
            assert "090-1234-5678" not in out[key], out[key]
            assert "redacted" in out[key]

    def test_clean_output_untouched(self):
        clean = "稟議書: coffee市場は好調。棚拡大を推奨します。"
        out = PostProcessNode()._extra_security_gate_output({"output": clean})
        assert out["output"] == clean

    def test_execute_masks_and_reports_pii_count(self):
        report = "出典: bob@example.com からのコメント"
        out = PostProcessNode().execute({"status": "success", "output": report})
        assert "bob@example.com" not in _val(out["output"])
        assert _val(out["status"]) == "success"

    def test_execute_preserves_upstream_error(self):
        out = PostProcessNode().execute({"status": AgentStatus.ERROR.value})
        assert _val(out["status"]) == "error"


class TestMaskHelper:
    def test_mask_pii_counts_and_offsets(self):
        text = "user@example.com and other@example.com"
        masked, n = _mask_pii(text)
        assert n == 2
        assert "@" not in masked
