"""AgentCore Platform v1.0 — RET-C2-571 state schema."""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# object kinds). LangGraph checkpoints use msgpack serialization; non-primitive model objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or such model objects.

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Retail Category Intelligence Orchestrator state.

    Flat TypedDict (primitives + JSON-serializable only). All shared fields
    (user_input, status, session_id, node_history, error_log, hitl_*, etc.)
    are inherited from AgentState. Fields below are domain-specific.

    Pipeline (inner CategoryIntelligenceWorkflow):
        TargetScoping -> DataAcquire -> TrendResearch -> Fusion -> ReportGen
    """

    # pre_process output: normalized research request
    # {topic, target, period_days, sources, output_format}
    request: dict

    # step 1 (TargetScoping): confirmed scope; or HITL draft prior to confirmation
    scope: dict

    # step 2 (DataAcquire): normalized sales/price series, or
    # {"status": "unavailable", "reason": ...} when acquisition fails
    acquired_data: dict

    # step 3 (TrendResearch): external trend signals + citations
    # {series: [...], citations: [...], degraded_sources: [...]}
    trend_data: dict

    # step 4 (Fusion): quantitative fusion result
    # {correlations: [...], divergences: [...], inflections: [...], gaps: [...]}
    fusion_report: dict

    # step 5 (ReportGen) / post_process: final buyer decision report (ringi format)
    output: str
