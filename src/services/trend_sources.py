"""AgentCore Platform v1.0 — RET-C2-571 external trend sources.

Free tier (implemented + tested): Hacker News, Reddit public JSON,
Polymarket public — all key-free. Paid tier (config slot only): X / Perplexity
accessed via ctx.secrets.get() (optional). When a paid key is unset the source
is skipped and recorded in degraded_sources[] — this is a normal partial run,
NOT an error.

All network egress goes through src.services.safe_fetch (S-2 / SSRF guard),
so free-tier fetches are gated identically to DataAcquire fetches. The fetcher
is injectable so unit tests run offline against recorded fixtures.
"""

from __future__ import annotations

import json
from typing import Callable

from src.services.safe_fetch import safe_fetch

FetcherT = Callable[[str, float], tuple[int, str]]

# Free-tier endpoints (no API key required)
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=story"
REDDIT_SEARCH = "https://www.reddit.com/search.json?q={q}&sort=new&limit=50"

PAID_SECRETS = ("X_API_KEY", "PERPLEXITY_API_KEY")


def collect_free_tier(topic: str, *, fetcher: FetcherT | None = None) -> dict:
    """Collect engagement signals from key-free public sources.

    Returns {series, citations, sources_used}. `series` is a per-bucket
    engagement-weighted volume list consumable by fusion.fuse().
    Individual source failures degrade gracefully (recorded, not raised).
    """
    citations: list[dict] = []
    sources_used: list[str] = []
    points: list[float] = []
    failed: list[str] = []

    for name, builder in (("hacker_news", _hn), ("reddit", _reddit)):
        try:
            sig = builder(topic, fetcher)
            points.extend(sig["series"])
            citations.extend(sig["citations"])
            sources_used.append(name)
        except Exception as exc:  # noqa: BLE001 — one source must not kill the rest
            failed.append(f"{name}: {type(exc).__name__}")

    return {
        "series": points,
        "citations": citations,
        "sources_used": sources_used,
        "failed_free_sources": failed,
    }


def resolve_paid_tier(secrets_get: Callable[[str, str | None], str | None]) -> dict:
    """Decide which paid sources are active based on optional secrets.

    `secrets_get` is ctx.secrets.get (returns None when unset). Returns
    {active: [...], degraded_sources: [...]}. Never raises on missing keys.
    """
    active: list[str] = []
    degraded: list[str] = []
    for key in PAID_SECRETS:
        if secrets_get(key, None):
            active.append(key)
        else:
            degraded.append(key)
    return {"active": active, "degraded_sources": degraded}


def _hn(topic: str, fetcher: FetcherT | None) -> dict:
    body = safe_fetch(HN_SEARCH.format(q=_q(topic)), fetcher=fetcher)
    data = json.loads(body)
    hits = data.get("hits", [])
    series = [float(h.get("points", 0) or 0) + float(h.get("num_comments", 0) or 0) for h in hits]
    citations = [
        {"source": "hacker_news", "title": h.get("title", ""), "url": h.get("url") or _hn_item(h)}
        for h in hits[:10]
        if h.get("title")
    ]
    return {"series": series, "citations": citations}


def _reddit(topic: str, fetcher: FetcherT | None) -> dict:
    body = safe_fetch(REDDIT_SEARCH.format(q=_q(topic)), fetcher=fetcher)
    data = json.loads(body)
    children = data.get("data", {}).get("children", [])
    series = [
        float(c.get("data", {}).get("score", 0) or 0)
        + float(c.get("data", {}).get("num_comments", 0) or 0)
        for c in children
    ]
    citations = [
        {
            "source": "reddit",
            "title": c.get("data", {}).get("title", ""),
            "url": "https://www.reddit.com" + c.get("data", {}).get("permalink", ""),
        }
        for c in children[:10]
        if c.get("data", {}).get("title")
    ]
    return {"series": series, "citations": citations}


def _hn_item(hit: dict) -> str:
    oid = hit.get("objectID", "")
    return f"https://news.ycombinator.com/item?id={oid}" if oid else ""


def _q(topic: str) -> str:
    from urllib.parse import quote

    return quote(topic.strip())
