from datetime import date, datetime, timezone
from types import SimpleNamespace

from re_ass.arxiv_fetcher import ArxivFetcher
from re_ass.models import PreferenceConfig


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


def test_available_announcement_dates_unions_configured_categories() -> None:
    listing_html_by_category = {
        "cs.AI": _listing_html(heading="Mon, 23 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10021"]),
        "cs.CL": _listing_html(heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )", ids=["2603.10022"]),
    }

    fetcher = ArxivFetcher(
        page_size=10,
        client=SimpleNamespace(results=lambda _search: []),
        listing_fetcher=lambda category: listing_html_by_category[category],
    )

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
        listing_fetcher=lambda category: listing_html_by_category[category],
    )

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
        listing_fetcher=lambda category: listing_html_by_category[category],
    )

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
        listing_fetcher=lambda _category: _listing_html(
            heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )",
            ids=["2603.10040"],
        ),
    )

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
        listing_fetcher=lambda _category: _listing_html(
            heading="Tue, 24 Mar 2026 (showing 1 of 1 entries )",
            ids=["2603.10050"],
        ),
    )

    try:
        fetcher.collect_candidates(
            PreferenceConfig(priorities=("Agents",), categories=("cs.AI",)),
            announcement_date=date(2026, 3, 25),
        )
    except ValueError as error:
        assert "2026-03-25" in str(error)
    else:
        raise AssertionError("Expected collect_candidates to reject an unavailable announcement date.")
