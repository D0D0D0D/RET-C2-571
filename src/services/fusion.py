"""AgentCore Platform v1.0 — RET-C2-571 quantitative fusion (core value).

Pure, dependency-free numeric logic that fuses internal sales/price series
with external trend signals. This is the agent's reason to exist (Agent vs
Tool): without these computations the template would be a "paste-only"
wrapper around last30days and would be a Tool, not an Agent.

Implements the four fusion operations from docs/02_design.md:
  (1) correlation   — Pearson + lag (0..7 days)
  (2) divergence    — z-score: external spike vs flat/declining sales
  (3) inflection    — moving-average slope sign flip + lead/lag
  (4) gaps          — explicit "cannot judge" when data is missing/too sparse

No LLM here — the LLM only narrates the report later. These functions are
unit-tested directly so the numeric path is provably exercised.
"""

from __future__ import annotations

from math import sqrt

MIN_CORR_POINTS = 14  # below this, correlation is "not determinable"
MAX_LAG_DAYS = 7
DIVERGENCE_Z = 1.5
MA_WINDOW = 7


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation, or None if undefined (zero variance / too short)."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sqrt(sum((x - mx) ** 2 for x in xs))
    dy = sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _zscores(xs: list[float]) -> list[float]:
    n = len(xs)
    if n == 0:
        return []
    m = sum(xs) / n
    sd = sqrt(sum((x - m) ** 2 for x in xs) / n)
    if sd == 0:
        return [0.0] * n
    return [(x - m) / sd for x in xs]


def _moving_avg(xs: list[float], window: int) -> list[float]:
    if window <= 1 or len(xs) < window:
        return list(xs)
    out: list[float] = []
    for i in range(len(xs) - window + 1):
        out.append(sum(xs[i : i + window]) / window)
    return out


def correlate(sales: list[float], external: list[float]) -> dict:
    """(1) Pearson + best lag. Returns a structured result, never raises."""
    n = min(len(sales), len(external))
    if n < MIN_CORR_POINTS:
        return {"determinable": False, "reason": f"need >= {MIN_CORR_POINTS} points, got {n}"}
    best = {"coef": 0.0, "lag_days": 0}
    found = False
    for lag in range(0, MAX_LAG_DAYS + 1):
        if n - lag < MIN_CORR_POINTS:
            break
        # external leads sales by `lag` days
        coef = _pearson(sales[lag:n], external[: n - lag])
        if coef is None:
            continue
        if not found or abs(coef) > abs(best["coef"]):
            best = {"coef": round(coef, 4), "lag_days": lag}
            found = True
    if not found:
        return {"determinable": False, "reason": "zero variance in one series"}
    return {"determinable": True, "n": n, **best}


def detect_divergence(sales: list[float], external: list[float]) -> list[dict]:
    """(2) External spike (z>=+1.5) while sales flat/declining (z<=0)."""
    n = min(len(sales), len(external))
    if n == 0:
        return []
    sz = _zscores(sales[:n])
    ez = _zscores(external[:n])
    out: list[dict] = []
    for i in range(n):
        if ez[i] >= DIVERGENCE_Z and sz[i] <= 0:
            out.append(
                {
                    "index": i,
                    "ext_z": round(ez[i], 3),
                    "sales_z": round(sz[i], 3),
                    "type": "opportunity_loss",  # demand interest up, own sales not
                }
            )
    return out


def detect_inflection(series: list[float], *, side: str) -> list[dict]:
    """(3) Moving-average slope sign-flip points (trend reversal signals)."""
    ma = _moving_avg(series, MA_WINDOW)
    if len(ma) < 3:
        return []
    out: list[dict] = []
    prev_slope = 0.0
    for i in range(1, len(ma)):
        slope = ma[i] - ma[i - 1]
        if prev_slope != 0.0 and slope != 0.0 and (slope > 0) != (prev_slope > 0):
            out.append({"index": i, "side": side, "direction": "up" if slope > 0 else "down"})
        if slope != 0.0:
            prev_slope = slope
    return out


def fuse(acquired_data: dict, trend_data: dict) -> dict:
    """Run all four fusion operations. Caller guarantees sales data exists.

    Returns {correlations, divergences, inflections, gaps}. Missing/sparse
    inputs become explicit `gaps` entries — never silently imputed.
    """
    sales = [float(v) for v in acquired_data.get("sales_series", []) if _is_num(v)]
    external = [float(v) for v in trend_data.get("series", []) if _is_num(v)]

    gaps: list[dict] = []
    if not external:
        gaps.append({"field": "trend_data.series", "reason": "no external trend signal available"})

    correlations = correlate(sales, external) if external else {"determinable": False, "reason": "no external series"}
    if isinstance(correlations, dict) and not correlations.get("determinable"):
        gaps.append({"field": "correlation", "reason": correlations.get("reason", "not determinable")})

    divergences = detect_divergence(sales, external) if external else []
    sales_inf = detect_inflection(sales, side="sales")
    ext_inf = detect_inflection(external, side="external") if external else []
    inflections = sales_inf + ext_inf

    # lead_days: how many days the external trend's first reversal preceded the
    # sales reversal (positive => external led). None when either side has none.
    lead_days = None
    if sales_inf and ext_inf:
        lead_days = sales_inf[0]["index"] - ext_inf[0]["index"]

    return {
        "correlations": correlations,
        "divergences": divergences,
        "inflections": inflections,
        "lead_days": lead_days,
        "gaps": gaps,
    }


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)
