from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from re_ass.arxiv_fetcher import ArxivFetcher, build_category_query, filter_papers_between, rank_papers
from re_ass.models import PreferenceConfig
from re_ass.models import ArxivPaper


def make_paper(
    *,
    title: str,
    summary: str,
    published_offset_hours: int,
) -> ArxivPaper:
    now = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    return ArxivPaper(
        title=title,
        summary=summary,
        arxiv_url=f"https://arxiv.org/abs/{title.replace(' ', '_')}",
        entry_id=f"https://arxiv.org/abs/{title.replace(' ', '_')}",
        authors=("Test Author",),
        primary_category="cs.AI",
        categories=("cs.AI",),
        published=now - timedelta(hours=published_offset_hours),
        updated=now - timedelta(hours=published_offset_hours),
    )


def test_build_category_query_joins_categories() -> None:
    assert build_category_query(("cs.AI", "cs.CL")) == "cat:cs.AI OR cat:cs.CL"


def test_filter_papers_between_respects_start_and_end_bounds() -> None:
    start = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    papers = [
        make_paper(title="Inside Window", summary="Agents", published_offset_hours=18),
        make_paper(title="Too Old", summary="Agents", published_offset_hours=30),
        make_paper(title="Future Paper", summary="Agents", published_offset_hours=-1),
    ]

    recent = filter_papers_between(papers, start=start, end=end)

    assert [paper.title for paper in recent] == ["Inside Window"]


def test_rank_papers_prefers_highest_ranked_preference_then_recency() -> None:
    papers = [
        make_paper(title="Agents Planning", summary="Planning systems", published_offset_hours=3),
        make_paper(title="RAG Benchmarks", summary="Retrieval methods", published_offset_hours=1),
        make_paper(title="Agents and RAG", summary="Joint systems", published_offset_hours=5),
        make_paper(title="Unrelated Vision", summary="Image generation", published_offset_hours=2),
    ]

    ranked = rank_papers(papers, ("Agents", "RAG"), max_papers=3)

    assert [paper.title for paper in ranked] == [
        "Agents and RAG",
        "Agents Planning",
        "RAG Benchmarks",
    ]


def test_rank_papers_matches_phrase_preferences_by_keyword_overlap() -> None:
    papers = [
        make_paper(
            title="Box Maze",
            summary="A process-control architecture for reliable LLM reasoning.",
            published_offset_hours=1,
        ),
        make_paper(
            title="Diffusion Schedules",
            summary="Noise schedules for image generation.",
            published_offset_hours=1,
        ),
    ]

    ranked = rank_papers(papers, ("Large Language Model Reasoning",), max_papers=3)

    assert [paper.title for paper in ranked] == ["Box Maze"]


def test_fetch_top_papers_tops_up_from_fallback_window_when_needed() -> None:
    now = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    result = SimpleNamespace(
        title="Primordial black holes in the early universe",
        summary="A study of black hole populations in cosmology.",
        entry_id="https://arxiv.org/abs/1234.5678",
        authors=[SimpleNamespace(name="Test Author")],
        primary_category="astro-ph.CO",
        categories=("astro-ph.CO",),
        published=now - timedelta(hours=48),
        updated=now - timedelta(hours=48),
    )

    class FakeClient:
        def results(self, _search: object):
            return [result]

    fetcher = ArxivFetcher(
        max_results=10,
        fetch_window_hours=24,
        fallback_window_hours=168,
        now_provider=lambda: now,
        client=FakeClient(),
    )

    papers = fetcher.fetch_top_papers(
        PreferenceConfig(
            priorities=("black holes and AGN",),
            categories=("astro-ph.CO",),
        ),
        max_papers=3,
    )

    assert [paper.title for paper in papers] == ["Primordial black holes in the early universe"]
