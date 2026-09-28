"""AgentCore Platform v1.0 — RET-C2-571 report generation seam.

Turns the quantitative fusion result into a buyer decision report (ringi
format). The LLM is injected (BaseLLM) so unit tests run with a fake; the
deterministic header / citations / structure are assembled in code so the
report is reproducible and citation-backed even if the LLM is stubbed.
"""

from __future__ import annotations

from shared.services.llm.base_llm import BaseLLM

DISCLAIMER = (
    "【ご注意】本レポートは AI が生成した意思決定支援ドラフトです。"
    "数値の突合・引用は自動処理によるものであり、最終的な調達/カテゴリ判断の前に"
    "一次情報の確認と担当者によるレビューを行ってください。"
)


def build_report(
    *,
    scope: dict,
    fusion_report: dict,
    citations: list[dict],
    degraded_sources: list[str],
    output_format: str,
    llm: BaseLLM | None,
) -> str:
    """Assemble the final markdown report.

    Deterministic structure (header, findings table, citations, disclaimer)
    is built in code; the narrative recommendation paragraph is delegated to
    the LLM when one is provided, otherwise a code-only summary is used.
    """
    header = _header(scope, degraded_sources)
    findings = _findings_table(fusion_report)
    narrative = _narrative(scope, fusion_report, llm)
    cites = _citations_block(citations)

    parts = [DISCLAIMER, "", header, "", "## 分析サマリ", narrative, "", findings, "", cites]
    report = "\n".join(parts)
    if output_format == "ringi":
        report = "# 稟議書: カテゴリ意思決定レポート\n\n" + report
    return report


def _header(scope: dict, degraded: list[str]) -> str:
    topic = scope.get("topic", "(未指定)")
    target = scope.get("target", "(未指定)")
    period = scope.get("period_days", 30)
    deg = "、".join(degraded) if degraded else "なし"
    return (
        f"**調査トピック**: {topic}　**対象**: {target}　**期間**: 直近{period}日\n"
        f"**未設定の有料ソース（縮退）**: {deg}"
    )


def _findings_table(fusion: dict) -> str:
    corr = fusion.get("correlations", {})
    corr_txt = (
        f"係数 {corr.get('coef')}（ラグ {corr.get('lag_days')}日, n={corr.get('n')}）"
        if corr.get("determinable")
        else f"判定不可（{corr.get('reason', '理由不明')}）"
    )
    div = fusion.get("divergences", [])
    inf = fusion.get("inflections", [])
    gaps = fusion.get("gaps", [])
    lines = [
        "## 定量融合の所見",
        "| 観点 | 結果 |",
        "|---|---|",
        f"| 相関（売上×話題量） | {corr_txt} |",
        f"| 乖離（機会損失候補） | {len(div)}件検出 |",
        f"| 転換点 | {len(inf)}件検出 |",
        f"| データ不足ギャップ | {len(gaps)}件（{'; '.join(g.get('field','') for g in gaps) or 'なし'}） |",
    ]
    return "\n".join(lines)


def _narrative(scope: dict, fusion: dict, llm: BaseLLM | None) -> str:
    if llm is None:
        return (
            "（LLM 未注入のためコード生成サマリ）"
            "定量融合の所見テーブルに基づき、相関・乖離・転換点・データ不足を確認してください。"
        )
    prompt = (
        "あなたは小売カテゴリ分析の専門家です。以下の定量融合結果に基づき、"
        "バイヤー向けの推奨アクションを3点以内で簡潔に日本語で述べてください。"
        "数値にない断定はせず、データ不足は明示してください。\n\n"
        f"トピック: {scope.get('topic')}\n融合結果: {fusion}"
    )
    resp = llm.complete([{"role": "user", "content": prompt}])
    return str(resp.get("content", "")).strip() or "（推奨生成なし）"


def _citations_block(citations: list[dict]) -> str:
    if not citations:
        return "## 引用\n（引用なし — データ不足）"
    lines = ["## 引用"]
    for i, c in enumerate(citations[:20], 1):
        lines.append(f"{i}. [{c.get('source','')}] {c.get('title','')} {c.get('url','')}".rstrip())
    return "\n".join(lines)
