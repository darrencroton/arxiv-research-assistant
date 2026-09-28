from datetime import date, datetime, timezone
import http.client
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
import zlib

import arxiv
import pytest

from re_ass.arxiv_fetcher import ArxivFetcher, _normalize_author_name, parse_rss_listing
from re_ass.arxiv_rate_limit import ArxivRateLimiter, get_shared_limiter
from re_ass.models import PreferenceConfig

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _rss_fixture() -> str:
    return (_FIXTURES_DIR / "rss-astro-ph-ga-2026-09-24.xml").read_text(encoding="utf-8")


def _listing_html(*, heading: str, ids: list[str]) -> str:
    blocks = []
    for source_id in ids:
        blocks.append(
            f"""
            <dt>
              <a href="/abs/{source_id}" title="Abstract" id="{source_id}">
                arXiv:{source_id}
              </a>
            </dt>
            """
        )
    joined = "\n".join(blocks)
    return f"<div id='dlpage'><dl id='articles'><h3>{heading}</h3>{joined}</dl></div>"


def _abstract_html(
    *,
    source_id: str,
    title: str,
    authors: tuple[str, ...],
    abstract: str,
    citation_date: str = "2026/03/24",
    primary_subject: str = "Artificial Intelligence (cs.AI)",
    subjects: str = "Artificial Intelligence (cs.AI); Computation and Language (cs.CL)",
) -> str:
    authors_meta = "".join(f'<meta name="citation_author" content="{author}" />' for author in authors)
    return (
        "<html><head>"
        f'<meta name="citation_title" content="{title}" />'
        f"{authors_meta}"
        f'<meta name="citation_date" content="{citation_date}" />'
        f'<meta name="citation_arxiv_id" content="{source_id}" />'
        f'<meta name="citation_abstract" content="{abstract}" />'
        "</head><body>"
        '<div class="dateline">[Submitted on 24 Mar 2026]</div>'
        '<table><tr><td class="tablecell subjects">'
        f'<span class="primary-subject">{primary_subject}</span>; {subjects.removeprefix(primary_subject + "; ")}'
        "</td></tr></table>"
        "</body></html>"
    )


class _FakeHtmlResponse:
    """Stands in for urlopen's context-managed response, serving fixed HTML."""

    def __init__(self, html: str) -> None:
        self._html = html

    def __enter__(self):
        return SimpleNamespace(
            read=lambda: self._html.encode("utf-8"),
            headers=SimpleNamespace(get=lambda *_args: None),
        )

    def __exit__(self, *args):
        return None


def test_available_announcement_dates_unions_configured_categories() -> None:
    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Mon, 23 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10021"]),
        "cs.CL": _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10022"]),
    }

    fetcher = ArxivFetcher(
        page_size=10,
        client=SimpleNamespace(results=lambda _search: []),
        listing_fetcher=lambda category, _host: listing_html_by_category[category],
    )

    fetcher.load_recent_listings(("cs.AI", "cs.CL"))

    assert fetcher.available_announcement_dates(("cs.AI", "cs.CL")) == (
        date(2026, 3, 23),
        date(2026, 3, 24),
    )


def test_collect_candidates_fetches_all_listing_ids_for_announcement_date() -> None:
    announcement_day = date(2026, 3, 24)
    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Tue, 24 Mar 2026 (showing 2 of 2 entries )", ids=["2603.10021", "2603.10022"]),
        "cs.CL": _listing_html(heading="Tue, 24 Mar 2026 (showing 2 of 2 entries )", ids=["2603.10022", "2603.10023"]),
    }
    results_by_id = {
        "2603.10021": SimpleNamespace(
            title="In Range One",
            summary="Agents and tools.",
            entry_id="https://arxiv.org/abs/2603.10021",
            authors=[SimpleNamespace(name="Test Author")],
            primary_category="cs.AI",
            categories=("cs.AI",),
            published=datetime(2026, 3, 24, 11, 0, tzinfo=timezone.utc),
            updated=datetime(2026, 3, 24, 11, 0, tzinfo=timezone.utc),
        ),
        "2603.10022": SimpleNamespace(
            title="In Range Two",
            summary="Language models.",
            entry_id="https://arxiv.org/abs/2603.10022",
            authors=[SimpleNamespace(name="Test Author")],
            primary_category="cs.CL",
            categories=("cs.CL",),
            published=datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc),
            updated=datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc),
        ),
        "2603.10023": SimpleNamespace(
            title="In Range Three",
            summary="Planning agents.",
            entry_id="https://arxiv.org/abs/2603.10023",
            authors=[SimpleNamespace(name="Test Author")],
            primary_category="cs.AI",
            categories=("cs.AI",),
            published=datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc),
            updated=datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc),
        ),
    }

    class FakeClient:
        def __init__(self) -> None:
            self.searches = []

        def results(self, search: object):
            self.searches.append(search)
            return [results_by_id[source_id] for source_id in search.id_list]

    client = FakeClient()
    fetcher = ArxivFetcher(
        page_size=10,
        client=client,
        listing_fetcher=lambda category, _host: listing_html_by_category[category],
    )

    fetcher.load_recent_listings(("cs.AI", "cs.CL"))
    papers = fetcher.collect_candidates(
        PreferenceConfig(priorities=("Agents",), categories=("cs.AI", "cs.CL")),
        announcement_date=announcement_day,
    )

    assert [paper.title for paper in papers] == ["In Range One", "In Range Two", "In Range Three"]
    assert client.searches[0].id_list == ["2603.10021", "2603.10022", "2603.10023"]


def test_collect_candidates_skips_completed_paper_keys_before_metadata_fetch() -> None:
    announcement_day = date(2026, 3, 24)
    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Tue, 24 Mar 2026 (showing 2 of 2 entries )", ids=["2603.10031", "2603.10032"]),
    }
    results_by_id = {
        "2603.10032": SimpleNamespace(
            title="Fresh Agents Paper",
            summary="Agents and execution.",
            entry_id="https://arxiv.org/abs/2603.10032",
            authors=[SimpleNamespace(name="Author Two")],
            primary_category="cs.AI",
            categories=("cs.AI",),
            published=datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc),
            updated=datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc),
        ),
    }

    class FakeClient:
        def __init__(self) -> None:
            self.searches = []

        def results(self, search: object):
            self.searches.append(search)
            return [results_by_id[source_id] for source_id in search.id_list]

    client = FakeClient()
    fetcher = ArxivFetcher(
        page_size=10,
        client=client,
        listing_fetcher=lambda category, _host: listing_html_by_category[category],
    )

    fetcher.load_recent_listings(("cs.AI",))
    papers = fetcher.collect_candidates(
        PreferenceConfig(priorities=("Agents",), categories=("cs.AI",)),
        announcement_date=announcement_day,
        excluded_paper_keys={"arxiv:2603.10031"},
    )

    assert [paper.title for paper in papers] == ["Fresh Agents Paper"]
    assert client.searches[0].id_list == ["2603.10032"]


def test_collect_candidates_returns_empty_when_all_listing_ids_are_already_completed() -> None:
    announcement_day = date(2026, 3, 24)
    fetcher = ArxivFetcher(
        page_size=10,
        client=SimpleNamespace(results=lambda _search: (_ for _ in ()).throw(AssertionError("API should not be called"))),
        listing_fetcher=lambda _category, _host: _listing_html(
            heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )",
            ids=["2603.10040"],
        ),
    )

    fetcher.load_recent_listings(("cs.AI",))
    papers = fetcher.collect_candidates(
        PreferenceConfig(priorities=("Agents",), categories=("cs.AI",)),
        announcement_date=announcement_day,
        excluded_paper_keys={"arxiv:2603.10040"},
    )

    assert papers == []


def test_collect_candidates_raises_for_announcement_date_outside_visible_listing() -> None:
    fetcher = ArxivFetcher(
        page_size=10,
        client=SimpleNamespace(results=lambda _search: []),
        listing_fetcher=lambda _category, _host: _listing_html(
            heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )",
            ids=["2603.10050"],
        ),
    )

    fetcher.load_recent_listings(("cs.AI",))
    try:
        fetcher.collect_candidates(
            PreferenceConfig(priorities=("Agents",), categories=("cs.AI",)),
            announcement_date=date(2026, 3, 25),
        )
    except ValueError as error:
        assert "2026-03-25" in str(error)
    else:
        raise AssertionError("Expected collect_candidates to reject an unavailable announcement date.")


def test_collect_candidates_falls_back_to_abstract_pages_on_export_api_429() -> None:
    announcement_day = date(2026, 3, 24)
    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Tue, 24 Mar 2026 (showing 2 of 2 entries )", ids=["2603.10021", "2603.10022"]),
        "cs.CL": _listing_html(heading="Tue, 24 Mar 2026 (showing 2 of 2 entries )", ids=["2603.10022", "2603.10023"]),
    }
    abstract_html_by_id = {
        "2603.10021": _abstract_html(
            source_id="2603.10021",
            title="Fallback One",
            authors=("Test Author",),
            abstract="Fallback abstract one.",
        ),
        "2603.10022": _abstract_html(
            source_id="2603.10022",
            title="Fallback Two",
            authors=("Author Two",),
            abstract="Fallback abstract two.",
            primary_subject="Computation and Language (cs.CL)",
            subjects="Computation and Language (cs.CL)",
        ),
        "2603.10023": _abstract_html(
            source_id="2603.10023",
            title="Fallback Three",
            authors=("Author Three",),
            abstract="Fallback abstract three.",
        ),
    }

    class FailingClient:
        def __init__(self) -> None:
            self.searches = []

        def results(self, search: object):
            self.searches.append(search)
            raise arxiv.HTTPError("https://export.arxiv.org/api/query?id_list=2603.10021", 0, 429)

    client = FailingClient()
    fetcher = ArxivFetcher(
        page_size=10,
        client=client,
        listing_fetcher=lambda category, _host: listing_html_by_category[category],
        abstract_fetcher=lambda source_id: abstract_html_by_id[source_id],
    )

    fetcher.load_recent_listings(("cs.AI", "cs.CL"))
    papers = fetcher.collect_candidates(
        PreferenceConfig(priorities=("Agents",), categories=("cs.AI", "cs.CL")),
        announcement_date=announcement_day,
    )

    assert [paper.title for paper in papers] == ["Fallback One", "Fallback Two", "Fallback Three"]
    assert papers[0].summary == "Fallback abstract one."
    assert papers[1].primary_category == "cs.CL"
    assert papers[2].categories == ("cs.AI", "cs.CL")
    assert client.searches[0].id_list == ["2603.10021", "2603.10022", "2603.10023"]


def test_normalize_author_name_converts_last_comma_first_to_first_last() -> None:
    assert _normalize_author_name("Meyer, R. A.") == "R. A. Meyer"
    assert _normalize_author_name("Leethochawalit, Natalie") == "Natalie Leethochawalit"
    assert _normalize_author_name("Yu, Si-Yue") == "Si-Yue Yu"
    assert _normalize_author_name("Chandro-Gómez, Ángel") == "Ángel Chandro-Gómez"


def test_normalize_author_name_leaves_first_last_format_unchanged() -> None:
    assert _normalize_author_name("Natalie Leethochawalit") == "Natalie Leethochawalit"
    assert _normalize_author_name("Euclid Collaboration") == "Euclid Collaboration"
    assert _normalize_author_name("F. Valentino") == "F. Valentino"


def test_collect_candidates_fallback_normalizes_author_names() -> None:
    announcement_day = date(2026, 3, 24)
    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10060"]),
    }
    abstract_html_by_id = {
        "2603.10060": _abstract_html(
            source_id="2603.10060",
            title="Author Name Test",
            authors=("Meyer, R. A.", "Oesch, P. A.", "Yu, Si-Yue"),
            abstract="Testing author normalization.",
        ),
    }

    class FailingClient:
        def results(self, search: object):
            raise arxiv.HTTPError("https://export.arxiv.org/api/query", 0, 429)

    fetcher = ArxivFetcher(
        page_size=10,
        client=FailingClient(),
        listing_fetcher=lambda category, _host: listing_html_by_category[category],
        abstract_fetcher=lambda source_id: abstract_html_by_id[source_id],
    )

    fetcher.load_recent_listings(("cs.AI",))
    papers = fetcher.collect_candidates(
        PreferenceConfig(priorities=("Agents",), categories=("cs.AI",)),
        announcement_date=announcement_day,
    )

    assert len(papers) == 1
    assert papers[0].authors == ("R. A. Meyer", "P. A. Oesch", "Si-Yue Yu")


def test_collect_candidates_falls_back_to_abstract_pages_on_export_api_503() -> None:
    announcement_day = date(2026, 3, 24)
    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10050"]),
    }
    abstract_html_by_id = {
        "2603.10050": _abstract_html(
            source_id="2603.10050",
            title="Service Unavailable Fallback",
            authors=("Test Author",),
            abstract="Fallback abstract for a transient 503.",
        ),
    }

    class FailingClient:
        def results(self, search: object):
            raise arxiv.HTTPError("https://export.arxiv.org/api/query?id_list=2603.10050", 0, 503)

    fetcher = ArxivFetcher(
        page_size=10,
        client=FailingClient(),
        listing_fetcher=lambda category, _host: listing_html_by_category[category],
        abstract_fetcher=lambda source_id: abstract_html_by_id[source_id],
    )

    fetcher.load_recent_listings(("cs.AI",))
    papers = fetcher.collect_candidates(
        PreferenceConfig(priorities=("Agents",), categories=("cs.AI",)),
        announcement_date=announcement_day,
    )

    assert [paper.title for paper in papers] == ["Service Unavailable Fallback"]


def test_collect_candidates_reraises_non_429_client_export_errors() -> None:
    fetcher = ArxivFetcher(
        page_size=10,
        client=SimpleNamespace(results=lambda _search: (_ for _ in ()).throw(arxiv.HTTPError("https://export.arxiv.org/api/query", 0, 404))),
        listing_fetcher=lambda _category, _host: _listing_html(
            heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )",
            ids=["2603.10050"],
        ),
        abstract_fetcher=lambda _source_id: (_ for _ in ()).throw(AssertionError("Fallback should not be used")),
    )

    fetcher.load_recent_listings(("cs.AI",))
    try:
        fetcher.collect_candidates(
            PreferenceConfig(priorities=("Agents",), categories=("cs.AI",)),
            announcement_date=date(2026, 3, 24),
        )
    except arxiv.HTTPError as error:
        assert error.status == 404
    else:
        raise AssertionError("Expected non-429, non-5xx export errors to propagate.")


def test_arxiv_fetcher_defaults_to_the_shared_rate_limiter() -> None:
    fetcher = ArxivFetcher(page_size=10, client=SimpleNamespace(results=lambda _search: []))

    assert fetcher._rate_limiter is get_shared_limiter()


def test_fetch_listing_html_retries_on_406_then_succeeds(monkeypatch) -> None:
    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    sleeps: list[float] = []
    fake_now = [0.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr(arxiv_fetcher_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(arxiv_fetcher_module.time, "monotonic", lambda: fake_now[0])

    listing_html = _listing_html(
        heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )",
        ids=["2603.10050"],
    )
    responses = iter([
        HTTPError("https://arxiv.org/list/cs.AI/pastweek", 406, "Not Acceptable", None, None),
        _FakeHtmlResponse(listing_html),
    ])

    def fake_urlopen(_request, timeout):
        next_response = next(responses)
        if isinstance(next_response, HTTPError):
            raise next_response
        return next_response

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())
    ok = fetcher.load_recent_listings(("cs.AI",))

    assert ok == (date(2026, 3, 24),)
    assert fetcher._category_listing("cs.AI") == {date(2026, 3, 24): ["2603.10050"]}
    assert sleeps == [15]


def test_fetch_listing_html_logs_response_detail_on_406(monkeypatch, caplog) -> None:
    import io

    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    monkeypatch.setattr(arxiv_fetcher_module.time, "sleep", lambda _seconds: None)

    def fake_urlopen(_request, timeout):
        raise HTTPError(
            "https://arxiv.org/list/cs.AI/pastweek",
            406,
            "Not Acceptable",
            {"Retry-After": "300"},
            io.BytesIO(b"Automated requests are not permitted."),
        )

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())

    with caplog.at_level("WARNING"):
        ok = fetcher.load_recent_listings(("cs.AI",))

    assert ok == ()
    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert "Retry-After=300" in combined
    assert "Automated requests are not permitted." in combined


def test_load_recent_listings_returns_false_on_non_transient_error(monkeypatch, caplog) -> None:
    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    sleeps: list[float] = []
    fake_now = [0.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr(arxiv_fetcher_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(arxiv_fetcher_module.time, "monotonic", lambda: fake_now[0])
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        raise HTTPError(request.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())

    with caplog.at_level("WARNING"):
        ok = fetcher.load_recent_listings(("cs.AI",))

    assert ok == ()
    # One attempt per host, no retries: the only wait is the crawl delay between them.
    assert requested_urls == [
        "https://export.arxiv.org/list/cs.AI/pastweek?show=2000",
        "https://arxiv.org/list/cs.AI/pastweek?show=2000",
    ]
    assert sleeps == [15]
    assert any("cs.AI" in record.getMessage() for record in caplog.records)


def test_fetch_listing_html_sends_default_headers(monkeypatch) -> None:
    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    listing_html = _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10050"])
    captured_requests = []

    def fake_urlopen(request, timeout):
        captured_requests.append(request)
        return _FakeHtmlResponse(listing_html)

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())
    fetcher.load_recent_listings(("cs.AI",))

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.full_url == "https://export.arxiv.org/list/cs.AI/pastweek?show=2000"
    # Request.add_header() stores keys via str.capitalize() (e.g. "Accept-encoding"),
    # and get_header() does a literal lookup rather than re-normalizing the name.
    assert request.get_header("Accept") is not None
    assert request.get_header("Accept-encoding") == "gzip, deflate"
    assert request.get_header("Accept-language") is not None


def test_load_recent_listings_returns_false_after_exhausting_retries_on_406(monkeypatch) -> None:
    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    sleeps: list[float] = []
    fake_now = [0.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr(arxiv_fetcher_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(arxiv_fetcher_module.time, "monotonic", lambda: fake_now[0])

    def fake_urlopen(_request, timeout):
        raise HTTPError("https://arxiv.org/list/cs.AI/pastweek", 406, "Not Acceptable", None, None)

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())
    ok = fetcher.load_recent_listings(("cs.AI",))

    assert ok == ()
    # Each host exhausts the full retry schedule before the next is tried.
    assert sleeps.count(30) == 2
    assert sleeps.count(90) == 2


def test_fetch_abstract_html_requests_export_arxiv_org(monkeypatch) -> None:
    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    requested_urls: list[str] = []
    abstract_html = _abstract_html(
        source_id="2603.10050",
        title="Example Paper",
        authors=("Doe, J.",),
        abstract="An example abstract.",
    )

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        return _FakeHtmlResponse(abstract_html)

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())
    fetcher._fetch_abstract_html("2603.10050")

    assert requested_urls == ["https://export.arxiv.org/abs/2603.10050"]


def test_load_recent_listings_respects_crawl_delay_between_categories(monkeypatch) -> None:
    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    sleeps: list[float] = []
    fake_now = [0.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr(arxiv_fetcher_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(arxiv_fetcher_module.time, "monotonic", lambda: fake_now[0])

    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10050"]),
        "cs.CL": _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10051"]),
    }

    def fake_urlopen(request, timeout):
        category = "cs.AI" if "cs.AI" in request.full_url else "cs.CL"
        return _FakeHtmlResponse(listing_html_by_category[category])

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())
    fetcher.load_recent_listings(("cs.AI", "cs.CL"))
    dates = fetcher.available_announcement_dates(("cs.AI", "cs.CL"))

    assert dates == (date(2026, 3, 24),)
    # No wait before the first request; a full 15s crawl-delay before the second.
    assert sleeps == [15]


def test_parse_rss_listing_lists_new_and_cross_items_per_category_and_date() -> None:
    listing = parse_rss_listing(_rss_fixture(), ("astro-ph.GA", "astro-ph.CO", "cs.AI"))

    assert listing == {
        "astro-ph.GA": {
            date(2026, 9, 24): ["2609.26877", "2609.26993", "2609.27048"],
        },
        "astro-ph.CO": {
            date(2026, 9, 24): ["2609.26993"],
        },
        "cs.AI": {},
    }


def test_parse_rss_listing_skips_items_missing_required_fields(caplog) -> None:
    xml_text = """<rss xmlns:arxiv="http://arxiv.org/schemas/atom">
<channel>
<item>
<link>  https://arxiv.org/abs/2609.00001  </link>
<pubDate> Thu, 24 Sep 2026 00:00:00 -0400 </pubDate>
<category> astro-ph.GA </category>
<arxiv:announce_type> new </arxiv:announce_type>
</item>
<item>
<pubDate>Thu, 24 Sep 2026 00:00:00 -0400</pubDate>
<category>astro-ph.GA</category>
<arxiv:announce_type>new</arxiv:announce_type>
</item>
<item>
<link>https://arxiv.org/abs/2609.00003</link>
<pubDate>not-a-real-date</pubDate>
<category>astro-ph.GA</category>
<arxiv:announce_type>cross</arxiv:announce_type>
</item>
</channel>
</rss>"""

    with caplog.at_level("WARNING"):
        listing = parse_rss_listing(xml_text, ("astro-ph.GA",))

    assert listing == {"astro-ph.GA": {date(2026, 9, 24): ["2609.00001"]}}
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Skipped 2" in warnings[0]


def test_parse_rss_listing_raises_on_malformed_xml() -> None:
    try:
        parse_rss_listing("<rss><channel><item></rss>", ("astro-ph.GA",))
    except ET.ParseError:
        pass
    else:
        raise AssertionError("Expected malformed XML to raise ET.ParseError.")


_MINIMAL_RSS_XML = """<rss xmlns:arxiv="http://arxiv.org/schemas/atom">
<channel>
<item>
<link>https://arxiv.org/abs/2609.30001</link>
<pubDate>Thu, 24 Sep 2026 00:00:00 -0400</pubDate>
<category>cs.AI</category>
<category>cs.CL</category>
<arxiv:announce_type>new</arxiv:announce_type>
</item>
</channel>
</rss>"""


def test_load_announcement_feed_requests_rss_url_through_limiter_with_default_headers(monkeypatch) -> None:
    import re_ass.arxiv_fetcher as arxiv_fetcher_module

    sleeps: list[float] = []
    fake_now = [0.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr(arxiv_fetcher_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(arxiv_fetcher_module.time, "monotonic", lambda: fake_now[0])

    captured_requests = []

    def fake_urlopen(request, timeout):
        captured_requests.append(request)
        return _FakeHtmlResponse(_MINIMAL_RSS_XML)

    monkeypatch.setattr(arxiv_fetcher_module, "urlopen", fake_urlopen)

    fetcher = ArxivFetcher(page_size=10, rate_limiter=ArxivRateLimiter())
    fetcher.load_announcement_feed(("cs.AI", "cs.CL"))
    fetcher.load_announcement_feed(("cs.AI", "cs.CL"))

    assert len(captured_requests) == 2
    request = captured_requests[0]
    assert request.full_url == "https://rss.arxiv.org/rss/cs.AI+cs.CL"
    assert request.get_header("Accept") is not None
    assert request.get_header("Accept-encoding") == "gzip, deflate"
    # A second feed load within the same fetcher pays the full crawl-delay.
    assert sleeps == [15]


@pytest.mark.parametrize(
    "feed_fetcher",
    [
        lambda categories: (_ for _ in ()).throw(
            HTTPError("https://rss.arxiv.org/rss/cs.AI", 406, "Not Acceptable", None, None)
        ),
        lambda categories: (_ for _ in ()).throw(zlib.error("bad zlib data")),
        lambda categories: (_ for _ in ()).throw(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")),
        lambda categories: (_ for _ in ()).throw(http.client.IncompleteRead(b"partial", 10)),
        lambda categories: "<rss><channel><item></rss>",
    ],
    ids=["http-error", "zlib-error", "unicode-decode-error", "incomplete-read", "malformed-xml"],
)
def test_load_announcement_feed_returns_empty_and_warns_on_source_error(feed_fetcher, caplog) -> None:
    fetcher = ArxivFetcher(page_size=10, feed_fetcher=feed_fetcher)

    with caplog.at_level("WARNING"):
        dates = fetcher.load_announcement_feed(("cs.AI",))

    assert dates == ()
    assert any("rss.arxiv.org/rss/cs.AI" in record.getMessage() for record in caplog.records)


def test_load_announcement_feed_returns_empty_when_feed_has_no_listed_items(caplog) -> None:
    xml_text = """<rss xmlns:arxiv="http://arxiv.org/schemas/atom">
<channel>
<item>
<link>https://arxiv.org/abs/2609.30002</link>
<pubDate>Thu, 24 Sep 2026 00:00:00 -0400</pubDate>
<category>cs.AI</category>
<arxiv:announce_type>replace</arxiv:announce_type>
</item>
</channel>
</rss>"""
    fetcher = ArxivFetcher(page_size=10, feed_fetcher=lambda categories: xml_text)

    with caplog.at_level("WARNING"):
        dates = fetcher.load_announcement_feed(("cs.AI",))

    assert dates == ()
    assert any("no new or cross" in record.getMessage() for record in caplog.records)


def test_load_recent_listings_merges_older_days_under_feed_day() -> None:
    feed_xml = """<rss xmlns:arxiv="http://arxiv.org/schemas/atom">
<channel>
<item>
<link>https://arxiv.org/abs/2609.30010</link>
<pubDate>Thu, 24 Sep 2026 00:00:00 -0400</pubDate>
<category>astro-ph.GA</category>
<arxiv:announce_type>new</arxiv:announce_type>
</item>
</channel>
</rss>"""
    listing_html = _listing_html(
        heading="Thu, 24 Sep 2026 (showing 2 of 2 entries )", ids=["2609.30010", "2609.30011"]
    ) + _listing_html(heading="Wed, 23 Sep 2026 (showing 1 of 1 entries )", ids=["2609.30099"])

    fetcher = ArxivFetcher(
        page_size=10,
        feed_fetcher=lambda categories: feed_xml,
        listing_fetcher=lambda category, _host: listing_html,
    )

    fetcher.load_announcement_feed(("astro-ph.GA",))
    ok = fetcher.load_recent_listings(("astro-ph.GA",))

    assert ok == (date(2026, 9, 23), date(2026, 9, 24))
    assert fetcher._category_listing("astro-ph.GA") == {
        date(2026, 9, 24): ["2609.30010", "2609.30011"],
        date(2026, 9, 23): ["2609.30099"],
    }


def test_load_recent_listings_is_all_or_nothing_when_one_category_fails() -> None:
    listing_html_ga = _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.40001"])

    def listing_fetcher(category, _host):
        if category == "astro-ph.GA":
            return listing_html_ga
        raise HTTPError("https://arxiv.org/list/astro-ph.CO/pastweek", 404, "Not Found", None, None)

    fetcher = ArxivFetcher(page_size=10, listing_fetcher=listing_fetcher)

    ok = fetcher.load_recent_listings(("astro-ph.GA", "astro-ph.CO"))

    assert ok == ()
    assert fetcher._category_listing("astro-ph.GA") == {}


def _hosted_listing_fetcher(pages_by_host: dict[str, str | Exception], requested_hosts: list[str]):
    """listing_fetcher fake serving one page (or raising one error) per host."""

    def listing_fetcher(_category, host):
        requested_hosts.append(host)
        page = pages_by_host[host]
        if isinstance(page, Exception):
            raise page
        return page

    return listing_fetcher


_LISTING_24 = _listing_html(heading="Thu, 24 Sep 2026 (showing 1 of 1 entries )", ids=["2609.30010"])
_LISTING_22 = _listing_html(heading="Tue, 22 Sep 2026 (showing 1 of 1 entries )", ids=["2609.30020"])


@pytest.mark.parametrize(
    "export_page,required_dates,expected_hosts,expected_ids",
    [
        (_LISTING_24, (date(2026, 9, 23),), ["export.arxiv.org"], "2609.30010"),
        (HTTPError("https://export.arxiv.org/list", 503, "Unavailable", None, None), (), ["export.arxiv.org", "arxiv.org"], "2609.30010"),
        ("<html>challenge</html>", (), ["export.arxiv.org", "arxiv.org"], "2609.30010"),
        (_LISTING_22, (date(2026, 9, 23),), ["export.arxiv.org", "arxiv.org"], "2609.30010"),
    ],
    ids=["export-accepted", "export-error", "export-zero-days", "export-stale"],
)
def test_load_recent_listings_prefers_export_host_and_falls_back_to_main_site(
    export_page, required_dates, expected_hosts, expected_ids, caplog
) -> None:
    requested_hosts: list[str] = []
    main_page = _LISTING_24
    pages = {"export.arxiv.org": export_page, "arxiv.org": main_page}
    fetcher = ArxivFetcher(page_size=10, listing_fetcher=_hosted_listing_fetcher(pages, requested_hosts))

    with caplog.at_level("WARNING"):
        ok = fetcher.load_recent_listings(("astro-ph.GA",), required_dates)

    assert ok == (date(2026, 9, 24),)
    assert requested_hosts == expected_hosts
    assert fetcher._category_listing("astro-ph.GA")[date(2026, 9, 24)] == [expected_ids]
    if len(expected_hosts) == 2:
        assert any("https://export.arxiv.org/list/astro-ph.GA" in record.getMessage() for record in caplog.records)


def test_load_recent_listings_merges_nothing_when_no_host_is_acceptable(caplog) -> None:
    requested_hosts: list[str] = []
    pages = {"export.arxiv.org": _LISTING_22, "arxiv.org": HTTPError("u", 406, "No", None, None)}
    fetcher = ArxivFetcher(page_size=10, listing_fetcher=_hosted_listing_fetcher(pages, requested_hosts))

    with caplog.at_level("WARNING"):
        ok = fetcher.load_recent_listings(("astro-ph.GA",), (date(2026, 9, 24),))

    assert ok == ()
    assert fetcher._category_listing("astro-ph.GA") == {}
    assert any("stale" in record.getMessage() and "2026-09-22" in record.getMessage() for record in caplog.records)


def test_seeded_listings_are_visible_and_readable_per_day() -> None:
    fetcher = ArxivFetcher(page_size=10)

    fetcher.seed_listings({"cs.AI": {date(2026, 9, 24): ["2609.00001"]}, "cs.CL": {}})

    assert fetcher.available_announcement_dates(("cs.AI", "cs.CL")) == (date(2026, 9, 24),)
    assert fetcher.listing_for_day(("cs.AI", "cs.CL"), date(2026, 9, 24)) == {"cs.AI": ["2609.00001"], "cs.CL": []}


def test_load_recent_listings_returns_false_on_incomplete_read(caplog) -> None:
    def listing_fetcher(_category, _host):
        raise http.client.IncompleteRead(b"partial", 10)

    fetcher = ArxivFetcher(page_size=10, listing_fetcher=listing_fetcher)

    with caplog.at_level("WARNING"):
        ok = fetcher.load_recent_listings(("astro-ph.GA",))

    assert ok == ()
    assert any("astro-ph.GA" in record.getMessage() for record in caplog.records)


def test_load_recent_listings_fails_when_a_category_parses_to_zero_announcement_days(caplog) -> None:
    challenge_page_html = "<html><body>Please verify you are human.</body></html>"
    fetcher = ArxivFetcher(page_size=10, listing_fetcher=lambda _category, _host: challenge_page_html)

    with caplog.at_level("WARNING"):
        ok = fetcher.load_recent_listings(("astro-ph.GA",))

    assert ok == ()
    assert fetcher._category_listing("astro-ph.GA") == {}
    assert any(
        "astro-ph.GA" in record.getMessage() and "zero announcement days" in record.getMessage()
        for record in caplog.records
    )


def test_load_recent_listings_treats_an_unparsable_id_as_a_failure(caplog) -> None:
    bad_listing_html = _listing_html(
        heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["not-an-id"]
    )
    fetcher = ArxivFetcher(page_size=10, listing_fetcher=lambda _category, _host: bad_listing_html)

    with caplog.at_level("WARNING"):
        ok = fetcher.load_recent_listings(("astro-ph.GA",))

    assert ok == ()
    assert fetcher._category_listing("astro-ph.GA") == {}
    assert any("astro-ph.GA" in record.getMessage() for record in caplog.records)


def test_load_announcement_feed_seeds_ids_that_reach_collect_candidates() -> None:
    """End-to-end through a real ArxivFetcher: RSS-parsed ids flow to collect_candidates."""
    rss_ids = ["2609.26877", "2609.26993", "2609.27048"]
    results_by_id = {
        source_id: SimpleNamespace(
            title=f"RSS Seeded Paper {source_id}",
            summary="Seeded via the RSS feed.",
            entry_id=f"https://arxiv.org/abs/{source_id}",
            authors=[SimpleNamespace(name="Test Author")],
            primary_category="astro-ph.GA",
            categories=("astro-ph.GA",),
            published=datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc),
            updated=datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc),
        )
        for source_id in rss_ids
    }

    class FakeClient:
        def __init__(self) -> None:
            self.searches = []

        def results(self, search: object):
            self.searches.append(search)
            return [results_by_id[source_id] for source_id in search.id_list]

    client = FakeClient()
    fetcher = ArxivFetcher(
        page_size=10,
        client=client,
        feed_fetcher=lambda categories: _rss_fixture(),
        rate_limiter=ArxivRateLimiter(),
    )

    feed_dates = fetcher.load_announcement_feed(("astro-ph.GA",))
    assert feed_dates == (date(2026, 9, 24),)

    papers = fetcher.collect_candidates(
        PreferenceConfig(priorities=("Galaxies",), categories=("astro-ph.GA",)),
        announcement_date=date(2026, 9, 24),
    )

    assert client.searches[0].id_list == rss_ids
    assert [paper.title for paper in papers] == [f"RSS Seeded Paper {source_id}" for source_id in rss_ids]
