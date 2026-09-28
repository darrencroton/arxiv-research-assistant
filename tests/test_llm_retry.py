"""Tests for status-aware LLM retry helpers (llm-gate 503/507 handling)."""

from __future__ import annotations

import httpx
import openai
import pytest

from re_ass.llm_retry import MAX_RETRY_AFTER_SECONDS, is_retryable_llm_error, retry_delay_seconds


def make_status_error(status: int, headers: dict[str, str] | None = None) -> openai.APIStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:8080/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return openai.APIStatusError(f"Error code: {status}", response=response, body=None)


def test_507_is_not_retryable() -> None:
    assert is_retryable_llm_error(make_status_error(507)) is False


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_statuses_remain_retryable(status: int) -> None:
    assert is_retryable_llm_error(make_status_error(status)) is True


def test_message_markers_still_non_retryable() -> None:
    assert is_retryable_llm_error(RuntimeError("authentication failed")) is False
    assert is_retryable_llm_error(RuntimeError("connection reset")) is True


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_is_honoured(status: int) -> None:
    assert retry_delay_seconds(make_status_error(status, {"Retry-After": "300"}), default=2) == 300.0


def test_retry_after_is_capped() -> None:
    error = make_status_error(503, {"Retry-After": "86400"})
    assert retry_delay_seconds(error, default=2) == MAX_RETRY_AFTER_SECONDS


@pytest.mark.parametrize("value", ["Wed, 21 Oct 2026 07:28:00 GMT", "soon", "-5", "NaN"])
def test_unusable_retry_after_falls_back_to_default(value: str) -> None:
    assert retry_delay_seconds(make_status_error(503, {"Retry-After": value}), default=4) == 4


def test_missing_retry_after_and_other_errors_use_default() -> None:
    assert retry_delay_seconds(make_status_error(503), default=4) == 4
    assert retry_delay_seconds(make_status_error(500, {"Retry-After": "300"}), default=4) == 4
    assert retry_delay_seconds(openai.APITimeoutError(httpx.Request("POST", "http://x")), default=4) == 4
