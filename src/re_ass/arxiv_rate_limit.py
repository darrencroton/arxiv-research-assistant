"""Process-wide pacing and retry policy for requests to the arxiv.org family
of domains.

arxiv.org/robots.txt declares "Crawl-delay: 15" under `User-agent: *` for
/list, /abs, and /pdf alike. Only the announcement-day listing fetch
(arxiv_fetcher.py's _fetch_listing_html) still hits that interactive main
site -- see AGENTS.md's Working Notes for why: export.arxiv.org's copy of
/list is cached for days and cannot serve "what's new" queries reliably.
Abstract-page fallback fetches and PDF downloads go to export.arxiv.org
instead, arXiv's site "specifically set aside for programmatic access"
(https://info.arxiv.org/help/bulk_data.html). All of it shares one clock
here rather than each call site keeping an independent timer that could
still race the others inside the 15s window.
"""

from __future__ import annotations

import time

CRAWL_DELAY_SECONDS = 15
RETRY_DELAYS_SECONDS = (15, 30, 90)

# The listing fetch is the one call site still exposed to arxiv.org's main-site
# bot mitigation (see AGENTS.md), which clears within tens of minutes rather
# than being a lasting block. A longer, dedicated schedule gives a single run
# a real chance to ride that out instead of always going fatal and waiting
# for the next scheduled invocation.
LISTING_RETRY_DELAYS_SECONDS = (15, 30, 90, 300, 600)

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
