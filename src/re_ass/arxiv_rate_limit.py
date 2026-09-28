"""Process-wide pacing and retry policy for requests to the arxiv.org family
of domains.

All arxiv-host HTTP -- the `rss.arxiv.org` announcement feed, `arxiv.org/list`
gap fill, `export.arxiv.org` abstract pages, and `export.arxiv.org` PDFs --
shares one crawl-delay clock here rather than each call site keeping an
independent timer that could still race the others inside the 15s window.
The RSS feed, `/list`, and PDF downloads also retry on a transient HTTP
status per RETRY_DELAYS_SECONDS; the abstract-page fetch makes a single
attempt, since it is itself a fallback reached only after the primary
metadata query has already hit a transient error.
"""

from __future__ import annotations

import gzip
import time
import zlib
from urllib.error import HTTPError

CRAWL_DELAY_SECONDS = 15
RETRY_DELAYS_SECONDS = (15, 30, 90)

# arxiv.org/robots.txt asks operators to contact arXiv in advance if an
# application needs relaxed limits; a bare version string gives their abuse
# tooling nothing to go on if it ever needs to tell this client apart from an
# anonymous bot, so it carries a contact address.
USER_AGENT = "re-ass/1.0 (+mailto:dcroton@swin.edu.au)"

# Accept/Accept-Language/Accept-Encoding for text responses; decode_response_text()
# un-gzips or inflates the compressed bodies this invites.
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def is_transient_http_status(status_code: int) -> bool:
    return status_code == 429 or status_code == 406 or status_code >= 500


def _decompress(body: bytes, content_encoding: str) -> bytes:
    encoding = (content_encoding or "").lower()
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def decode_response_text(response) -> str:
    """Read and decode an HTML or XML text body, un-gzipping or inflating it
    when Content-Encoding says so."""
    body = _decompress(response.read(), response.headers.get("Content-Encoding"))
    return body.decode("utf-8")


_ERROR_BODY_SNIPPET_BYTES = 2048


def describe_http_error(exc: HTTPError) -> str:
    """Best-effort summary of an HTTPError's Retry-After header and body
    snippet, for diagnosing a transient status."""
    parts = []
    headers = getattr(exc, "headers", None)
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after:
        parts.append(f"Retry-After={retry_after}")
    try:
        # Bounded read: an error page is normally tiny, but nothing in HTTP
        # guarantees that, and this must never be the thing that turns a
        # transient-status retry into a slow, memory-heavy detour.
        raw = exc.read(_ERROR_BODY_SNIPPET_BYTES)
        content_encoding = (
            headers.get("Content-Encoding") if headers is not None else None
        )
        if content_encoding:
            raw = _decompress(raw, content_encoding)
    except Exception:
        raw = b""
    if raw:
        snippet = raw.decode("utf-8", errors="replace").strip()
        if snippet:
            parts.append(f"body={snippet!r}")
    return "; ".join(parts) if parts else "(no additional detail in response)"


class ArxivRateLimiter:
    """Enforces a minimum gap between requests to the arxiv.org family of hosts."""

    def __init__(self) -> None:
        self._last_request_at: float | None = None

    def wait_for_crawl_delay(self) -> None:
        if self._last_request_at is None:
            return
        remaining = CRAWL_DELAY_SECONDS - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def mark_request_completed(self) -> None:
        self._last_request_at = time.monotonic()


_shared_limiter = ArxivRateLimiter()


def get_shared_limiter() -> ArxivRateLimiter:
    """Return the process-wide limiter shared by all arxiv.org call sites."""
    return _shared_limiter
