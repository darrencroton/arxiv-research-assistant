"""arXiv announcement listings (RSS feed, with the recent-listing page as gap
fill) and per-paper metadata collection for re-ass."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time as clock_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import http.client
import logging
import time
from typing import Any
import re
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo
import zlib

import arxiv

from re_ass.arxiv_rate_limit import (
    DEFAULT_HEADERS,
    ArxivRateLimiter,
    RETRY_DELAYS_SECONDS,
    decode_response_text,
    describe_http_error,
    fetch_arxiv_url,
    get_shared_limiter,
    is_transient_http_status,
)
from re_ass.models import ArxivPaper, PreferenceConfig
from re_ass.paper_identity import derive_identity, extract_source_id


LOGGER = logging.getLogger(__name__)
_ANNOUNCEMENT_HEADING_RE = re.compile(r"^(?P<label>[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4})")
_CATEGORY_CODE_RE = re.compile(r"\((?P<code>[A-Za-z0-9.-]+)\)")
_RECENT_PAGE_SIZE = 2000
# export.arxiv.org is arXiv's programmatic-access host, so it is tried first; arxiv.org is the
# second host. Either host's /list page can be served from a CDN cache entry that predates
# later announcements (export was observed at Age 512,239 s, 6 days, with no Cache-Control),
# so staleness is judged on content, in _fetch_recent_listing: the newest day shown against
# the days needed and the expected newest day. The Age header is no guide on its own: over a
# weekend a correct page is legitimately 2-3 days old. (The 406s seen since Sep 2026 were the
# CDN refusing urllib, which the curl transport in arxiv_rate_limit avoids.)
_RECENT_LISTING_HOSTS = ("export.arxiv.org", "arxiv.org")
# arXiv announces day D at 20:00 US Eastern on the evening before D.
_ANNOUNCEMENT_ZONE = ZoneInfo("America/New_York")
_ANNOUNCEMENT_TIME = clock_time(20, 0)
_RSS_ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_RSS_LISTED_ANNOUNCE_TYPES = frozenset({"new", "cross"})
_SUBMITTED_DATE_RE = re.compile(r"\[Submitted on (?P<label>\d{1,2} [A-Za-z]{3} \d{4})")
_WHITESPACE_RE = re.compile(r"\s+")

# What self._fetch_text (fetch_arxiv_url + decode_response_text) can raise:
# HTTPError (non-2xx) and ArxivTransferError (curl failure or timeout) are
# URLError/OSError subclasses so they're covered, as are the urllib fallback's
# socket errors; gzip.BadGzipFile is an OSError subclass; UnicodeDecodeError
# is a ValueError subclass; zlib.error is the one
# decompression error outside that hierarchy; http.client.HTTPException is
# listed explicitly because it is not an OSError subclass and the urllib
# fallback's http.client machinery can raise it on a malformed response.
_ARXIV_SOURCE_ERRORS = (URLError, OSError, ValueError, zlib.error, http.client.HTTPException)


def _rss_feed_url(categories: tuple[str, ...]) -> str:
    return f"https://rss.arxiv.org/rss/{'+'.join(categories)}"


def _recent_listing_url(category: str, host: str) -> str:
    return f"https://{host}/list/{category}/pastweek?show={_RECENT_PAGE_SIZE}"


def parse_rss_listing(xml_text: str, categories: tuple[str, ...]) -> dict[str, dict[date, list[str]]]:
    """Per-category announcement listing from an arXiv RSS feed (new and cross items only).

    Raises xml.etree.ElementTree.ParseError on malformed XML.
    Returns an entry for every requested category, empty when the feed lists nothing for it.
    """
    requested = set(categories)
    listings: dict[str, dict[date, list[str]]] = {category: {} for category in categories}
    seen_ids: dict[str, dict[date, set[str]]] = {category: {} for category in categories}
    skipped_count = 0

    root = ET.fromstring(xml_text)
    for item in root.iterfind("./channel/item"):
        announce_type = (item.findtext(f"{_RSS_ARXIV_NS}announce_type") or "").strip()
        if announce_type not in _RSS_LISTED_ANNOUNCE_TYPES:
            continue

        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not link or not pub_date:
            skipped_count += 1
            continue
        try:
            source_id = extract_source_id(link)
            announcement_date = parsedate_to_datetime(pub_date).date()
        except ValueError:
            skipped_count += 1
            continue

        for category_element in item.findall("category"):
            category = (category_element.text or "").strip()
            if category not in requested:
                continue
            day_seen = seen_ids[category].setdefault(announcement_date, set())
            if source_id in day_seen:
                continue
            listings[category].setdefault(announcement_date, []).append(source_id)
            day_seen.add(source_id)

    if skipped_count:
        LOGGER.warning(
            "Skipped %d new/cross RSS item(s) with a missing or unparsable link or pubDate.",
            skipped_count,
        )
    return listings


def _merge_listing(base: dict[date, list[str]], extra: dict[date, list[str]]) -> dict[date, list[str]]:
    """Union two per-date id listings: base order first, extra ids appended if unseen."""
    merged = {day: list(ids) for day, ids in base.items()}
    for day, ids in extra.items():
        existing = merged.setdefault(day, [])
        seen = set(existing)
        for source_id in ids:
            if source_id not in seen:
                existing.append(source_id)
                seen.add(source_id)
    return merged


def _announcement_instant(announcement_date: date) -> datetime:
    """When announcement_date's listing went out: 20:00 US Eastern the evening before."""
    return datetime.combine(
        announcement_date - timedelta(days=1), _ANNOUNCEMENT_TIME, tzinfo=_ANNOUNCEMENT_ZONE
    )


def _cached_at(response: Any, fetched_at: datetime) -> datetime | None:
    """When the CDN cached this response: fetched_at minus its Age header.

    None when Age is missing or malformed. arXiv's CDN sends Age on every
    response, even a cache miss (Age 0), so a missing one is unknown
    freshness, not a live fetch; the Date header is no substitute, since the
    CDN stamps it with the current time even on a days-old copy.
    """
    try:
        age = int(response.getheader("Age"))
    except (TypeError, ValueError):
        return None
    return fetched_at - timedelta(seconds=max(age, 0))


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
        feed_fetcher: Any | None = None,
        rate_limiter: ArxivRateLimiter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.page_size = max(1, min(page_size, 100))
        self.client = client or arxiv.Client(page_size=self.page_size, num_retries=3, delay_seconds=3)
        self._listing_fetcher = listing_fetcher or self._fetch_listing_html
        self._abstract_fetcher = abstract_fetcher or self._fetch_abstract_html
        self._feed_fetcher = feed_fetcher or self._fetch_rss_xml
        self._listing_cache: dict[str, dict[date, list[str]]] = {}
        self._rate_limiter = rate_limiter or get_shared_limiter()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # When each (category, host) /list page the default fetcher returned was
        # cached by arXiv's CDN (None: unknown). Absent for injected fetchers,
        # which are treated as live.
        self._listing_cached_at: dict[tuple[str, str], datetime | None] = {}

    def _fetch_text(self, url: str, *, label: str) -> str:
        return self._fetch_text_and_response(url, label=label)[0]

    def _fetch_text_and_response(self, url: str, *, label: str) -> tuple[str, Any]:
        """Shared retry loop for arXiv HTML/XML GETs; returns the text and the response.

        Waits on the shared crawl-delay limiter, fetches with
        fetch_arxiv_url (curl) sending DEFAULT_HEADERS, and decodes the
        response body. On a transient HTTP status it logs a WARNING and
        sleeps per RETRY_DELAYS_SECONDS; the HTTPError is re-raised once that
        schedule is exhausted or immediately for a non-transient status.
        Severity of the eventual failure is the caller's call, not logged
        here.
        """
        delays = list(RETRY_DELAYS_SECONDS)
        for attempt, delay in enumerate(delays + [None], start=1):
            self._rate_limiter.wait_for_crawl_delay()
            try:
                # Every attempt counts against the crawl delay, including one
                # that fails below the HTTP level (curl error or timeout).
                try:
                    response = fetch_arxiv_url(url, headers=DEFAULT_HEADERS, timeout=60)
                finally:
                    self._rate_limiter.mark_request_completed()
                text = decode_response_text(response)
            except HTTPError as exc:
                detail = describe_http_error(exc)
                if not is_transient_http_status(exc.code) or delay is None:
                    raise
                LOGGER.warning(
                    "arXiv fetch of %s returned HTTP %s (attempt %d/%d); retrying in %ds. %s",
                    label, exc.code, attempt, len(delays) + 1, delay, detail,
                )
                time.sleep(delay)
            else:
                return text, response
        raise RuntimeError("unreachable")

    def _fetch_listing_html(self, category: str, host: str) -> str:
        text, response = self._fetch_text_and_response(
            _recent_listing_url(category, host),
            label=f"recent listing for {category} from {host}",
        )
        self._listing_cached_at[(category, host)] = _cached_at(response, self._clock())
        return text

    def _fetch_rss_xml(self, categories: tuple[str, ...]) -> str:
        return self._fetch_text(_rss_feed_url(categories), label=f"announcement feed for {'+'.join(categories)}")

    def _fetch_abstract_html(self, source_id: str) -> str:
        # export.arxiv.org mirrors individual /abs pages promptly and is arXiv's site
        # "specifically set aside for programmatic access" (see AGENTS.md).
        url = f"https://export.arxiv.org/abs/{source_id}"
        self._rate_limiter.wait_for_crawl_delay()
        try:
            return decode_response_text(fetch_arxiv_url(url, headers=DEFAULT_HEADERS, timeout=60))
        finally:
            self._rate_limiter.mark_request_completed()

    def _category_listing(self, category: str) -> dict[date, list[str]]:
        return self._listing_cache.get(category, {})

    def load_announcement_feed(self, categories: tuple[str, ...]) -> tuple[date, ...]:
        """Fetch the RSS feed once for all categories and seed the listing cache.

        Returns the announcement dates the feed lists, or () when the feed is
        unusable (HTTP/network error, malformed XML, or no new/cross items);
        each unusable case is logged at WARNING with the feed URL. Never
        raises for feed problems.
        """
        feed_url = _rss_feed_url(categories)
        try:
            xml_text = self._feed_fetcher(categories)
            listing = parse_rss_listing(xml_text, categories)
        except _ARXIV_SOURCE_ERRORS as error:
            LOGGER.warning("arXiv announcement feed %s was unusable: %s", feed_url, error)
            return ()
        except ET.ParseError as error:
            LOGGER.warning("arXiv announcement feed %s returned malformed XML: %s", feed_url, error)
            return ()

        dates: set[date] = set()
        for category, day_to_ids in listing.items():
            self._listing_cache[category] = _merge_listing(self._listing_cache.get(category, {}), day_to_ids)
            dates.update(day_to_ids)

        if not dates:
            LOGGER.warning("arXiv announcement feed %s listed no new or cross items.", feed_url)
            return ()
        return tuple(sorted(dates))

    def seed_listings(self, listings: dict[str, dict[date, list[str]]]) -> None:
        """Merge previously saved per-category listings (snapshots) into the cache."""
        for category, day_to_ids in listings.items():
            self._listing_cache[category] = _merge_listing(self._listing_cache.get(category, {}), day_to_ids)

    def listing_for_day(self, categories: tuple[str, ...], announcement_date: date) -> dict[str, list[str]]:
        """Cached ids per category for one announcement day; [] for a category that listed nothing."""
        return {
            category: list(self._category_listing(category).get(announcement_date, []))
            for category in categories
        }

    def _fetch_recent_listing(
        self,
        category: str,
        required_dates: tuple[date, ...],
        expected_latest: date | None = None,
    ) -> dict[date, list[str]] | None:
        """First acceptable /list page for one category across the hosts, or None.

        A page is acceptable when it fetches, parses to at least one
        announcement day, and (given required_dates) its newest day is not
        older than the newest date needed, i.e. the mirror is not stale. The
        test is the newest listed day rather than presence of a needed day
        because a category can legitimately have no papers on a day.

        expected_latest is a soft freshness hint: the announcement day the
        caller expects to be the newest by now. A page older than it moves on
        to the next host. Whether it may still be used depends on when the CDN
        cached it (fetch time minus its Age header): a copy cached before
        expected_latest was announced is stale and is never used, since it
        cannot say whether this category had papers that day; one cached after
        it is authoritative that the category listed nothing that day (or that
        it was an arXiv holiday), so the freshest such page is used and the
        caller is left to warn if every category is behind. With no such page
        the category has no acceptable host.
        """
        freshest: dict[date, list[str]] | None = None
        for host in _RECENT_LISTING_HOSTS:
            url = _recent_listing_url(category, host)
            self._listing_cached_at.pop((category, host), None)
            try:
                parser = _AnnouncementListingParser()
                parser.feed(self._listing_fetcher(category, host))
                listing = {day: list(ids) for day, ids in parser.day_to_ids.items()}
            except _ARXIV_SOURCE_ERRORS as error:
                LOGGER.warning("Recent-listing fallback failed for %s (%s): %s", category, url, error)
                continue
            if not listing:
                LOGGER.warning(
                    "Recent-listing fallback for %s (%s) parsed zero announcement days; treating as a failure.",
                    category,
                    url,
                )
                continue
            if required_dates and max(listing) < max(required_dates):
                LOGGER.warning(
                    "Recent-listing fallback for %s (%s) is stale: newest day shown is %s but %s is needed.",
                    category,
                    url,
                    max(listing).isoformat(),
                    max(required_dates).isoformat(),
                )
                continue
            if expected_latest is None or max(listing) >= expected_latest:
                return listing
            if (category, host) in self._listing_cached_at:
                cached_at = self._listing_cached_at[(category, host)]
                if cached_at is None or cached_at < _announcement_instant(expected_latest):
                    LOGGER.warning(
                        "Recent-listing fallback for %s (%s) is a cached copy from %s, which may predate "
                        "%s's announcement; treating it as stale.",
                        category,
                        url,
                        "an unknown time (no Age header)"
                        if cached_at is None
                        else cached_at.astimezone(timezone.utc).isoformat(timespec="minutes"),
                        expected_latest.isoformat(),
                    )
                    continue
            # INFO, not WARNING: a category can have no papers on a day, and the
            # pipeline warns once if the merged listing is still behind.
            LOGGER.info(
                "Recent-listing fallback for %s (%s) shows %s as its newest day, behind the expected %s.",
                category,
                url,
                max(listing).isoformat(),
                expected_latest.isoformat(),
            )
            if freshest is None or max(listing) > max(freshest):
                freshest = listing
        return freshest

    def load_recent_listings(
        self,
        categories: tuple[str, ...],
        required_dates: tuple[date, ...] = (),
        *,
        expected_latest: date | None = None,
    ) -> tuple[date, ...]:
        """Fetch /list/{category}/pastweek for every category and merge it into the cache.

        Each category tries export.arxiv.org then arxiv.org (see
        _fetch_recent_listing; required_dates are the days the caller needs,
        empty when none are known, and expected_latest is the soft freshness
        hint for the newest day). Returns the sorted announcement days the
        accepted pages showed, so the caller can tell which days the window
        covered. All-or-nothing across categories: if any category has no
        acceptable host, nothing is merged and () is returned (each rejected
        host is logged at WARNING with category and URL).
        """
        fetched: dict[str, dict[date, list[str]]] = {}
        for category in categories:
            listing = self._fetch_recent_listing(category, required_dates, expected_latest)
            if listing is None:
                return ()
            fetched[category] = listing

        self.seed_listings(fetched)
        return tuple(sorted({day for listing in fetched.values() for day in listing}))

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
            if error.status != 429 and error.status < 500:
                raise
            LOGGER.warning(
                "arXiv export API returned HTTP %s for %s candidate(s); falling back to abstract-page parsing.",
                error.status,
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
