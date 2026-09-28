# Test Specification — RET-C2-571 Retail Category Intelligence Orchestrator

## Test Strategy
- Test types: Unit / Integration / Proof-of-Boundary（すべてオフライン実行・外部ネットワーク不要）
- 注入シーム（`llm` / `fetcher` / `secrets_get`）でフレーム外依存を遮断し、決定論的に実行
- 退化防止（Agent vs Tool）の核 = FusionNode 定量4経路 + 「実績データなし→error」を直接アサート

## Framework Compliance Tests

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict（`State(AgentState)`、domain は dict/str/list のみ） | Pydantic/dataclass なし | PASS (PB-2 / test_state_safety) |
| TC-02 | SecurityViolationError fires（safe_fetch SSRF / scheme） | Error raised | PASS (test_services::TestSafeFetchSSRF) |
| TC-03 | No JWT/Credential in State（secrets は `ctx.secrets.get()`、State 非格納） | CI `gate-credential-scan` 0 violations | PASS (CI) |
| TC-04 | InvocationContext via configurable only | 直接格納なし | PASS (PB-2) |
| TC-05 | S-4: lifecycle イベントを execute() で重複させない | `node_start/complete/error` を execute 内で emit しない | PASS (domain event のみ emit) |
| TC-06 | S-2: `_security_gate_input()` を override しない（`@final`） | 直接 override なし（`_extra_*` のみ） | PASS (DataAcquireNode は `_extra_security_gate_input`) |
| TC-07 | S-3: `_security_gate_output()` を override しない（`@final`） | 直接 override なし（`_extra_*` のみ） | PASS (ReportGenNode は `_extra_security_gate_output`) |
| TC-08 | `required_trust_level`（agent.yaml: VERIFIED_EXTERNAL） | 不足 trust は拒否 | PASS (framework `__call__` S-1) |
| TC-09 | S-2: `_extra_security_gate_input()` 非自明（URL scheme/長さ検査） | domain check 実行 | PASS (test_nodes::test_s2_gate_rejects_bad_url) |
| TC-10 | S-3: `_extra_security_gate_output()` 非自明（景表法 annotate・保全） | domain check 実行 | PASS (test_nodes::test_s3_keihyo_annotation) |
| TC-11 | S-4: 各 execute() で domain `emit_trace_event()` 1回以上 | 各ノードで emit | PASS (全 step ノードで emit_trace_event) |

## Proof-of-Boundary Tests

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-2 | State serialization | Post-invoke State は primitives のみ | Pydantic/dataclass なし | PASS (test_state_safety) |
| PB-4 | Import isolation | Level 0（agenticstar）import なし | AST scan 0 | PASS (test_import_isolation) |
| PB-7 | HITL interrupt 伝播 | TargetScoping の `interrupt()` が GraphInterrupt として伝播し、`status` が error にならず pause | awaiting-human / 非 error | PASS (test_hitl_interrupt) |

## Business Logic Tests（退化防止の核）

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Fusion 1相関 | 完全相関16点 | coef≈1.0, lag=0 | PASS (test_fusion) |
| BL-02 | Fusion 1相関サンプル過少 | 10点 | determinable=False（判定不可） | PASS (test_fusion) |
| BL-03 | Fusion 2乖離 | 外部急騰×売上横ばい | opportunity_loss 検出 | PASS (test_fusion) |
| BL-04 | Fusion 3転換点 | 上昇→下降系列 | inflection 検出 | PASS (test_fusion) |
| BL-05 | Fusion 4データ不足 | 外部なし | gaps に明示・推測補完なし | PASS (test_fusion) |
| BL-06 | error 境界 | 実績データなし・外部あり | status=error、稟議書を返さない | PASS (test_nodes / test_end_to_end) |
| BL-07 | 無料層 end-to-end | HN/Reddit fake + 売上CSV | 稟議書・disclaimer・引用・縮退明示 | PASS (test_end_to_end) |
| BL-08 | 有料層 optional | secrets get=None | degraded_sources に X/Perplexity、error にならない | PASS (test_services / test_end_to_end) |
| BL-09 | data_acquire 異常入力 | malformed CSV/null | unavailable（crash しない） | PASS (test_services) |

## Test Execution Summary
- 実行コマンド: `python -m pytest tests/ -v`
- Total tests: 48
- Pass: 48 / Fail: 0 / Skip: 0
- 外部ネットワーク: 不要（全 fetch を注入 fake で遮断）

## 実 LLM 1 回転検証

モックだけで固めた実装は実 LLM を繋いだ瞬間に動かないことがあるため、注入シームへ実 LLM クライアントを刺して 1 回 invoke し、**中身のある出力が出ること**を確認している（`gitproject-dev-flow.md` §14-1-bis）。

| 項目 | 内容 |
|---|---|
| 実施日 | 2026-08-18 |
| 使用 LLM | ローカル CLI（Azure OpenAI `gpt-5.5`）を `BaseLLM` 実装として注入 |
| 注入シーム | `config[llm]` |
| 実 LLM 呼び出し | 1 回 |
| 出力 | ['<text output: 1570字>'] が非空。縮退理由なし |

本番の LLM は `AnthropicClient` 等で、これは検証用シーム。テンプレの `src/` は変更していない（一時テストを生成→実行→削除する方式）。CI には入れていない（ローカル CLI と鍵が要るため）。
