"""AgentCore Platform v1.0 — RET-C2-571 safe HTTP fetch (S-2 / SSRF guard).

Shared by DataAcquireNode (sales/price web fetch) and TrendResearchNode
(free-tier trend sources). Both perform outbound HTTP and are S-2 targets,
so the same SSRF allowlist / sandbox is applied here in one place.

The guard rejects requests to internal networks, cloud metadata IPs, and
non-http(s) schemes, enforces a host allowlist, and caps response size.
Network egress itself is injected (fetcher) so unit tests run offline.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlparse

from framework.errors import SecurityViolationError

# Hosts the agent is allowed to reach. Free-tier trend sources + common
# retail IR / market data domains. Extend per deployment in config.
DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = (
    "hacker-news.firebaseio.com",  # HN public API (free)
    "hn.algolia.com",             # HN search API (free)
    "www.reddit.com",             # Reddit public JSON (free)
    "reddit.com",
    "gamma-api.polymarket.com",   # Polymarket public API (free)
    "clob.polymarket.com",
)

_MAX_BYTES = 2_000_000  # 2 MB response cap
_ALLOWED_SCHEMES = ("http", "https")


def _is_blocked_ip(host: str) -> bool:
    """Resolve host and block private / loopback / link-local / metadata IPs."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Unresolvable host — treat as blocked (fail closed).
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip_str == "169.254.169.254"  # cloud metadata endpoint
        ):
            return True
    return False


def assert_url_allowed(
    url: str, allowed_hosts: Iterable[str], *, enforce_allowlist: bool = True
) -> None:
    """Raise SecurityViolationError if the URL is not safe to fetch.

    The scheme check and the internal/metadata-IP (SSRF) check ALWAYS run.
    The host allowlist is enforced only when enforce_allowlist=True. Trend
    sources use a fixed allowlist (enforce_allowlist=True); user-supplied
    data_url uses enforce_allowlist=False so any *public* host is reachable
    while internal/metadata addresses stay blocked.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SecurityViolationError(
            f"safe_fetch: scheme '{parsed.scheme}' not permitted (http/https only)"
        )
    host = parsed.hostname or ""
    if enforce_allowlist and host not in set(allowed_hosts):
        raise SecurityViolationError(
            f"safe_fetch: host '{host}' is not in the allowlist"
        )
    if _is_blocked_ip(host):
        raise SecurityViolationError(
            f"safe_fetch: host '{host}' resolves to a blocked (internal/metadata) address"
        )


def safe_fetch(
    url: str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    enforce_allowlist: bool = True,
    timeout: float = 10.0,
    fetcher: Callable[[str, float], tuple[int, str]] | None = None,
) -> str:
    """Fetch a URL with SSRF protection. Returns the response body (truncated).

    `fetcher(url, timeout) -> (status_code, body)` is the injectable network
    seam; the default uses urllib. Tests pass a fake fetcher to stay offline.
    enforce_allowlist=False relaxes the host allowlist for user-supplied URLs
    (internal/metadata IPs stay blocked regardless).
    """
    hosts = tuple(allowed_hosts) if allowed_hosts is not None else DEFAULT_ALLOWED_HOSTS
    assert_url_allowed(url, hosts, enforce_allowlist=enforce_allowlist)

    if fetcher is None:
        fetcher = _urllib_fetch

    status, body = fetcher(url, timeout)
    if status >= 400:
        raise SecurityViolationError(
            f"safe_fetch: upstream returned HTTP {status} for {url}"
        )
    if len(body) > _MAX_BYTES:
        body = body[:_MAX_BYTES]
    return body


# Content-Types we accept from public data endpoints (text/JSON/CSV).
# NOTE: this check runs only on the real urllib egress path below. Injected
# test fetchers (offline) return bodies directly and bypass it by design —
# production traffic always goes through _urllib_fetch and is checked.
_ALLOWED_CONTENT_PREFIXES = ("application/json", "text/", "application/csv")


def _urllib_fetch(url: str, timeout: float) -> tuple[int, str]:
    """Default network egress via urllib (only invoked outside tests)."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "RET-C2-571/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (guarded above)
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if ctype and not any(ctype.startswith(p) for p in _ALLOWED_CONTENT_PREFIXES):
            raise SecurityViolationError(
                f"safe_fetch: response Content-Type '{ctype}' is not text/JSON/CSV"
            )
        raw = resp.read(_MAX_BYTES + 1)
    return resp.status, raw.decode("utf-8", errors="replace")
