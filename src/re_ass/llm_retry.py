"""Shared retry helpers for transient LLM failures."""

from __future__ import annotations

import math


_NON_RETRYABLE_LLM_ERROR_MARKERS = (
    "credit balance is too low",
    "api key",
    "authentication",
    "logged out",
    "login required",
    "not logged in",
    "not authenticated",
    "no authentication information found",
    "not found on path",
)

# HTTP 507 from the local llm-gate means the requested model can never fit in
# memory, so retrying cannot succeed.
_NON_RETRYABLE_STATUS_CODES = frozenset({507})

# Statuses whose `Retry-After` header is honoured: 503 is the llm-gate's
# "hold expired, try later" reply; 429 is a standard rate-limit reply.
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})

# Upper bound on a server-requested wait, so a malformed or very large header
# cannot stall a run indefinitely.
MAX_RETRY_AFTER_SECONDS = 900.0


def _status_code(error: Exception) -> int | None:
    """Return the HTTP status carried by an SDK error (e.g. `openai.APIStatusError`), if any."""
    status = getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


def is_retryable_llm_error(error: Exception) -> bool:
    """Return True when an LLM failure looks transient and worth retrying."""
    if _status_code(error) in _NON_RETRYABLE_STATUS_CODES:
        return False
    message = str(error).lower()
    return not any(marker in message for marker in _NON_RETRYABLE_LLM_ERROR_MARKERS)


def retry_delay_seconds(error: Exception, default: float) -> float:
    """Return how long to wait before retrying after `error`.

    Honours a numeric `Retry-After` header on 429/503 responses (capped at
    `MAX_RETRY_AFTER_SECONDS`); otherwise, or if the header is missing or not a
    number of seconds, returns `default`.
    """
    if _status_code(error) not in _RETRY_AFTER_STATUS_CODES:
        return default
    headers = getattr(getattr(error, "response", None), "headers", None)
    raw = headers.get("retry-after") if headers is not None else None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(seconds) or seconds < 0:
        return default
    return min(seconds, MAX_RETRY_AFTER_SECONDS)
