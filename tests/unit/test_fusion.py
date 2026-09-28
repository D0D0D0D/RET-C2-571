# RET-C2-571 — Unit Tests: quantitative fusion (core value, degradation guard)
#
# These tests prove the four numeric fusion operations actually run — the
# Agent-vs-Tool line. A "paste-only" regression would fail these.

from src.services import fusion


class TestCorrelation:
    def test_full_positive_correlation(self):
        xs = [float(i) for i in range(16)]
        r = fusion.correlate(xs, xs)
        assert r["determinable"] is True
        assert abs(r["coef"] - 1.0) < 1e-9
        assert r["lag_days"] == 0

    def test_sparse_is_not_determinable(self):
        xs = [float(i) for i in range(10)]  # below MIN_CORR_POINTS
        r = fusion.correlate(xs, xs)
        assert r["determinable"] is False
        assert "14" in r["reason"]

    def test_lag_detection(self):
        # external leads sales by 2 days
        base = [float(i % 5) for i in range(20)]
        sales = base[2:]
        ext = base[:-2]
        r = fusion.correlate(sales, ext)
        assert r["determinable"] is True


class TestDivergence:
    def test_external_spike_flat_sales(self):
        sales = [10.0] * 16
        ext = [1.0] * 15 + [50.0]
        d = fusion.detect_divergence(sales, ext)
        assert len(d) >= 1
        assert d[-1]["type"] == "opportunity_loss"

    def test_no_divergence_when_aligned(self):
        xs = [float(i) for i in range(16)]
        assert fusion.detect_divergence(xs, xs) == []


class TestInflection:
    def test_reversal_detected(self):
        series = [float(x) for x in [1, 2, 3, 4, 5, 6, 7, 8, 7, 6, 5, 4, 3, 2, 1]]
        inf = fusion.detect_inflection(series, side="sales")
        assert len(inf) >= 1
        assert inf[0]["side"] == "sales"

    def test_monotonic_has_no_inflection(self):
        series = [float(i) for i in range(15)]
        assert fusion.detect_inflection(series, side="sales") == []


class TestFuseGaps:
    def test_no_external_records_gap(self):
        r = fusion.fuse({"sales_series": [float(i) for i in range(16)]}, {"series": []})
        assert any(g["field"] == "trend_data.series" for g in r["gaps"])
        assert r["correlations"]["determinable"] is False

    def test_full_fusion_all_keys(self):
        xs = [float(i) for i in range(16)]
        r = fusion.fuse({"sales_series": xs}, {"series": xs})
        assert {"correlations", "divergences", "inflections", "gaps"} <= set(r.keys())
        assert "lead_days" in r
        assert r["correlations"]["determinable"] is True
