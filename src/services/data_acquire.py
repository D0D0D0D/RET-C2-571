"""AgentCore Platform v1.0 — RET-C2-571 sales/price data acquisition.

Normalizes user-provided sales/price data (CSV text or JSON rows) or a
web-fetched public source into a sales_series list consumable by fusion.
On failure returns an explicit {"status": "unavailable", "reason": ...}
rather than guessing — Fusion then treats absence of internal data as an
error (no market-trend-only report).

Web fetch (public source path) goes through safe_fetch (S-2 / SSRF guard).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Callable

from src.services.safe_fetch import safe_fetch

FetcherT = Callable[[str, float], tuple[int, str]]

# Column name candidates for the numeric series we fuse on.
_SALES_KEYS = ("sales", "units", "revenue", "qty", "quantity", "amount")


def acquire(request: dict, *, fetcher: FetcherT | None = None) -> dict:
    """Acquire and normalize sales/price data.

    request may carry:
      - request["inline_data"]: CSV text or list-of-dict rows (user provided)
      - request["data_url"]: a public URL to fetch (S-2 guarded)
    Returns {"status": "ok", "sales_series": [...], "rows": n} or
    {"status": "unavailable", "reason": ...}.
    """
    inline = request.get("inline_data")
    if inline is not None:
        return _normalize(inline)

    url = request.get("data_url")
    if url:
        try:
            # User-supplied URL: allow any *public* host (allowlist relaxed),
            # but internal/metadata IPs and non-http(s) schemes stay blocked.
            body = safe_fetch(url, enforce_allowlist=False, fetcher=fetcher)
        except Exception as exc:  # noqa: BLE001 — surface as unavailable, caller decides
            return {"status": "unavailable", "reason": f"fetch failed: {type(exc).__name__}"}
        return _normalize(body)

    return {"status": "unavailable", "reason": "no inline_data or data_url provided"}


def _normalize(data) -> dict:
    """Coerce CSV text / JSON / list-of-dict into a numeric sales_series."""
    rows = _to_rows(data)
    if rows is None:
        return {"status": "unavailable", "reason": "unparseable data format"}
    if not rows:
        return {"status": "unavailable", "reason": "no rows in data"}

    key = _pick_sales_key(rows[0])
    if key is None:
        return {
            "status": "unavailable",
            "reason": f"no sales-like column found (looked for {_SALES_KEYS})",
        }

    series: list[float] = []
    for r in rows:
        val = r.get(key)
        num = _as_float(val)
        if num is not None:
            series.append(num)

    if not series:
        return {"status": "unavailable", "reason": f"column '{key}' had no numeric values"}

    return {"status": "ok", "sales_series": series, "rows": len(series), "column": key}


def _to_rows(data) -> list[dict] | None:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return []
        # try JSON first
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
        except (json.JSONDecodeError, ValueError):
            pass
        # fall back to CSV
        try:
            reader = csv.DictReader(io.StringIO(text))
            return list(reader)
        except csv.Error:
            return None
    return None


def _pick_sales_key(row: dict) -> str | None:
    lower = {k.lower(): k for k in row}
    for cand in _SALES_KEYS:
        if cand in lower:
            return lower[cand]
    return None


def _as_float(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None
