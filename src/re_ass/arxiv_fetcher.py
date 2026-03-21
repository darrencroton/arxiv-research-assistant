from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import re

import arxiv

from re_ass.models import ArxivPaper, PreferenceConfig


LOGGER = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "using",
    "via",
    "with",
}
_TOKEN_EQUIVALENTS = {
    "agn": {"agn", "active", "galactic", "nuclei", "nucleus"},
    "llm": {"llm", "large", "language", "model"},
    "lrd": {"lrd", "little", "red", "dot"},
}


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_category_query(categories: tuple[str, ...]) -> str:
    return " OR ".join(f"cat:{category}" for category in categories)


def _to_paper(result: Any) -> ArxivPaper:
    return ArxivPaper(
        title=result.title.strip(),
        summary=result.summary.strip(),
        arxiv_url=result.entry_id.strip(),
        entry_id=result.entry_id.strip(),
        authors=tuple(author.name for author in result.authors),
        primary_category=result.primary_category,
        categories=tuple(result.categories),
        published=_ensure_utc(result.published),
        updated=_ensure_utc(result.updated) if result.updated else None,
    )


def filter_papers_between(
    papers: Iterable[ArxivPaper],
    *,
    start: datetime,
    end: datetime,
) -> list[ArxivPaper]:
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    return [
        paper
        for paper in papers
        if start_utc <= _ensure_utc(paper.published) < end_utc
    ]


def _normalize_token(token: str) -> str:
    normalized = token.casefold()
    if normalized.endswith("s") and len(normalized) > 3:
        normalized = normalized[:-1]
    return normalized


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in _TOKEN_PATTERN.findall(text):
        normalized = _normalize_token(raw_token)
        if normalized:
            tokens.add(normalized)

        for separator in ("-", "/"):
            if separator in raw_token:
                for part in raw_token.split(separator):
                    normalized_part = _normalize_token(part)
                    if normalized_part:
                        tokens.add(normalized_part)

    expanded_tokens: set[str] = set()
    for token in tokens:
        expanded_tokens.add(token)
        expanded_tokens.update(_TOKEN_EQUIVALENTS.get(token, set()))

    return {token for token in expanded_tokens if token and token not in _STOPWORDS}


def _score_preference_match(paper: ArxivPaper, preference: str) -> tuple[bool, int]:
    paper_text = paper.searchable_text.casefold()
    if preference.casefold() in paper_text:
        return True, 10_000

    preference_tokens = _tokenize(preference)
    if not preference_tokens:
        return False, 0

    paper_tokens = _tokenize(paper.searchable_text)
    overlap = len(preference_tokens & paper_tokens)
    return overlap > 0, overlap


def rank_papers(
    papers: Iterable[ArxivPaper],
    priorities: tuple[str, ...],
    max_papers: int,
) -> list[ArxivPaper]:
    ranked: list[tuple[int, int, int, float, str, ArxivPaper]] = []

    for paper in papers:
        matches = [
            (index, match_score)
            for index, priority in enumerate(priorities)
            for matched, match_score in [_score_preference_match(paper, priority)]
            if matched
        ]
        if not matches:
            continue
        best_rank = min(index for index, _score in matches)
        matched_preferences = len(matches)
        best_match_score = max(score for _index, score in matches)
        ranked.append(
            (
                best_rank,
                -matched_preferences,
                -best_match_score,
                -paper.published.timestamp(),
                paper.title.casefold(),
                paper,
            )
        )

    ranked.sort(key=lambda item: item[:4])
    return [paper for *_metadata, paper in ranked[:max_papers]]


class ArxivFetcher:
    def __init__(
        self,
        *,
        max_results: int,
        fetch_window_hours: int,
        fallback_window_hours: int | None = None,
        now_provider: Callable[[], datetime] | None = None,
        client: arxiv.Client | None = None,
    ) -> None:
        self.max_results = max_results
        self.fetch_window = timedelta(hours=fetch_window_hours)
        self.fallback_window = (
            timedelta(hours=fallback_window_hours)
            if fallback_window_hours and fallback_window_hours > fetch_window_hours
            else None
        )
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.client = client or arxiv.Client(page_size=min(max_results, 100), num_retries=3, delay_seconds=3)

    def fetch_top_papers(self, preferences: PreferenceConfig, max_papers: int) -> list[ArxivPaper]:
        query = build_category_query(preferences.categories)
        search = arxiv.Search(
            query=query,
            max_results=self.max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        results = list(self.client.results(search))
        all_papers = [_to_paper(result) for result in results]
        window_end = self.now_provider()
        cutoff = window_end - self.fetch_window
        primary_ranked = rank_papers(
            filter_papers_between(all_papers, start=cutoff, end=window_end),
            preferences.priorities,
            max_papers,
        )
        if len(primary_ranked) >= max_papers or self.fallback_window is None:
            return primary_ranked

        fallback_cutoff = window_end - self.fallback_window
        fallback_ranked = rank_papers(
            filter_papers_between(all_papers, start=fallback_cutoff, end=window_end),
            preferences.priorities,
            max_papers,
        )
        combined: list[ArxivPaper] = []
        seen_entry_ids: set[str] = set()

        for paper in [*primary_ranked, *fallback_ranked]:
            if paper.entry_id in seen_entry_ids:
                continue
            combined.append(paper)
            seen_entry_ids.add(paper.entry_id)
            if len(combined) == max_papers:
                break

        if combined and len(primary_ranked) < len(combined):
            LOGGER.info(
                "Expanded arXiv lookback from %s to %s hours to fill %s paper(s).",
                int(self.fetch_window.total_seconds() // 3600),
                int(self.fallback_window.total_seconds() // 3600),
                len(combined),
            )

        return combined
