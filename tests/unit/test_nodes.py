# RET-C2-571 — Unit Tests: nodes (execute contract, error boundary, S-2/S-3)

import inspect

from framework.schemas.agent_status import AgentStatus
from src.nodes.data_acquire_node import DataAcquireNode
from src.nodes.fusion_node import FusionNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.report_gen_node import ReportGenNode
from src.nodes.target_scoping_node import TargetScopingNode


def _status(v):
    return v.value if hasattr(v, "value") else v


class TestExecuteContract:
    """TC-: every node implements execute(self, state) — not _invoke_impl."""

    NODES = [PreProcessNode, TargetScopingNode, DataAcquireNode, FusionNode, ReportGenNode]

    def test_execute_signature(self):
        for node_cls in self.NODES:
            assert hasattr(node_cls, "execute")
            params = list(inspect.signature(node_cls.execute).parameters)
            assert params[1] == "state", f"{node_cls.__name__} second param must be 'state'"
            assert "_invoke_impl" not in node_cls.__dict__


class TestPreProcess:
    def test_empty_input_errors(self):
        r = PreProcessNode().execute({"user_input": "  "})
        assert _status(r["status"]) == AgentStatus.ERROR.value

    def test_plain_topic_builds_request(self):
        r = PreProcessNode().execute({"user_input": "植物性プロテイン", "input_context": {}})
        assert _status(r["status"]) == AgentStatus.SUCCESS.value
        assert r["request"]["topic"] == "植物性プロテイン"
        assert r["request"]["output_format"] == "ringi"

    def test_json_payload_request(self):
        payload = '{"topic": "coffee", "period_days": 60}'
        r = PreProcessNode().execute({"user_input": payload, "input_context": {}})
        assert r["request"]["topic"] == "coffee"
        assert r["request"]["period_days"] == 60


class TestTargetScoping:
    def test_clear_topic_no_hitl(self):
        r = TargetScopingNode().execute({"request": {"topic": "coffee"}})
        assert _status(r["status"]) == AgentStatus.SUCCESS.value
        assert r["scope"]["topic"] == "coffee"
        assert "hitl_draft" not in r  # no interrupt path

    def test_rebuilds_request_from_inner_user_input(self):
        import json

        raw = json.dumps({"topic": "tea", "target": "競合"})
        r = TargetScopingNode().execute({"user_input": raw})
        assert r["scope"]["topic"] == "tea"
        assert r["request"]["target"] == "競合"


class TestDataAcquireNode:
    def test_acquire_ok(self):
        state = {"request": {"inline_data": "date,sales\nd,100\nd,200"}}
        r = DataAcquireNode().execute(state)
        assert r["acquired_data"]["status"] == "ok"
        assert _status(r["status"]) == AgentStatus.SUCCESS.value

    def test_s2_gate_rejects_bad_url(self):
        # _extra_security_gate_input must set status=error, not raise
        state = {"request": {"data_url": "ftp://internal/secret"}}
        out = DataAcquireNode()._extra_security_gate_input(state)
        assert _status(out["status"]) == AgentStatus.ERROR.value

    def test_s2_gate_passes_https(self):
        state = {"request": {"data_url": "https://example.com/data.csv"}}
        out = DataAcquireNode()._extra_security_gate_input(state)
        assert out.get("status") != AgentStatus.ERROR.value


class TestFusionNodeErrorBoundary:
    """Core degradation guard: no internal sales -> error (no trend-only report)."""

    def test_no_sales_is_error(self):
        state = {
            "acquired_data": {"status": "unavailable", "reason": "x"},
            "trend_data": {"series": [1.0, 2.0, 3.0]},
        }
        r = FusionNode().execute(state)
        assert _status(r["status"]) == AgentStatus.ERROR.value
        assert "fusion impossible" in " ".join(r["error_log"])

    def test_with_sales_fuses(self):
        xs = [float(i) for i in range(16)]
        state = {"acquired_data": {"status": "ok", "sales_series": xs}, "trend_data": {"series": xs}}
        r = FusionNode().execute(state)
        assert _status(r["status"]) == AgentStatus.SUCCESS.value
        assert r["fusion_report"]["correlations"]["determinable"] is True


class TestReportGenS3:
    def test_s3_keihyo_annotation(self):
        # _extra_security_gate_output annotates risky expressions (preservation)
        result = {"output": "業界No.1です", "result": "業界No.1です"}
        out = ReportGenNode()._extra_security_gate_output(result)
        assert "景表法チェック" in out["output"]
        assert "業界No.1" in out["output"]  # not deleted

    def test_report_built_without_llm(self):
        state = {
            "scope": {"topic": "t"},
            "fusion_report": {"correlations": {"determinable": False, "reason": "x"}, "divergences": [], "inflections": [], "gaps": []},
            "trend_data": {"citations": [], "degraded_sources": ["X_API_KEY"]},
            "request": {"output_format": "ringi"},
        }
        r = ReportGenNode(llm=None).execute(state)
        assert "稟議書" in r["output"]
        assert _status(r["status"]) == AgentStatus.SUCCESS.value
