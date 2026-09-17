"""Process-wide pacing and retry policy for requests to arxiv.org.

arxiv.org/robots.txt declares "Crawl-delay: 15" under `User-agent: *` for
/list, /abs, and /pdf alike. Listing/abstract-page fetches (arxiv_fetcher.py)
and PDF downloads (generation_service.py) hit the same domain under that same
policy from different call sites, so they share one clock here instead of
each keeping an independent timer that could still race the other inside the
15s window.
"""

from __future__ import annotations

import time

CRAWL_DELAY_SECONDS = 15
RETRY_DELAYS_SECONDS = (15, 30, 90)

# arxiv.org/robots.txt asks operators to contact arXiv in advance if an
# application needs relaxed limits; a bare version string gives their abuse
# tooling nothing to go on if it ever needs to tell this client apart from an
# anonymous bot, so it carries a contact address.
USER_AGENT = "re-ass/1.0 (+mailto:dcroton@swin.edu.au)"


def is_transient_http_status(status_code: int) -> bool:
    return status_code == 429 or status_code == 406 or status_code >= 500


class ArxivRateLimiter:
    """Enforces a minimum gap between requests to arxiv.org."""

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
