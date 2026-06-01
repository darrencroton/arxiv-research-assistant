"""arXiv paper fetching and announcement-day candidate collection for re-ass."""

from __future__ import annotations

from datetime import date, datetime, timezone
from html import unescape
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
_CATEGORY_CODE_RE = re.compile(r"\((?P<code>[A-Za-z0-9.-]+)\)")
_RECENT_PAGE_SIZE = 2000
_SUBMITTED_DATE_RE = re.compile(r"\[Submitted on (?P<label>\d{1,2} [A-Za-z]{3} \d{4})")
_WHITESPACE_RE = re.compile(r"\s+")


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


def _class_tokens(value: str | None) -> set[str]:
    if value is None:
        return set()
    return {token for token in value.split() if token}


def _clean_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", unescape(value)).strip()


def _strip_descriptor(value: str, descriptor: str) -> str:
    text = _clean_text(value)
    prefix = f"{descriptor}:"
    if text.lower().startswith(prefix.lower()):
        return text[len(prefix) :].strip()
    return text


def _parse_published_datetime(citation_date: str | None, dateline_text: str) -> datetime:
    if citation_date:
        return datetime.strptime(citation_date, "%Y/%m/%d").replace(tzinfo=timezone.utc)
    match = _SUBMITTED_DATE_RE.search(dateline_text)
    if match is None:
        raise ValueError("Could not determine paper submission date from abstract page.")
    return datetime.strptime(match.group("label"), "%d %b %Y").replace(tzinfo=timezone.utc)


def _normalize_author_name(name: str) -> str:
    """Convert 'Last, First [Middle]' from citation_author meta tags to 'First [Middle] Last'."""
    if "," not in name:
        return name
    last, _, rest = name.partition(",")
    first_parts = rest.strip()
    return f"{first_parts} {last.strip()}" if first_parts else last.strip()


def _extract_category_codes(value: str) -> tuple[str, ...]:
    codes: list[str] = []
    seen_codes: set[str] = set()
    for match in _CATEGORY_CODE_RE.finditer(value):
        code = match.group("code")
        if code in seen_codes:
            continue
        seen_codes.add(code)
        codes.append(code)
    return tuple(codes)


class _AbstractPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.citation_title = ""
        self.citation_authors: list[str] = []
        self.citation_abstract = ""
        self.citation_date = ""
        self.dateline_chunks: list[str] = []
        self.title_chunks: list[str] = []
        self.abstract_chunks: list[str] = []
        self.subject_chunks: list[str] = []
        self.primary_subject_chunks: list[str] = []
        self._in_dateline = False
        self._in_title = False
        self._in_abstract = False
        self._in_subjects = False
        self._in_primary_subject = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        classes = _class_tokens(attrs_map.get("class"))

        if tag == "meta":
            name = attrs_map.get("name")
            content = attrs_map.get("content") or ""
            if name == "citation_title":
                self.citation_title = content
            elif name == "citation_author":
                self.citation_authors.append(content)
            elif name == "citation_abstract":
                self.citation_abstract = content
            elif name == "citation_date":
                self.citation_date = content
            return

        if tag == "div" and "dateline" in classes:
            self._in_dateline = True
            return
        if tag == "h1" and "title" in classes:
            self._in_title = True
            return
        if tag == "blockquote" and "abstract" in classes:
            self._in_abstract = True
            return
        if tag == "td" and "subjects" in classes:
            self._in_subjects = True
            return
        if tag == "span" and "primary-subject" in classes:
            self._in_primary_subject = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_dateline:
            self._in_dateline = False
            return
        if tag == "h1" and self._in_title:
            self._in_title = False
            return
        if tag == "blockquote" and self._in_abstract:
            self._in_abstract = False
            return
        if tag == "td" and self._in_subjects:
            self._in_subjects = False
            return
        if tag == "span" and self._in_primary_subject:
            self._in_primary_subject = False

    def handle_data(self, data: str) -> None:
        if self._in_dateline:
            self.dateline_chunks.append(data)
        if self._in_title:
            self.title_chunks.append(data)
        if self._in_abstract:
            self.abstract_chunks.append(data)
        if self._in_subjects:
            self.subject_chunks.append(data)
        if self._in_primary_subject:
            self.primary_subject_chunks.append(data)

    def paper(self, source_id: str) -> ArxivPaper:
        title = _clean_text(self.citation_title) or _strip_descriptor("".join(self.title_chunks), "Title")
        if not title:
            raise ValueError(f"Abstract page for {source_id} is missing a title.")

        authors = tuple(
            _normalize_author_name(_clean_text(author))
            for author in self.citation_authors
            if _clean_text(author)
        )
        if not authors:
            raise ValueError(f"Abstract page for {source_id} is missing authors.")

        summary = _clean_text(self.citation_abstract) or _strip_descriptor("".join(self.abstract_chunks), "Abstract")
        if not summary:
            raise ValueError(f"Abstract page for {source_id} is missing an abstract.")

        subject_text = _clean_text("".join(self.subject_chunks))
        primary_subject_text = _clean_text("".join(self.primary_subject_chunks))
        categories = _extract_category_codes(subject_text)
        primary_categories = _extract_category_codes(primary_subject_text)
        primary_category = primary_categories[0] if primary_categories else (categories[0] if categories else "")
        if not primary_category:
            raise ValueError(f"Abstract page for {source_id} is missing category metadata.")
        if not categories:
            categories = (primary_category,)

        published = _parse_published_datetime(self.citation_date, _clean_text("".join(self.dateline_chunks)))
        entry_id = f"https://arxiv.org/abs/{source_id}"
        return ArxivPaper(
            title=title,
            summary=summary,
            arxiv_url=entry_id,
            entry_id=entry_id,
            authors=authors,
            primary_category=primary_category,
            categories=categories,
            published=published,
            updated=None,
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
        abstract_fetcher: Any | None = None,
    ) -> None:
        self.page_size = max(1, min(page_size, 100))
        self.client = client or arxiv.Client(page_size=self.page_size, num_retries=3, delay_seconds=3)
        self._listing_fetcher = listing_fetcher or self._fetch_listing_html
        self._abstract_fetcher = abstract_fetcher or self._fetch_abstract_html
        self._listing_cache: dict[str, dict[date, list[str]]] = {}

    def _fetch_listing_html(self, category: str) -> str:
        url = f"https://arxiv.org/list/{category}/pastweek?show={_RECENT_PAGE_SIZE}"
        request = Request(url, headers={"User-Agent": "re-ass/1.0"})
        with urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")

    def _fetch_abstract_html(self, source_id: str) -> str:
        url = f"https://arxiv.org/abs/{source_id}"
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

    def _collect_candidates_from_api(self, source_ids: list[str]) -> dict[str, ArxivPaper]:
        search = arxiv.Search(id_list=source_ids, max_results=len(source_ids))
        results_by_id: dict[str, ArxivPaper] = {}
        for result in self.client.results(search):
            paper = _to_paper(result)
            try:
                identity = derive_identity(paper)
            except ValueError as error:
                LOGGER.warning("Skipping paper with invalid arXiv identity (%s): %s", paper.title, error)
                continue
            results_by_id[identity.source_id] = paper
        return results_by_id

    def _collect_candidates_from_abstract_pages(self, source_ids: list[str]) -> dict[str, ArxivPaper]:
        results_by_id: dict[str, ArxivPaper] = {}
        for source_id in source_ids:
            try:
                parser = _AbstractPageParser()
                parser.feed(self._abstract_fetcher(source_id))
                paper = parser.paper(source_id)
                identity = derive_identity(paper)
            except Exception as error:
                LOGGER.warning("Skipping paper %s after abstract-page fallback failed: %s", source_id, error)
                continue
            results_by_id[identity.source_id] = paper
        return results_by_id

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

        try:
            results_by_id = self._collect_candidates_from_api(pending_source_ids)
        except arxiv.HTTPError as error:
            if error.status != 429:
                raise
            LOGGER.warning(
                "arXiv export API returned HTTP 429 for %s candidate(s); falling back to abstract-page parsing.",
                len(pending_source_ids),
            )
            results_by_id = self._collect_candidates_from_abstract_pages(pending_source_ids)

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
