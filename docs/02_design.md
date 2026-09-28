# Template Design Specification — RET-C2-571 Retail Category Intelligence Orchestrator

> 本書は設計の確定記録。
> 散文は日本語、技術トークン（クラス名・config キー・メソッドシグネチャ・enum）は英語のまま。

## Position in AgentCore Architecture

- **Agent Class**: `CategoryIntelligenceOrchestratorAgent`
- **L1 Base**: `AgentBaseGraph`（Cat 2 — 固定5ノード backbone を直継承。`AutonomousBaseGraph` ではない）
- **Three-Layer Separation**:
  - State: flat TypedDict composition（no Pydantic — msgpack incompatible）
  - Node: L1 inheritance（Template Method: `execute(self, state) -> dict` override only）
  - Graph: composition（`register_nodes()` で main スロットに `GraphNode` を差し込み）

> **Cat 2 構造**: 外側は `AgentBaseGraph` の固定5ノード backbone。**`main` スロットに `GraphNode` を置き、その内側に内部ワークフローグラフ `CategoryIntelligenceWorkflow`（domain workflow graph）を構成**する。decision.md §3 のオーケストレーション（確定→取得→調査→融合→レポート）はこの内部グラフが担う。`base_type` は概念パターン表記（agent.yaml では DocGeneration-系レポート生成パターン）であり、実継承は `AgentBaseGraph` L1 直継承で確定。

## Architecture Overview

### Node Configuration（外側 backbone）

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | セッション初期化・InvocationContext 取り込み | raw input | `validated_input` | InitializeNode (default) |
| pre_process | 入力検証・調査リクエスト正規化（topic / target / period / sources / output_format） | `validated_input` | `request` | `RequestNormalizeNode` (FunctionNode) |
| main | 内部ワークフロー `CategoryIntelligenceWorkflow` の実行 | `request` | `fusion_report` | `CategoryIntelligenceGraphNode` (GraphNode) |
| post_process | 稟議書整形・S-3 引用整合/景表法ゲート・disclaimer 付与 | `fusion_report` | `output` | `ReportFormatNode` (FunctionNode) |
| finalize | 出力射影・監査クローズ | `output` | final | FinalizeNode (default) |

### 内部ワークフロー `CategoryIntelligenceWorkflow`（main GraphNode の内側）

decision.md §3 の5ステップを内部グラフのノードとして実装する。

| Step Node | Responsibility | Output |
|---|---|---|
| `TargetScopingNode` | 製品/カテゴリ・対象(自社/公開企業/競合)・期間・指標を確定（曖昧時は軽 HITL = `interrupt()`） | 調査スコープ |
| `DataAcquireNode` | 売上/価格データ取得。(a) ユーザー提供 URL/CSV (b) Web fetch（公開情報）。**S-2/SSRF ガード**・スキーマ正規化・取得失敗は明示 | 正規化済み実績データ or 取得失敗フラグ |
| `TrendResearchNode` | `last30days-skill`（Tool）で30日トレンド/センチメント取得。**無料ソースを既定で実行・有料ソースはキー設定時のみ加算**（下記「ソース階層」）。取得不可時フォールバック | 外部トレンド・引用 |
| `FusionNode` ★本体価値 | 実績指標 × 外部トレンドを定量突合（下記①〜④の具体手法）。実績データ不在時は融合不能 = error（後述） | 融合分析 |
| `ReportGenNode` | 推奨アクション + 根拠/引用生成。S-3 引用整合・景表法ゲート前段 | 意思決定レポート draft |

#### `FusionNode` 定量融合の実装粒度（具体化 — 「貼るだけ」防止の生命線）

入力は ①実績側 = `acquired_data` の売上/価格時系列（日次 or 週次）、②外部側 = `trend_data` の last30days エンゲージ加重スコア・センチメント極性・話題量の日次時系列。両者を共通の時間軸（30日・日次／不足時は週次へ集約）に正規化したうえで以下を算出する。

| # | 突合 | 手法（具体） | 出力フィールド |
|---|---|---|---|
| ① 相関 | 売上/価格 vs 話題量・センチメント | ピアソン相関係数＋ラグ相関（0〜7日ラグで最大相関のラグを探索）。サンプル < 14 点なら「相関判定不可」を明示 | `correlations[]`（pair, coef, lag_days, n） |
| ② 乖離検出 | 外部急騰 vs 自社売上横ばい/欠品 | 各系列を z-score 標準化し、外部 z ≥ +1.5 かつ実績 z ≤ 0 の期間を機会損失候補として抽出 | `divergences[]`（window, ext_z, sales_z, type） |
| ③ 転換点検出 | トレンド反転の先行シグナル | 移動平均（7日）の傾き符号反転点を検出し、外部側の転換が実績側に先行する日数を測定 | `inflections[]`（date, side, lead_days） |
| ④ データ不足ギャップ | 突合できない領域 | 期間不一致・サンプル過少・系列欠落を列挙し、当該領域は推測補完せず「判定不可」と明示 | `gaps[]`（field, reason） |

#### 外部トレンドの「ソース階層」— 無料ソース実装 / 有料ソースは設定枠（予算前提・2026-06-29 確定）

本テンプレートは **追加課金なしで end-to-end が動く**ことを必須要件とする。`last30days-skill` が叩くソースを2階層に分け、無料層を実装・テストし、有料層は config の設定枠（secrets 参照）のみ用意する。

| 階層 | ソース例 | 実装範囲 | キー |
|---|---|---|---|
| **無料層（既定・本テンプレの実装スコープ）** | Hacker News（公式 API・無料）、Reddit public JSON、一般 web（公開ページ fetch）、Polymarket 公開エンドポイント等のキー不要ソース | **本テンプレで実装 + テスト（real or 録画データ）する対象**。これだけで TrendResearch → Fusion → Report が成立 | 不要 |
| **有料層（設定枠のみ）** | X(Twitter) API、Perplexity 等の有料 API 経由ソース | **config に設定枠 + `ctx.secrets.get()` 参照シームのみ**（optional なので `require()` は使わない）。キー未設定でも例外を投げず無料層へ縮退。テストは**モック**で経路だけ検証 | `agent.yaml` の `requires.secrets` に `required: false` で宣言 |

- **キー未設定時の挙動**: 有料層をスキップし無料層のみで実行。出力に「有料ソース（X/Perplexity）未設定のため一部シグナル欠落」を明示（`trend_data.degraded_sources[]`）。これは error ではなく**正常な部分実行**。
- **退化との区別**: 無料層でも TrendResearch の複数ソース合成 → Fusion の定量突合①〜④は完全実行されるため、「貼るだけ」退化には当たらない。有料層は**シグナルの厚みを足すオプション**であり、価値の核（実績 × 外部トレンドの定量融合）は無料層だけで成立する。
- **secrets 取り扱い（optional secret パターン）**: 有料 API キーは**任意**のため、`ctx.secrets.require()`（未設定時に raise）は使わない。`ctx.secrets.get("X_API_KEY")`（存在しなければ `None` を返す）で取得し、`None` なら当該有料ソースをスキップして無料層へ縮退する。`get()` が無い実装では `try: key = ctx.secrets.require(...) except SecretNotFoundError: key = None` で同等にする。いずれも State には置かない（`os.environ` 禁止）。`agent.yaml` の `requires.secrets` に `required: false` で宣言。

  ```python
  # optional paid-tier secret — must NOT raise when unset
  x_key = ctx.secrets.get("X_API_KEY")  # None if not configured
  if x_key:
      results += fetch_x_signals(query, x_key)
  else:
      degraded.append("X_API_KEY")      # recorded in trend_data.degraded_sources[]
  ```

> ⚠️ **退化禁止（criterion）**: 上記①〜④の数値処理が Agent 成立の生命線。これを欠き「last30days 出力を LLM 要約して貼るだけ」に退化すると Tool=No-Go。`tests/unit/` で (a) 相関係数算出 (b) 乖離 z-score 判定 (c) 転換点検出 (d) サンプル過少時の「判定不可」分岐、の4経路を検証する（LLM をモックしても数値ロジックが実行されることをアサート）。

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry, max 3)
                                          pre_process

  main 内側 (CategoryIntelligenceGraphNode → CategoryIntelligenceWorkflow):
    TargetScoping → DataAcquire → TrendResearch → Fusion → ReportGen
       └ (曖昧時 interrupt: HITL)         └ (取得失敗→明示)     └ (last30days不可→fallback)
```

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `validated_input` | `dict` | initialize 後の検証済み入力 | yes |
| `request` | `dict` | topic / target / period / sources / output_format の正規化リクエスト | yes |
| `acquired_data` | `dict` | 正規化済み売上/価格データ（or `{"status":"unavailable", ...}`） | no |
| `trend_data` | `dict` | last30days トレンド・センチメント・引用（or fallback フラグ） | no |
| `fusion_report` | `dict` | 融合分析結果（correlations / divergences / inflections / gaps） | yes |
| `output` | `str` | 稟議書フォーマット最終レポート（disclaimer 付） | yes |
| `hitl_draft` | `str\|dict\|None` | TargetScoping HITL の idempotency guard（template node が書く・schema 1.4 permitted） | no |
| `hitl_feedback` | `str\|dict\|None` | Framework 書き込み（resume 後） | no |
| `hitl_status` | `HitlStatus\|None` | Framework 書き込み（semantic outcome） | no |

**State Constraints (mandatory):**
- Flat TypedDict only（primitives + JSON-serializable types）。`AgentState` を継承し再宣言しない。
- No JWT, API keys, credentials in State（checkpoint DB leakage）。secrets は **必須キーは `ctx.secrets.require()` / 任意キー（有料層 X・Perplexity）は `ctx.secrets.get()`** で使い分けてアクセスし、いずれも State に置かない（`os.environ` 禁止）。本テンプレの有料層キーはすべて任意のため `get()` を用いる。
- InvocationContext via `config["configurable"]` only（State に入れない）。
- No Pydantic models, dataclass, arbitrary Python objects（msgpack incompatible）。取得 CSV は dict/list へ正規化して保持。

## Framework Utilization

### Shared Components Used
- [x] InvocationContext（correlation_id, session_id, permissions, credential handle）
- [x] SecurityViolationError
- [x] **S-2**: `DataAcquireNode` **および `TrendResearchNode` の両方**の Web fetch に対し SSRF ガードを実装（許可ドメイン allowlist・社内ネット/メタデータ IP 到達禁止・スキーム制限・サイズ/Content-Type 検査・タイムアウト）。無料層ソース（HN / Reddit public / web / Polymarket 公開）も外部 fetch であり S-2 対象＝同一の SSRF allowlist/sandbox を適用する。共通の fetch ガードは `shared/tools/` の安全 fetch ユーティリティに集約し、両ノードから呼ぶ。各ノードは `FunctionNode` の `_extra_security_gate_input()` で URL スキーム・サイズ上限の追加検証を行う。
- [x] **S-3**: `ReportFormatNode` の `_extra_security_gate_output()` で credential scan に加え、引用整合（出典のない断定の抑止）・景表法表現ゲート（優良誤認/有利誤認の表現フィルタ）を実施。`_security_gate_output()` は override しない（`@final`/`TypeError`）。
- [x] **S-4**: 各 `execute()` 内で `emit_trace_event()` を最低1回（DataAcquire の外部 fetch、TrendResearch の last30days 呼び出し、Fusion の融合実行、ReportGen の出力生成）。`node_start`/`node_complete`/`node_error` は `__call__()` が自動発火するため重複させない。

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass（pre_process/post_process/内部 step ノード）→ framework `@final` gate が自動実行。拡張は `_extra_security_gate_*()` のみ。
> - `GraphNode`（main = `CategoryIntelligenceGraphNode`）→ S-2/S-3 は deliberate no-op（内側 step ノードのゲートが適用済み）。error 伝播は下記。
> - 内部 step ノードは `FunctionNode` 継承で実装し `_extra_*` フックを使う。

### HITL 設計（TargetScoping の軽 HITL — 設計確定）

`TargetScopingNode` で調査スコープが曖昧な場合に D6 `interrupt()` パターンで人間確認を挟む。これに伴い `config/agent.yaml` を以下で確定する（未確定事項ではない＝`interrupt()` を使うなら必須）。

```yaml
memory_enabled: true        # MANDATORY — interrupt() pause/resume の checkpointer。これが無いと ConfigError
hitl:
  enabled: true
  max_hitl: 4               # TargetScoping の確認サイクル上限（positive int）
```

- 使用する HITL state field は schema 1.4 permitted の `hitl_draft` / `hitl_feedback` / `hitl_status` のみ。`hitl_count`（Framework only）/ `hitl_allowed`（Gateway only）はテンプレから書かない。
- `interrupt()` は `TargetScopingNode.execute()` 内から呼び、`hitl_draft` に確定前スコープを退避（resume 時の LLM 再実行防止）。
- PB-7（HITL interrupt 伝播）テスト必須: `interrupt()` が `GraphInterrupt` を送出し application error boundary に捕捉されない（`status` が error にならない）ことを検証。
- **採否確定（2026-06-29）: HITL を採用する。** 調査スコープが曖昧（topic だけで target/period が未指定 等）なら `interrupt()` で確認、明確に確定できれば確認なしで次ステップへ進む。`memory_enabled: true` / `hitl.enabled: true` を確定設定とする（中間状態は作らない）。

### Composition Pattern

- **Pattern**: GraphNode (subgraph) — main に `CategoryIntelligenceGraphNode` を置き、内側 `CategoryIntelligenceWorkflow` を実行
- **Composition target**: 内部 domain workflow graph（5 step ノード）
- **Error propagation strategy**: **propagate** — 内部 step ノードが `status="error"` を返した場合、`GraphNode.execute()` は上流 error をそのまま外側 state へ伝播し、`post_process` をスキップして finalize へ（pre→main 無条件辺のため main 側で上流 error を早期パススルー）。

  **取得失敗の扱い（部分成功 vs error の境界 — レビュー反映）:**
  - **片方が取れれば部分成功で継続**: 実績データ（`acquired_data`）が取れていて last30days のみ不可 → `trend_data.status="unavailable"` としてフォールバック（取得済み売上分析＋通常 web 検索で縮退）、Fusion は外部トレンド欠落を `gaps[]` に明示して継続。
  - **両方取れなければ error 伝播**: 実績データが取れない（DataAcquire 失敗）と Fusion は融合不能。この場合は欠落フラグで握り潰さず、`status="error"` ＋ `error_log=["acquired_data unavailable — fusion impossible"]` を返して外側へ伝播し、`post_process` をスキップ。**実績不在のまま last30days だけでレポートを生成しない**（＝「貼るだけ」退化の防止と、1.0.0 の「上流 error は post_process 前に表面化」契約の両立）。
  - **TargetScoping が対象を確定できない**（必須スコープ欠落）→ 軽 HITL（`interrupt()`）で確認、確認不能なら `status="error"`。

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only（no `agents/base/` required）。last30days-skill は `shared/tools/` 経由の Tool 呼び出し。

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | 多段だが固定パイプライン（自律 think-act ループではない）。終了条件は固定 5 ステップで決まる |
| Composition pattern | Standalone | GraphNode (subgraph) | **GraphNode** | 5 ステップのオーケストレーションを main 内側の domain workflow graph に閉じる Cat2 標準構造 |
| データ取得元 | CSV 必須 | URL/CSV or Web fetch 柔軟 | **柔軟** | CSV 必須は適用範囲を狭める。取得元を (a)(b) から選択可能にし汎用化（decision v2） |
| last30days 障害時 | 失敗で停止 | フォールバック縮退 | **フォールバック** | 取得済み売上分析＋無料 web 検索で縮退し、外部トレンド欠落を明示（部分成功） |
| 外部トレンドの有料 API | 全ソース実装 | 無料層実装+有料は設定枠 | **無料層実装+有料は設定枠** | 予算ゼロ前提。無料層（HN/Reddit public/web）で end-to-end 成立、有料層（X/Perplexity）はキー設定時のみ加算（未設定でも動く） |

## 設計上の未確定事項（実装フェーズで確定）

| # | 項目 | 確定時期 | オーナー |
|---|---|---|---|
| 1 | 無料層ソースの具体選定（HN / Reddit public / web / Polymarket 公開）と録画テストデータの用意 | 実装フェーズ | エンジニア |
| 2 | Web fetch S-2/SSRF allowlist の具体ドメイン・サンドボックス処理方式 | 実装フェーズ | エンジニア |

> **確定済み（2026-06-29）**:
> - Cat 判定 = **Cat 2 で確定**（Template ID とリポジトリ名は `RET-C2-571` で統一済み）。
> - HITL = **採用で確定**（`memory_enabled: true` / `hitl.enabled: true`。上記「HITL 設計」参照）。
> - 実績データ取得失敗時 = **両ソース失敗なら `status="error"` 伝播で確定**（退化防止優先。market-trend 単独レポートは返さない）。
> - **外部トレンドのソース戦略 = 無料層を実装+テスト・有料層（X/Perplexity）は config 設定枠のみ**で確定（予算前提）。キー未設定でも無料層で end-to-end が動く。上記「ソース階層」参照。budget/レート上限は有料層を使う導入先が後設定。
