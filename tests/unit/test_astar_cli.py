"""Tests for the AGENTIC STAR Marketplace entry adapter."""

from __future__ import annotations

import json
from typing import Any

import pytest

import cli
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import SecretProvider

_AZURE_VALUES = {
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
    "AZURE_OPENAI_API_KEY": "test-api-key",
    "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
}
_PAYLOAD = json.dumps(
    {
        "topic": "冷凍食品カテゴリ",
        "inline_data": [
            {"sales": 100},
            {"sales": 120},
            {"sales": 90},
            {"sales": 150},
        ],
    },
    ensure_ascii=False,
)
_FALLBACK_MARKER = "（LLM 未注入のためコード生成サマリ）"


class _FakeLLM:
    def __init__(self) -> None:
        self.complete_calls = 0

    def complete(self, messages: list[Any]) -> dict[str, Any]:
        self.complete_calls += 1
        return {
            "content": "冷凍食品の売場と在庫配分を見直すことを推奨します。",
            "tool_calls": [],
            "model": "fake",
            "usage": {},
        }

    def stream(self, messages: list[Any]):
        yield ""

    def bind_tools(self, tools: list[Any]):
        return self


class _EmptyProvider(SecretProvider):
    def get(self, key: str, default: str | None = None) -> str | None:
        return default


def _offline_fetch(url: str, timeout: float) -> tuple[int, str]:
    del url, timeout
    return 404, ""


def _parsed_config() -> dict[str, Any]:
    return cli._load_file_config()


def _set_valid_azure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _AZURE_VALUES.items():
        monkeypatch.setenv(key, value)


def test_main_wires_lazy_llm_into_runner_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_azure_environment(monkeypatch)
    recorded: dict[str, Any] = {}

    def recorder(agent_cls, *, agent_name, namespace, config):
        recorded.update(
            agent_cls=agent_cls,
            agent_name=agent_name,
            namespace=namespace,
            config=config,
        )

    monkeypatch.setattr(cli, "run_agent_marketplace", recorder)
    cli.main()

    assert recorded["agent_cls"] is cli.MarketplaceGraph
    assert recorded["agent_name"] == "RET-C2-571"
    assert recorded["namespace"] == "ret"
    assert isinstance(recorded["config"]["llm"], cli._LazyAzureLLM)


@pytest.mark.parametrize("key", tuple(_AZURE_VALUES))
@pytest.mark.parametrize("invalid", [None, "", " padded ", "line\nbreak"])
def test_main_rejects_unclean_azure_environment_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    invalid: str | None,
) -> None:
    _set_valid_azure_environment(monkeypatch)
    if invalid is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, invalid)
    calls: list[object] = []
    monkeypatch.setattr(cli, "run_agent_marketplace", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(RuntimeError, match=key):
        cli.main()

    assert calls == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview",
        "https://example.services.ai.azure.example/openai",
        "https://example.services.ai.azure.example/v1",
        "https://example.services.ai.azure.example?api-version=2024-10-21",
        "example.services.ai.azure.example",
    ],
    ids=["api-path", "openai-path", "base-path", "query-only", "missing-scheme"],
)
def test_main_rejects_unusable_azure_endpoint_shape_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    """The client appends its own path, so anything but a bare endpoint breaks.

    The resulting error surfaces only at call time; a node that swallows it
    degrades silently while the invocation still reports status: success. So the
    adapter rejects the shape at startup instead.
    """

    _set_valid_azure_environment(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
    calls: list[object] = []
    monkeypatch.setattr(cli, "run_agent_marketplace", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ValueError, match="bare"):
        cli.main()

    assert calls == []


def test_runner_order_succeeds_uses_llm_and_discards_runner_context() -> None:
    fake_llm = _FakeLLM()
    config = _parsed_config()
    config.update({"llm": fake_llm, "fetcher": _offline_fetch})
    graph = cli.MarketplaceGraph(config=config)

    graph.compile()
    graph.provision_secrets(_EmptyProvider())
    result = graph.invoke(
        _PAYLOAD,
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={
            "conversation_history": [
                {"role": "user", "content": "CANARY_XYZ"},
            ]
        },
    )

    assert result["status"] == "success"
    assert isinstance(result["output"], str) and result["output"]
    assert fake_llm.complete_calls >= 1
    assert _FALLBACK_MARKER not in result["output"]
    assert "CANARY_XYZ" not in json.dumps(result, ensure_ascii=False, default=str)


def test_invoke_rebuilds_input_context_from_scratch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def parent_invoke(self, user_input, session_id="", ctx=None, input_context=None):
        del self, user_input, session_id, ctx
        captured["input_context"] = input_context
        return {"status": "success", "output": "ok"}

    monkeypatch.setattr(cli.CategoryIntelligenceOrchestratorAgent, "invoke", parent_invoke)
    graph = cli.MarketplaceGraph(config=_parsed_config())
    result = graph.invoke(
        _PAYLOAD,
        input_context={
            "conversation_history": [{"role": "user", "content": "CANARY_XYZ"}]
        },
    )

    assert captured["input_context"] == {}
    assert "CANARY_XYZ" not in json.dumps(result, ensure_ascii=False)


def test_graph_injects_lazy_llm_when_runner_config_has_none() -> None:
    config = _parsed_config()
    config["llm"] = None

    graph = cli.MarketplaceGraph(config=config)

    assert isinstance(graph.config["llm"], cli._LazyAzureLLM)


def test_graph_preserves_caller_injected_llm() -> None:
    sentinel = object()
    config = _parsed_config()
    config["llm"] = sentinel

    graph = cli.MarketplaceGraph(config=config)

    assert graph.config["llm"] is sentinel


def test_lazy_llm_stores_no_connection_values() -> None:
    assert vars(cli._LazyAzureLLM()) == {}


def test_lazy_llm_bind_tools_returns_new_instance() -> None:
    llm = cli._LazyAzureLLM()

    bound = llm.bind_tools([{"type": "function"}])

    assert isinstance(bound, cli._LazyAzureLLM)
    assert bound is not llm
