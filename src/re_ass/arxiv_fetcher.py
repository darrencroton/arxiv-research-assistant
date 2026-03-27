"""arXiv paper fetching and announcement-day candidate collection for re-ass."""

from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser
import logging
from typing import Any
import re
from urllib.request import Request, urlopen

import arxiv

from re_ass.models import ArxivPaper, PreferenceConfig
from re_ass.paper_identity import derive_identity, extract_source_id


LOGGER = logging.getLogger(__name__)
_ANNOUNCEMENT_HEADING_RE = re.compile(r"^(?P<label>[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4})")
_RECENT_PAGE_SIZE = 2000


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


class _AnnouncementListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.day_to_ids: dict[date, list[str]] = {}
        self._day_seen_ids: dict[date, set[str]] = {}
        self._current_date: date | None = None
        self._inside_heading = False
        self._heading_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self._inside_heading = True
            self._heading_chunks = []
            return

        if tag != "a" or self._current_date is None:
            return

        href = dict(attrs).get("href") or ""
        if not href.startswith("/abs/"):
            return

        source_id = extract_source_id(href)
        day_seen = self._day_seen_ids.setdefault(self._current_date, set())
        if source_id in day_seen:
            return
        self.day_to_ids.setdefault(self._current_date, []).append(source_id)
        day_seen.add(source_id)

    def handle_data(self, data: str) -> None:
        if self._inside_heading:
            self._heading_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "h3" or not self._inside_heading:
            return
        self._inside_heading = False
        heading_text = " ".join("".join(self._heading_chunks).split())
        match = _ANNOUNCEMENT_HEADING_RE.match(heading_text)
        if match is None:
            return
        self._current_date = datetime.strptime(match.group("label"), "%a, %d %b %Y").date()


class ArxivFetcher:
    def __init__(
        self,
        *,
        page_size: int,
        client: arxiv.Client | None = None,
        listing_fetcher: Any | None = None,
    ) -> None:
        self.page_size = max(1, min(page_size, 100))
        self.client = client or arxiv.Client(page_size=self.page_size, num_retries=3, delay_seconds=3)
        self._listing_fetcher = listing_fetcher or self._fetch_listing_html
        self._listing_cache: dict[str, dict[date, list[str]]] = {}

    def _fetch_listing_html(self, category: str) -> str:
        url = f"https://arxiv.org/list/{category}/pastweek?show={_RECENT_PAGE_SIZE}"
        request = Request(url, headers={"User-Agent": "re-ass/1.0"})
        with urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")

    def _category_listing(self, category: str) -> dict[date, list[str]]:
        cached = self._listing_cache.get(category)
        if cached is not None:
            return cached

        parser = _AnnouncementListingParser()
        parser.feed(self._listing_fetcher(category))
        listing = {day: list(ids) for day, ids in parser.day_to_ids.items()}
        self._listing_cache[category] = listing
        return listing

    def available_announcement_dates(self, categories: tuple[str, ...]) -> tuple[date, ...]:
        dates: set[date] = set()
        for category in categories:
            dates.update(self._category_listing(category))
        return tuple(sorted(dates))

    def collect_candidates(
        self,
        preferences: PreferenceConfig,
        *,
        announcement_date: date,
        excluded_paper_keys: set[str] | None = None,
    ) -> list[ArxivPaper]:
        """Fetch all papers listed for an announcement day, suppressing duplicates by paper_key."""
        ordered_source_ids: list[str] = []
        seen_source_ids: set[str] = set()
        for category in preferences.categories:
            listing = self._category_listing(category)
            for source_id in listing.get(announcement_date, []):
                if source_id in seen_source_ids:
                    continue
                ordered_source_ids.append(source_id)
                seen_source_ids.add(source_id)

        if not ordered_source_ids:
            raise ValueError(
                f"Announcement date {announcement_date.isoformat()} is not visible in the recent arXiv listing "
                f"for categories {', '.join(preferences.categories)}."
            )

        excluded = excluded_paper_keys or set()
        pending_source_ids = [
            source_id
            for source_id in ordered_source_ids
            if f"arxiv:{source_id}" not in excluded
        ]
        if not pending_source_ids:
            LOGGER.info(
                "Collected 0 candidate paper(s) for announcement date %s across categories %s after excluding completed papers.",
                announcement_date.isoformat(),
                ", ".join(preferences.categories),
            )
            return []

        search = arxiv.Search(id_list=pending_source_ids, max_results=len(pending_source_ids))
        results_by_id: dict[str, ArxivPaper] = {}
        for result in self.client.results(search):
            paper = _to_paper(result)
            try:
                identity = derive_identity(paper)
            except ValueError as error:
                LOGGER.warning("Skipping paper with invalid arXiv identity (%s): %s", paper.title, error)
                continue
            results_by_id[identity.source_id] = paper

        combined = [results_by_id[source_id] for source_id in pending_source_ids if source_id in results_by_id]
        missing_source_ids = [source_id for source_id in pending_source_ids if source_id not in results_by_id]
        if missing_source_ids:
            LOGGER.warning(
                "Recent listing exposed %s source id(s) that were missing from the arXiv API response: %s",
                len(missing_source_ids),
                ", ".join(missing_source_ids),
            )

        LOGGER.info(
            "Collected %s candidate paper(s) for announcement date %s across categories %s.",
            len(combined),
            announcement_date.isoformat(),
            ", ".join(preferences.categories),
        )
        return combined
