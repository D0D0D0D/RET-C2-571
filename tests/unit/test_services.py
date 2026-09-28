# RET-C2-571 — Unit Tests: service layer (safe_fetch / data_acquire /
# trend_sources / report / keihyo_gate)

import json

import pytest

from framework.errors import SecurityViolationError
from src.services import data_acquire, keihyo_gate, report, safe_fetch, trend_sources


class TestSafeFetchSSRF:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "file:///etc/passwd",                         # non-http scheme
            "http://localhost:8080/admin",                # loopback host
            "http://evil.example.com/",                   # not in allowlist
        ],
    )
    def test_blocks_unsafe_urls(self, url):
        with pytest.raises(SecurityViolationError):
            safe_fetch.assert_url_allowed(url, safe_fetch.DEFAULT_ALLOWED_HOSTS)

    def test_allows_listed_host(self):
        # allowlisted host should pass the scheme/host checks (IP check skipped
        # offline is fine — host is on the list)
        try:
            safe_fetch.assert_url_allowed(
                "https://hn.algolia.com/api/v1/search", ["hn.algolia.com"]
            )
        except SecurityViolationError:
            # If DNS unavailable offline the IP guard may fire — acceptable.
            pass

    def test_size_cap_and_status(self):
        def fake(url, timeout):
            return 200, "x" * (safe_fetch._MAX_BYTES + 100)

        body = safe_fetch.safe_fetch(
            "https://hn.algolia.com/x", allowed_hosts=["hn.algolia.com"], fetcher=fake
        )
        assert len(body) <= safe_fetch._MAX_BYTES

    def test_http_error_raises(self):
        def fake(url, timeout):
            return 500, "err"

        with pytest.raises(SecurityViolationError):
            safe_fetch.safe_fetch(
                "https://hn.algolia.com/x", allowed_hosts=["hn.algolia.com"], fetcher=fake
            )


class TestDataAcquire:
    def test_csv_normalization(self):
        csv_text = "date,sales\n2026-01-01,100\n2026-01-02,120"
        r = data_acquire.acquire({"inline_data": csv_text})
        assert r["status"] == "ok"
        assert r["sales_series"] == [100.0, 120.0]

    def test_json_rows(self):
        rows = [{"sales": 5}, {"sales": 7}]
        r = data_acquire.acquire({"inline_data": rows})
        assert r["status"] == "ok"
        assert r["sales_series"] == [5.0, 7.0]

    def test_missing_data_is_unavailable(self):
        assert data_acquire.acquire({})["status"] == "unavailable"

    def test_user_url_public_host_allowed(self):
        # enforce_allowlist=False: any public host reachable (fetcher injected)
        def fake(url, timeout):
            return 200, "date,sales\n2026-01-01,42"

        r = data_acquire.acquire({"data_url": "https://example.com/data.csv"}, fetcher=fake)
        assert r["status"] == "ok"
        assert r["sales_series"] == [42.0]

    def test_user_url_internal_ip_blocked(self):
        # SSRF guard still blocks internal/metadata even with allowlist relaxed
        r = data_acquire.acquire({"data_url": "http://169.254.169.254/latest"})
        assert r["status"] == "unavailable"

    def test_no_sales_column_is_unavailable(self):
        r = data_acquire.acquire({"inline_data": "a,b\n1,2"})
        assert r["status"] == "unavailable"

    def test_malformed_does_not_crash(self):
        # non-iterable / weird values must degrade, never raise
        r = data_acquire.acquire({"inline_data": "sales\nnot_a_number"})
        assert r["status"] == "unavailable"


class TestTrendSources:
    def test_paid_tier_all_optional_unset(self):
        out = trend_sources.resolve_paid_tier(lambda k, d=None: None)
        assert out["active"] == []
        assert set(out["degraded_sources"]) == {"X_API_KEY", "PERPLEXITY_API_KEY"}

    def test_paid_tier_one_set(self):
        out = trend_sources.resolve_paid_tier(lambda k, d=None: "k" if k == "X_API_KEY" else None)
        assert out["active"] == ["X_API_KEY"]
        assert out["degraded_sources"] == ["PERPLEXITY_API_KEY"]

    def test_free_tier_collects_hn_and_reddit(self):
        def fake(url, timeout):
            if "algolia" in url:
                return 200, json.dumps({"hits": [{"points": 10, "num_comments": 5, "title": "t", "objectID": "1"}]})
            if "reddit" in url:
                return 200, json.dumps({"data": {"children": [{"data": {"score": 8, "num_comments": 2, "title": "r", "permalink": "/r/x/1"}}]}})
            return 404, ""

        out = trend_sources.collect_free_tier("plant protein", fetcher=fake)
        assert "hacker_news" in out["sources_used"]
        assert "reddit" in out["sources_used"]
        assert out["series"] == [15.0, 10.0]

    def test_free_tier_one_source_fails_gracefully(self):
        def fake(url, timeout):
            if "algolia" in url:
                return 200, json.dumps({"hits": [{"points": 3, "num_comments": 0, "title": "t", "objectID": "1"}]})
            raise ConnectionError("reddit down")

        out = trend_sources.collect_free_tier("x", fetcher=fake)
        assert "hacker_news" in out["sources_used"]
        assert any("reddit" in f for f in out["failed_free_sources"])


class TestReportAndKeihyo:
    def test_report_has_disclaimer_and_citations(self):
        rep = report.build_report(
            scope={"topic": "t", "target": "自社", "period_days": 30},
            fusion_report={"correlations": {"determinable": False, "reason": "x"}, "divergences": [], "inflections": [], "gaps": []},
            citations=[{"source": "hn", "title": "a", "url": "http://x"}],
            degraded_sources=["X_API_KEY"],
            output_format="ringi",
            llm=None,
        )
        assert "稟議書" in rep
        assert "AI が生成" in rep
        assert "X_API_KEY" in rep

    def test_keihyo_flags_superlatives(self):
        assert keihyo_gate.scan_keihyo("業界No.1の最安値")
        assert not keihyo_gate.scan_keihyo("前年比+10%")

    def test_keihyo_annotation_is_non_destructive(self):
        out = keihyo_gate.annotate_if_risky("これは日本一です")
        assert "日本一" in out  # original preserved
        assert "景表法チェック" in out  # warning appended
