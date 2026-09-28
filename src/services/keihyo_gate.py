"""AgentCore Platform v1.0 — RET-C2-571 景表法 (Act against Unjustifiable
Premiums and Misleading Representations) output check.

Used by ReportFormatNode._extra_security_gate_output() (S-3) to flag
superlative / unsubstantiated claims that would be a 優良誤認 / 有利誤認 risk
in a buyer-facing report. This is a preservation/content check layered on top
of the framework's @final credential scan — it does NOT replace it.
"""

from __future__ import annotations

# Superlative / absolute expressions that need substantiation in JP retail copy.
_RISK_TERMS = (
    "業界no.1",
    "業界no1",
    "日本一",
    "世界一",
    "最安",
    "最高品質",
    "完全無欠",
    "必ず儲かる",
    "100%効果",
    "絶対に",
    "唯一無二",
)


def scan_keihyo(text: str) -> list[str]:
    """Return a list of risky expressions found (case-insensitive)."""
    low = text.lower().replace(" ", "")
    hits: list[str] = []
    for term in _RISK_TERMS:
        if term.replace(" ", "") in low:
            hits.append(term)
    return hits


def annotate_if_risky(text: str) -> str:
    """Append a 景表法 caution footer when risky expressions are present.

    Non-destructive: we annotate rather than silently delete, so the human
    reviewer sees both the claim and the warning (preservation variant).
    """
    hits = scan_keihyo(text)
    if not hits:
        return text
    warn = (
        "\n\n---\n【景表法チェック】以下の表現は優良誤認/有利誤認のおそれがあります。"
        "客観的根拠の併記または表現修正を行ってください: " + "、".join(sorted(set(hits)))
    )
    return text + warn
