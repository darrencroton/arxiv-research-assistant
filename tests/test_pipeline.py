import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from re_ass.generation_service import GenerationError
from re_ass.models import PreferenceConfig
from re_ass.note_manager import NoteManager
from re_ass.paper_identity import derive_identity, extract_source_id
from re_ass.pipeline import _listing_gap_dates, _snapshot_lookup_dates, run
from re_ass.ranking import RankingError
from re_ass.state_store import StateStore
from tests.support import make_app_config, make_paper


def _build_selection(candidates, *, selected=None, weekly_interest=None):
    selected = list(candidates if selected is None else selected)
    weekly_interest = list([] if weekly_interest is None else weekly_interest)
    ranked = []
    final_selected = []
    final_weekly_interest = []

    for offset, paper in enumerate(candidates):
        identity = derive_identity(paper)
        ranked.append(
            SimpleNamespace(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                score=float(100 - offset),
                rationale=f"Reason for {paper.title}",
                science_match=None,
                method_match=None,
            )
        )

    for offset, paper in enumerate(selected):
        identity = derive_identity(paper)
        final_selected.append(
            SimpleNamespace(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                score=float(98 - offset),
                rationale=f"Selected {paper.title}",
                science_match=None,
                method_match=None,
            )
        )

    for offset, paper in enumerate(weekly_interest):
        identity = derive_identity(paper)
        final_weekly_interest.append(
            SimpleNamespace(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                score=float(88 - offset),
                rationale=f"Weekly interest {paper.title}",
                science_match=None,
                method_match=None,
            )
        )

    return SimpleNamespace(
        selected_papers=selected,
        candidate_count=len(candidates),
        ranked=ranked,
        selected=final_selected,
        weekly_interest=final_weekly_interest,
    )


class FakeFetcher:
    last_call = None

    def __init__(self, papers, *, feed_dates=None, recent_dates=None, recent_listing_ok=True):
        self.papers = papers
        self.feed_dates = tuple(feed_dates if feed_dates is not None else [date(2026, 3, 22)])
        self.recent_dates = tuple(recent_dates or [])
        self.recent_listing_ok = recent_listing_ok
        self.recent_listing_calls: list[tuple[str, ...]] = []
        self.recent_listing_required_dates: list[tuple[date, ...]] = []
        self.seeded_dates: set[date] = set()
        self._recent_listing_loaded = False

    def seed_listings(self, listings):
        for day_to_ids in listings.values():
            self.seeded_dates.update(day_to_ids)

    def listing_for_day(self, categories, _announcement_date):
        return {category: ["2603.00001"] for category in categories}

    def load_announcement_feed(self, _categories):
        return self.feed_dates

    def load_recent_listings(self, categories, required_dates=()):
        self.recent_listing_calls.append(tuple(categories))
        self.recent_listing_required_dates.append(tuple(required_dates))
        if not self.recent_listing_ok:
            return ()
        self._recent_listing_loaded = True
        return tuple(sorted(set(self.recent_dates) | set(self.feed_dates)))

    def available_announcement_dates(self, _categories):
        dates = set(self.feed_dates) | self.seeded_dates
        if self._recent_listing_loaded:
            dates.update(self.recent_dates)
        return tuple(sorted(dates))

    def collect_candidates(self, *_args, **kwargs):
        FakeFetcher.last_call = kwargs
        return list(self.papers)


class FakeGenerationService:
    def __init__(
        self,
        *,
        failing_titles: set[str] | None = None,
        note_content_by_title: dict[str, str] | None = None,
    ) -> None:
        self.failing_titles = failing_titles or set()
        self.note_content_by_title = note_content_by_title or {}
        self.provider = object()
        self.prompt_logger = None
        self.weekly_synthesis_calls: list[dict[str, object]] = []

    def generate_micro_summary(self, paper):
        return f"Summary for {paper.title}"

    def stage_pdf_download(self, paper, destination_dir: Path):
        staged = destination_dir / f"{paper.title}.pdf"
        staged.write_bytes(b"%PDF-1.4 fake")
        return staged

    def build_paper_note_content(self, paper, _staged_source_path: Path):
        if paper.title in self.failing_titles:
            raise GenerationError("simulated failure")
        return self.note_content_by_title.get(
            paper.title,
            f"# {paper.title}\n\nAuthors: Doe J.\nPublished: March 2026 ([Link](https://arxiv.org/abs/example))\n\n## Notes\nGenerated.\n",
        )

    def generate_weekly_synthesis(self, existing_synthesis: str, weekly_additions: str, *, word_limit: int, max_tokens: int = 4096):
        self.weekly_synthesis_calls.append(
            {
                "existing_synthesis": existing_synthesis,
                "weekly_additions": weekly_additions,
                "word_limit": word_limit,
                "max_tokens": max_tokens,
            }
        )
        return f"Synthesis around {word_limit} words."


class FakeRanker:
    last_min_summarize_score = None

    def __init__(self, *, selection=None, min_summarize_score=None, **_kwargs) -> None:
        self.selection = selection
        FakeRanker.last_min_summarize_score = min_summarize_score

    def rank_papers(self, _preferences, candidates):
        selection = self.selection or _build_selection(candidates, selected=candidates[:1])
        return SimpleNamespace(
            selected_papers=list(selection.selected_papers),
            candidate_count=selection.candidate_count,
            ranked=selection.ranked,
            selected=selection.selected,
            weekly_interest=selection.weekly_interest,
        )


class FailingRanker:
    def __init__(self, **_kwargs) -> None:
        pass

    def rank_papers(self, _preferences, _candidates):
        raise RankingError("ranking payload remained invalid")


def _preferences() -> PreferenceConfig:
    return PreferenceConfig(priorities=("Example priority",), categories=("astro-ph.GA",))


def _patch_pipeline(monkeypatch, fetcher) -> None:
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: fetcher)
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())


def test_pipeline_returns_zero_and_writes_run_summary_when_no_new_papers(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([], feed_dates=[date(2026, 3, 22)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())

    exit_code = run(config, date(2026, 3, 22))

    assert exit_code == 0
    run_summaries = list(config.state_runs_dir.glob("*.json"))
    assert len(run_summaries) == 1
    assert not any(config.daily_notes_dir.glob("*.md"))
    assert json.loads(run_summaries[0].read_text(encoding="utf-8"))["listing_gap_fallback"] == "not_needed"


def test_pipeline_continues_after_non_fatal_per_paper_failure(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30001", title="Working Paper"),
        make_paper(arxiv_id="2603.30002", title="Broken Paper"),
    ]
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher(papers, feed_dates=[date(2026, 3, 24)]))
    monkeypatch.setattr(
        "re_ass.pipeline.PaperRanker",
        lambda **kwargs: FakeRanker(selection=_build_selection(papers, selected=papers), **kwargs),
    )
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService(failing_titles={"Broken Paper"}))

    exit_code = run(config, date(2026, 3, 24))

    assert exit_code == 0
    assert (config.summaries_dir / "Bayer et al - 2026 - Working Paper [arXiv 2603.30001].md").exists()
    assert not (config.summaries_dir / "Bayer et al - 2026 - Broken Paper [arXiv 2603.30002].md").exists()
    assert (config.state_papers_dir / "arxiv_2603.30002.json").exists()
    assert "Working Paper" in (config.daily_notes_dir / "2026-03-24.md").read_text(encoding="utf-8")
    weekly_note_text = (config.weekly_notes_dir / config.weekly_note_file).read_text(encoding="utf-8")
    assert "Working Paper" in weekly_note_text
    assert "Broken Paper" not in weekly_note_text


def test_pipeline_fails_hard_when_provider_construction_fails(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: (_ for _ in ()).throw(ValueError("provider missing")))

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 1
    assert not any(config.summaries_dir.glob("*.md"))
    run_summary = next(config.state_runs_dir.glob("*.json")).read_text(encoding="utf-8")
    assert "provider missing" in run_summary


def test_pipeline_records_ranking_failure_without_unhandled_crash(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2603.30003", title="Unranked Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 3, 24)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FailingRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 24))

    assert exit_code == 1
    run_summaries = list(config.state_runs_dir.glob("*fatal*.json"))
    assert len(run_summaries) == 1
    summary_text = run_summaries[0].read_text(encoding="utf-8")
    assert '"fatal_error": "ranking payload remained invalid"' in summary_text
    assert '"candidate_count": 1' in summary_text


def test_pipeline_writes_verbatim_summariser_note_output(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2603.30011", title="Verbatim Paper")
    raw_summary = (
        "# Verbatim Paper\n\n"
        "Authors: Doe J., Smith J.\n"
        "Published: March 2026 ([Link](https://arxiv.org/abs/2603.30011))\n\n"
        "## Key Ideas\n"
        "- Important point[^1]\n\n"
        "## References\n"
        '[^1]: "Quoted support" (Abstract, p.1)\n'
    )
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 3, 26)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr(
        "re_ass.pipeline.GenerationService",
        lambda **_kwargs: FakeGenerationService(note_content_by_title={"Verbatim Paper": raw_summary}),
    )

    exit_code = run(config, date(2026, 3, 26))

    assert exit_code == 0
    note_path = config.summaries_dir / "Bayer et al - 2026 - Verbatim Paper [arXiv 2603.30011].md"
    assert note_path.read_text(encoding="utf-8") == raw_summary


def test_pipeline_leaves_papers_retryable_when_note_update_fails(tmp_path: Path, monkeypatch) -> None:
    class FailingNoteManager(NoteManager):
        def update_daily_note(self, run_date, top_paper, *, reference_date=None):
            raise ValueError("broken daily template")

    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2603.30031", title="Retryable Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 3, 27)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())
    monkeypatch.setattr("re_ass.pipeline.NoteManager", FailingNoteManager)

    exit_code = run(config, date(2026, 3, 27))

    assert exit_code == 1
    record_path = config.state_papers_dir / "arxiv_2603.30031.json"
    assert record_path.exists()
    assert '"status": "note_written"' in record_path.read_text(encoding="utf-8")


def test_pipeline_auto_assigns_note_dates_ending_at_invocation_date(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30035", title="Thursday Batch"),
        make_paper(arxiv_id="2603.30036", title="Friday Batch"),
        make_paper(arxiv_id="2603.30037", title="Monday Batch"),
    ]

    class SequencedFetcher(FakeFetcher):
        def __init__(self):
            super().__init__([], feed_dates=[date(2026, 3, 20), date(2026, 3, 21), date(2026, 3, 24)])
            self._papers_by_day = {
                date(2026, 3, 20): [papers[0]],
                date(2026, 3, 21): [papers[1]],
                date(2026, 3, 24): [papers[2]],
            }

        def collect_candidates(self, *_args, **kwargs):
            FakeFetcher.last_call = kwargs
            return list(self._papers_by_day[kwargs["announcement_date"]])

    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: SequencedFetcher())
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 0
    assert "Thursday Batch" in (config.daily_notes_dir / "2026-03-23.md").read_text(encoding="utf-8")
    assert "Friday Batch" in (config.daily_notes_dir / "2026-03-24.md").read_text(encoding="utf-8")
    assert "Monday Batch" in (config.daily_notes_dir / "2026-03-25.md").read_text(encoding="utf-8")
    run_files = sorted(config.state_runs_dir.glob("*.json"))
    assert len(run_files) == 3
    assert any("announcement-2026-03-20" in path.name for path in run_files)
    assert any("announcement-2026-03-21" in path.name for path in run_files)
    assert any("announcement-2026-03-24" in path.name for path in run_files)


def test_pipeline_shifted_announcements_fill_next_weekday_notes_and_defer_future_notes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = make_app_config(tmp_path, shift_announcements_to_next_weekday=True)
    announcement_dates = [
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 5, 6),
        date(2026, 5, 7),
        date(2026, 5, 8),
    ]
    note_dates = [
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 5, 6),
        date(2026, 5, 7),
        date(2026, 5, 8),
    ]
    papers_by_day = {
        announcement_day: [
            make_paper(
                arxiv_id=f"2605.30{index:03d}",
                title=f"Announcement {announcement_day.isoformat()}",
            )
        ]
        for index, announcement_day in enumerate(announcement_dates, start=1)
    }

    class SequencedFetcher(FakeFetcher):
        def __init__(self):
            super().__init__([], feed_dates=announcement_dates)

        def collect_candidates(self, *_args, **kwargs):
            FakeFetcher.last_call = kwargs
            return list(papers_by_day[kwargs["announcement_date"]])

    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: SequencedFetcher())
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 5, 8))

    assert exit_code == 0
    for announcement_day, note_day in zip(announcement_dates[:5], note_dates):
        daily_text = (config.daily_notes_dir / f"{note_day.isoformat()}.md").read_text(encoding="utf-8")
        assert f"Announcement {announcement_day.isoformat()}" in daily_text
    assert not (config.daily_notes_dir / "2026-05-11.md").exists()
    assert '"last_completed_announcement_date": "2026-05-07"' in (
        config.state_root / "announcement-checkpoint.json"
    ).read_text(encoding="utf-8")


def test_pipeline_defers_all_announcements_when_every_note_date_is_in_the_future(tmp_path: Path, monkeypatch) -> None:
    # Friday announcement maps to Monday; running on Friday should return 0
    # with no notes written and the checkpoint left untouched.
    config = make_app_config(tmp_path, shift_announcements_to_next_weekday=True)
    monkeypatch.setattr(
        "re_ass.pipeline.ArxivFetcher",
        lambda **_kwargs: FakeFetcher([make_paper()], feed_dates=[date(2026, 5, 8)]),
    )
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 5, 8))

    assert exit_code == 0
    assert not any(config.daily_notes_dir.glob("*.md"))
    assert not (config.state_root / "announcement-checkpoint.json").exists()


def test_pipeline_skips_weekend_note_dates_when_backfilling_automatic_runs(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30101", title="Friday Reading Paper"),
        make_paper(arxiv_id="2603.30102", title="Monday Reading Paper"),
    ]

    class WeekendGapFetcher(FakeFetcher):
        def __init__(self):
            super().__init__([], feed_dates=[date(2026, 3, 25), date(2026, 3, 26)])
            self._papers_by_day = {
                date(2026, 3, 25): [papers[0]],
                date(2026, 3, 26): [papers[1]],
            }

        def collect_candidates(self, *_args, **kwargs):
            FakeFetcher.last_call = kwargs
            return list(self._papers_by_day[kwargs["announcement_date"]])

    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: WeekendGapFetcher())
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 30))

    assert exit_code == 0
    assert "Friday Reading Paper" in (config.daily_notes_dir / "2026-03-27.md").read_text(encoding="utf-8")
    assert "Monday Reading Paper" in (config.daily_notes_dir / "2026-03-30.md").read_text(encoding="utf-8")
    assert not (config.daily_notes_dir / "2026-03-28.md").exists()
    assert not (config.daily_notes_dir / "2026-03-29.md").exists()


def test_pipeline_explicit_date_backfill_stays_on_requested_date_when_shift_enabled(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path, shift_announcements_to_next_weekday=True)
    paper = make_paper(arxiv_id="2605.30101", title="Surgical Backfill Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 5, 4)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 5, 4), backfill=True)

    assert exit_code == 0
    assert "Surgical Backfill Paper" in (config.daily_notes_dir / "2026-05-04.md").read_text(encoding="utf-8")
    assert not (config.daily_notes_dir / "2026-05-05.md").exists()


def test_pipeline_backfill_leaves_current_weekly_summary_unchanged(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    manager = NoteManager(config)
    manager.bootstrap(reference_date=date(2026, 3, 23))
    manager.weekly_note_path.write_text(
        "# ARXIV PAPERS FOR THE WEEK 16th - 20th March 2026\n\n"
        "## SYNTHESIS\n"
        "\n"
        "Live synthesis.\n"
        "\n"
        "---\n"
        "## DAILY ADDITIONS\n"
        "\n"
        "### Sunday 22nd\n"
        "\n"
        "**Title:** [[Existing]]\n"
        "\n"
        "**Summary:** Existing summary\n"
        "\n",
        encoding="utf-8",
    )
    paper = make_paper(arxiv_id="2603.30041", title="Backfill Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 3, 23)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 23), backfill=True)

    assert exit_code == 0
    assert "Backfill Paper" in (config.daily_notes_dir / "2026-03-23.md").read_text(encoding="utf-8")
    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "Live synthesis." in weekly_text
    assert "Backfill Paper" not in weekly_text
    assert not (config.weekly_notes_dir / "2026-03-16-weekly-arxiv.md").exists()


def test_pipeline_backfill_renders_daily_template_for_target_date(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    config.daily_template.parent.mkdir(parents=True, exist_ok=True)
    config.daily_template.write_text(
        "# DAILY NOTE: {{date:dddd Do MMMM YYYY}}\n\n" + config.daily_top_paper_heading + "\n",
        encoding="utf-8",
    )
    paper = make_paper(arxiv_id="2603.30036", title="Backfill Template Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 3, 23)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 23), backfill=True)

    assert exit_code == 0
    daily_text = (config.daily_notes_dir / "2026-03-23.md").read_text(encoding="utf-8")
    assert daily_text.startswith("# DAILY NOTE: Monday 23rd March 2026\n")


def test_pipeline_regenerates_weekly_synthesis_from_full_week_context(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    manager = NoteManager(config)
    manager.bootstrap(reference_date=date(2026, 3, 25))
    manager.weekly_note_path.write_text(
        "# ARXIV PAPERS FOR THE WEEK 23rd - 27th March 2026\n\n"
        "## SYNTHESIS\n\n"
        "Earlier synthesis.\n\n"
        "---\n"
        "## DAILY ADDITIONS\n\n"
        "### Monday 23rd\n\n"
        "**Title:** [[Existing]]\n\n"
        "**Summary:** Existing summary.\n",
        encoding="utf-8",
    )
    paper = make_paper(arxiv_id="2603.30061", title="Wednesday Paper")
    generation_service = FakeGenerationService()
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 3, 25)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: generation_service)

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 0
    assert generation_service.weekly_synthesis_calls == [
        {
            "existing_synthesis": "Earlier synthesis.",
            "weekly_additions": (
                "### Monday 23rd\n\n"
                "**Title:** [[Existing]]\n\n"
                "**Summary:** Existing summary.\n\n"
                "---\n\n"
                "### Wednesday 25th\n\n"
                "**Title:** [[Bayer et al - 2026 - Wednesday Paper [arXiv 2603.30061]|Wednesday Paper]]\n"
                "**Authors:** Bayer M. & Doe J.\n"
                "**Summary:** Summary for Wednesday Paper [arXiv:2603.30061](https://arxiv.org/abs/2603.30061)"
            ),
            "word_limit": 150,
            "max_tokens": 4096,
        }
    ]


def test_pipeline_writes_weekly_interest_bullets_without_leaking_them_into_synthesis(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path, min_summarize_score=90.0, min_selection_score=70.0)
    summarized = make_paper(arxiv_id="2603.30071", title="Summarized Paper")
    weekly_only = make_paper(
        arxiv_id="2603.30072",
        title="Weekly Only Paper",
        authors=("Elena Banados", "Yuan Peng", "Chris Ledoux"),
    )
    selection = _build_selection([summarized, weekly_only], selected=[summarized], weekly_interest=[weekly_only])
    generation_service = FakeGenerationService()
    monkeypatch.setattr(
        "re_ass.pipeline.ArxivFetcher",
        lambda **_kwargs: FakeFetcher([summarized, weekly_only], feed_dates=[date(2026, 3, 25)]),
    )
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(selection=selection, **kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: generation_service)

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 0
    assert generation_service.weekly_synthesis_calls == [
        {
            "existing_synthesis": "*(A synthesis of this week's papers will be automatically generated here. Max 100 words.)*",
            "weekly_additions": (
                "### Wednesday 25th\n\n"
                "**Title:** [[Bayer et al - 2026 - Summarized Paper [arXiv 2603.30071]|Summarized Paper]]\n"
                "**Authors:** Bayer M. & Doe J.\n"
                "**Summary:** Summary for Summarized Paper [arXiv:2603.30071](https://arxiv.org/abs/2603.30071)"
            ),
            "word_limit": 150,
            "max_tokens": 4096,
        }
    ]
    weekly_text = (config.weekly_notes_dir / config.weekly_note_file).read_text(encoding="utf-8")
    assert "**Other papers of interest:**" in weekly_text
    source_id = extract_source_id(weekly_only.entry_id)
    assert f'[arXiv:{source_id}]({weekly_only.arxiv_url})' in weekly_text
    assert "Weekly Only Paper" in weekly_text
    assert "Peng Y." not in generation_service.weekly_synthesis_calls[0]["weekly_additions"]


def test_pipeline_writes_no_papers_placeholder_when_nothing_clears_threshold(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_app_config(tmp_path, min_summarize_score=90.0, min_selection_score=70.0)
    candidate = make_paper(
        arxiv_id="2603.30073",
        title="Below Threshold Paper",
        authors=("Elena Banados", "Yuan Peng"),
    )
    # Ranking returns empty selected — nothing cleared any threshold.
    selection = _build_selection([candidate], selected=[], weekly_interest=[])
    generation_service = FakeGenerationService()
    monkeypatch.setattr(
        "re_ass.pipeline.ArxivFetcher",
        lambda **_kwargs: FakeFetcher([candidate], feed_dates=[date(2026, 3, 25)]),
    )
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(selection=selection, **kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: generation_service)

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 0
    assert generation_service.weekly_synthesis_calls == []
    daily_notes = list(config.daily_notes_dir.glob("*.md"))
    assert len(daily_notes) == 1
    assert "No top papers today." in daily_notes[0].read_text(encoding="utf-8")


def test_pipeline_writes_weekly_interest_when_nothing_selected_but_partial_matches_exist(
    tmp_path: Path, monkeypatch
) -> None:
    # Regression test: dual_match failures leave selected=[] but weekly_interest non-empty.
    # The weekly note must still receive interest bullets even when the daily note shows
    # the no-papers placeholder.
    config = make_app_config(tmp_path, min_summarize_score=90.0, min_selection_score=70.0)
    partial_match = make_paper(
        arxiv_id="2603.30076",
        title="Partial Match Paper",
        authors=("Elena Banados", "Yuan Peng"),
    )
    selection = _build_selection([partial_match], selected=[], weekly_interest=[partial_match])
    generation_service = FakeGenerationService()
    monkeypatch.setattr(
        "re_ass.pipeline.ArxivFetcher",
        lambda **_kwargs: FakeFetcher([partial_match], feed_dates=[date(2026, 3, 25)]),
    )
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(selection=selection, **kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: generation_service)

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 0
    assert generation_service.weekly_synthesis_calls == []
    daily_notes = list(config.daily_notes_dir.glob("*.md"))
    assert len(daily_notes) == 1
    assert "No top papers today." in daily_notes[0].read_text(encoding="utf-8")
    weekly_text = (config.weekly_notes_dir / config.weekly_note_file).read_text(encoding="utf-8")
    assert "Partial Match Paper" in weekly_text
    assert "**Other papers of interest:**" in weekly_text


def test_pipeline_still_writes_weekly_interest_when_selected_papers_all_fail(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path, min_summarize_score=90.0, min_selection_score=70.0)
    selected = make_paper(arxiv_id="2603.30074", title="Failing Selected Paper")
    weekly_only = make_paper(
        arxiv_id="2603.30075",
        title="Still Worth Listing",
        authors=("Kevin Wang", "Yingjie Peng"),
    )
    selection = _build_selection([selected, weekly_only], selected=[selected], weekly_interest=[weekly_only])
    generation_service = FakeGenerationService(failing_titles={"Failing Selected Paper"})
    monkeypatch.setattr(
        "re_ass.pipeline.ArxivFetcher",
        lambda **_kwargs: FakeFetcher([selected, weekly_only], feed_dates=[date(2026, 3, 25)]),
    )
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(selection=selection, **kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: generation_service)

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 0
    assert generation_service.weekly_synthesis_calls == []
    assert not any(config.daily_notes_dir.glob("*.md"))
    weekly_text = (config.weekly_notes_dir / config.weekly_note_file).read_text(encoding="utf-8")
    assert "**Other papers of interest:**" in weekly_text
    assert "Still Worth Listing" in weekly_text
    assert "Failing Selected Paper" not in weekly_text


def test_pipeline_records_announcement_and_ranking_diagnostics(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30051", title="Ranked One"),
        make_paper(arxiv_id="2603.30052", title="Ranked Two"),
    ]
    selection = _build_selection(papers, selected=papers[:1], weekly_interest=papers[1:])
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher(papers, feed_dates=[date(2026, 3, 26)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(selection=selection, **kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 26))

    assert exit_code == 0
    summary_text = next(config.state_runs_dir.glob("*.json")).read_text(encoding="utf-8")
    assert '"announcement_date": "2026-03-26"' in summary_text
    assert '"note_date": "2026-03-26"' in summary_text
    assert '"available_announcement_dates"' in summary_text
    assert '"visible_window_start": "2026-03-26"' in summary_text
    assert '"candidate_count": 2' in summary_text
    assert '"min_summarize_score": 90.0' in summary_text
    assert '"min_selection_score": 70.0' in summary_text
    assert '"ranking_results"' in summary_text
    assert '"selected_results"' in summary_text
    assert '"weekly_interest_results"' in summary_text


def test_pipeline_run_summary_records_role_specific_llm_stamps(tmp_path: Path, monkeypatch) -> None:
    base_config = make_app_config(tmp_path)
    ranking_llm = replace(base_config.llm, provider="copilot", model="rank-mini")
    summary_llm = replace(base_config.llm, provider="claude", model="summary-opus")
    config = replace(base_config, ranking_llm=ranking_llm, summary_llm=summary_llm)
    paper = make_paper(arxiv_id="2603.30081", title="Role Stamp Paper")

    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], feed_dates=[date(2026, 3, 26)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())
    monkeypatch.setattr("re_ass.pipeline.make_provider", lambda _config: object())

    exit_code = run(config, date(2026, 3, 26))

    assert exit_code == 0
    run_summary = json.loads(next(config.state_runs_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert run_summary["llm"]["provider"] == "claude"
    assert run_summary["llm"]["base"]["provider"] == "claude"
    assert run_summary["llm"]["ranking"]["provider"] == "copilot"
    assert run_summary["llm"]["ranking"]["model"] == "rank-mini"
    assert run_summary["llm"]["summary"]["provider"] == "claude"
    assert run_summary["llm"]["summary"]["model"] == "summary-opus"


def test_listing_gap_dates_lists_weekdays_between_marker_and_feed_date() -> None:
    gap = _listing_gap_dates(
        (date(2026, 9, 24),),
        last_completed_announcement_date=date(2026, 9, 17),
        backfill_date=None,
    )
    assert gap == [date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]

    gap_multi_date_feed = _listing_gap_dates(
        (date(2026, 9, 22), date(2026, 9, 24)),
        last_completed_announcement_date=date(2026, 9, 17),
        backfill_date=None,
    )
    assert gap_multi_date_feed == [date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 23)]


@pytest.mark.parametrize(
    "feed_dates,last_completed_announcement_date",
    [
        ((date(2026, 9, 24),), None),
        ((date(2026, 9, 24),), date(2026, 9, 23)),
        ((date(2026, 9, 24),), date(2026, 9, 24)),
        ((date(2026, 9, 24),), date(2026, 9, 25)),
        ((date(2026, 9, 28),), date(2026, 9, 25)),
    ],
    ids=[
        "first-run",
        "marker-day-before-feed",
        "marker-equals-feed",
        "marker-after-feed",
        "friday-marker-monday-feed",
    ],
)
def test_listing_gap_dates_is_empty_when_nothing_is_missing(
    feed_dates, last_completed_announcement_date
) -> None:
    gap = _listing_gap_dates(
        feed_dates,
        last_completed_announcement_date=last_completed_announcement_date,
        backfill_date=None,
    )
    assert gap == []


def test_listing_gap_dates_for_backfill_only_when_date_missing_from_feed() -> None:
    assert _listing_gap_dates(
        (), last_completed_announcement_date=None, backfill_date=date(2026, 9, 20)
    ) == [date(2026, 9, 20)]
    assert _listing_gap_dates(
        (date(2026, 9, 20),), last_completed_announcement_date=None, backfill_date=date(2026, 9, 20)
    ) == []


def test_pipeline_uses_rss_only_when_no_gap(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2609.40001", title="RSS Only Paper")
    fetcher = FakeFetcher([paper], feed_dates=[date(2026, 9, 24)])
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 24))

    assert exit_code == 0
    assert fetcher.recent_listing_calls == []
    assert "RSS Only Paper" in (config.daily_notes_dir / "2026-09-24.md").read_text(encoding="utf-8")
    run_summary = json.loads(
        next(config.state_runs_dir.glob("*announcement-2026-09-24*.json")).read_text(encoding="utf-8")
    )
    assert run_summary["listing_gap_fallback"] == "not_needed"


def test_pipeline_fills_gap_from_recent_listing_and_processes_both_days(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    StateStore(config).save_completed_announcement_date(date(2026, 9, 22))

    papers_by_day = {
        date(2026, 9, 23): [make_paper(arxiv_id="2609.40010", title="Gap Day Paper")],
        date(2026, 9, 24): [make_paper(arxiv_id="2609.40011", title="Feed Day Paper")],
    }

    class SequencedFetcher(FakeFetcher):
        def collect_candidates(self, *_args, **kwargs):
            FakeFetcher.last_call = kwargs
            return list(papers_by_day[kwargs["announcement_date"]])

    fetcher = SequencedFetcher([], feed_dates=[date(2026, 9, 24)], recent_dates=[date(2026, 9, 23)])
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 24))

    assert exit_code == 0
    assert fetcher.recent_listing_calls == [("astro-ph.GA",)]
    assert "Gap Day Paper" in (config.daily_notes_dir / "2026-09-23.md").read_text(encoding="utf-8")
    assert "Feed Day Paper" in (config.daily_notes_dir / "2026-09-24.md").read_text(encoding="utf-8")
    run_summary = json.loads(
        next(config.state_runs_dir.glob("*announcement-2026-09-24*.json")).read_text(encoding="utf-8")
    )
    assert run_summary["listing_gap_fallback"] == "filled"


def test_pipeline_warns_and_continues_when_gap_fallback_fails(tmp_path: Path, monkeypatch, caplog) -> None:
    config = make_app_config(tmp_path)
    StateStore(config).save_completed_announcement_date(date(2026, 9, 22))

    paper = make_paper(arxiv_id="2609.40020", title="Feed Day Only Paper")
    fetcher = FakeFetcher([paper], feed_dates=[date(2026, 9, 24)], recent_listing_ok=False)
    _patch_pipeline(monkeypatch, fetcher)

    with caplog.at_level("WARNING"):
        exit_code = run(config, date(2026, 9, 24))

    assert exit_code == 0
    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert "--date 2026-09-23" in combined
    assert "leaves the weekly note untouched" in combined

    assert "Feed Day Only Paper" in (config.daily_notes_dir / "2026-09-24.md").read_text(encoding="utf-8")
    assert not (config.daily_notes_dir / "2026-09-23.md").exists()
    assert '"last_completed_announcement_date": "2026-09-24"' in (
        config.state_root / "announcement-checkpoint.json"
    ).read_text(encoding="utf-8")

    run_summary = json.loads(
        next(config.state_runs_dir.glob("*announcement-2026-09-24*.json")).read_text(encoding="utf-8")
    )
    assert run_summary["listing_gap_fallback"] == "failed"
    assert run_summary["listing_gap_dates"] == ["2026-09-23"]


def test_pipeline_keeps_gap_pending_when_fallback_fails_and_feed_day_is_deferred(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    config = make_app_config(tmp_path, shift_announcements_to_next_weekday=True)
    StateStore(config).save_completed_announcement_date(date(2026, 9, 23))

    paper = make_paper(arxiv_id="2609.40025", title="Deferred Feed Day Paper")
    fetcher = FakeFetcher([paper], feed_dates=[date(2026, 9, 28)], recent_listing_ok=False)
    _patch_pipeline(monkeypatch, fetcher)

    with caplog.at_level("WARNING"):
        exit_code = run(config, date(2026, 9, 28))

    assert exit_code == 0
    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert "stay pending and the next run will retry" in combined
    assert "--date" not in combined
    assert '"last_completed_announcement_date": "2026-09-23"' in (
        config.state_root / "announcement-checkpoint.json"
    ).read_text(encoding="utf-8")


def test_pipeline_falls_back_to_recent_listing_when_feed_unusable(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2609.40030", title="Recent Listing Rescue Paper")
    fetcher = FakeFetcher([paper], feed_dates=(), recent_dates=[date(2026, 9, 24)])
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 24))

    assert exit_code == 0
    assert fetcher.recent_listing_calls == [("astro-ph.GA",)]
    assert "Recent Listing Rescue Paper" in (config.daily_notes_dir / "2026-09-24.md").read_text(encoding="utf-8")


def test_pipeline_fails_when_feed_unusable_and_recent_listing_fails(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    fetcher = FakeFetcher([], feed_dates=(), recent_listing_ok=False)
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 24))

    assert exit_code == 1
    run_summaries = list(config.state_runs_dir.glob("*overall-fatal*.json"))
    assert len(run_summaries) == 1
    assert "No announcement listing available" in run_summaries[0].read_text(encoding="utf-8")
    assert not (config.state_root / "announcement-checkpoint.json").exists()


def test_pipeline_backfill_uses_feed_when_date_matches_feed_day(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2609.40040", title="Backfill Feed Match Paper")
    fetcher = FakeFetcher([paper], feed_dates=[date(2026, 9, 24)])
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 24), backfill=True)

    assert exit_code == 0
    assert fetcher.recent_listing_calls == []
    assert "Backfill Feed Match Paper" in (config.daily_notes_dir / "2026-09-24.md").read_text(encoding="utf-8")


def test_pipeline_backfill_uses_recent_listing_for_past_date(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2609.40050", title="Backfill Recent Listing Paper")
    fetcher = FakeFetcher([paper], feed_dates=[date(2026, 9, 24)], recent_dates=[date(2026, 9, 21)])
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 21), backfill=True)

    assert exit_code == 0
    assert fetcher.recent_listing_calls == [("astro-ph.GA",)]
    assert "Backfill Recent Listing Paper" in (config.daily_notes_dir / "2026-09-21.md").read_text(encoding="utf-8")
    assert not (config.daily_notes_dir / "2026-09-22.md").exists()


def test_pipeline_backfill_never_moves_the_marker_backwards(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    StateStore(config).save_completed_announcement_date(date(2026, 9, 24))

    paper = make_paper(arxiv_id="2609.40060", title="Backfill Older Day Paper")
    fetcher = FakeFetcher([paper], feed_dates=[date(2026, 9, 23)])
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 23), backfill=True)

    assert exit_code == 0
    assert "Backfill Older Day Paper" in (config.daily_notes_dir / "2026-09-23.md").read_text(encoding="utf-8")
    assert '"last_completed_announcement_date": "2026-09-24"' in (
        config.state_root / "announcement-checkpoint.json"
    ).read_text(encoding="utf-8")


def test_pipeline_backfill_reports_recent_listing_failure_when_date_absent_and_fallback_failed(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_app_config(tmp_path)
    fetcher = FakeFetcher([], feed_dates=[date(2026, 9, 24)], recent_listing_ok=False)
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 20), backfill=True)

    assert exit_code == 1
    run_summary = json.loads(
        next(config.state_runs_dir.glob("*overall-fatal*.json")).read_text(encoding="utf-8")
    )
    assert "2026-09-20" in run_summary["fatal_error"]
    assert "recent-listing fetch failed" in run_summary["fatal_error"]
    assert "retry" in run_summary["fatal_error"].lower()


def test_pipeline_backfill_reports_not_visible_when_date_absent_after_successful_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_app_config(tmp_path)
    fetcher = FakeFetcher([], feed_dates=[date(2026, 9, 24)], recent_dates=[date(2026, 9, 22)])
    _patch_pipeline(monkeypatch, fetcher)

    exit_code = run(config, date(2026, 9, 20), backfill=True)

    assert exit_code == 1
    run_summary = json.loads(
        next(config.state_runs_dir.glob("*overall-fatal*.json")).read_text(encoding="utf-8")
    )
    assert "not visible in the current arXiv recent window" in run_summary["fatal_error"]


def _save_snapshots(config, days: list[date]) -> None:
    store = StateStore(config)
    store.bootstrap()
    for day in days:
        store.save_listing_snapshot(day, {"astro-ph.GA": ["2609.50001"]}, source="rss")


def test_pipeline_saves_feed_and_recent_listing_days_as_snapshots(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    StateStore(config).save_completed_announcement_date(date(2026, 9, 22))
    fetcher = FakeFetcher(
        [make_paper(arxiv_id="2609.50010", title="Snapshot Paper")],
        feed_dates=[date(2026, 9, 24)],
        recent_dates=[date(2026, 9, 23)],
    )
    _patch_pipeline(monkeypatch, fetcher)

    assert run(config, date(2026, 9, 24)) == 0

    listings_dir = config.state_root / "listings"
    sources = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))["source"] for path in listings_dir.glob("*.json")
    }
    assert sources == {"2026-09-24": "rss", "2026-09-23": "list"}
    assert fetcher.recent_listing_required_dates == [(date(2026, 9, 23),)]


def test_pipeline_backfill_of_a_snapshotted_day_makes_no_recent_listing_request(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_app_config(tmp_path)
    _save_snapshots(config, [date(2026, 9, 21)])
    # An unusable feed must not trigger /list when the snapshot already covers the day.
    fetcher = FakeFetcher([make_paper(arxiv_id="2609.50020", title="Snapshot Backfill Paper")], feed_dates=())
    _patch_pipeline(monkeypatch, fetcher)

    assert run(config, date(2026, 9, 21), backfill=True) == 0

    assert fetcher.recent_listing_calls == []
    run_summary = json.loads(next(config.state_runs_dir.glob("*announcement-2026-09-21*.json")).read_text(encoding="utf-8"))
    assert run_summary["listing_gap_fallback"] == "not_needed"
    assert "Snapshot Backfill Paper" in (config.daily_notes_dir / "2026-09-21.md").read_text(encoding="utf-8")


def test_pipeline_processes_snapshot_days_after_the_marker_without_a_recent_listing_request(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_app_config(tmp_path)
    StateStore(config).save_completed_announcement_date(date(2026, 9, 22))
    _save_snapshots(config, [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)])
    processed_days: list[date] = []

    class RecordingFetcher(FakeFetcher):
        def collect_candidates(self, *_args, **kwargs):
            processed_days.append(kwargs["announcement_date"])
            return list(self.papers)

    fetcher = RecordingFetcher([make_paper(arxiv_id="2609.50030", title="Recovered Paper")], feed_dates=[date(2026, 9, 24)])
    _patch_pipeline(monkeypatch, fetcher)

    assert run(config, date(2026, 9, 24)) == 0

    assert fetcher.recent_listing_calls == []
    assert processed_days == [date(2026, 9, 23), date(2026, 9, 24)]
    run_summary = json.loads(
        next(config.state_runs_dir.glob("*announcement-2026-09-24*.json")).read_text(encoding="utf-8")
    )
    # Days on or before the marker are never looked up, so they cannot become pending.
    assert run_summary["snapshot_announcement_dates"] == ["2026-09-23"]


@pytest.mark.parametrize(
    "marker,backfill,expected",
    [
        (date(2026, 9, 22), True, [date(2026, 9, 24)]),
        (date(2026, 9, 22), False, [date(2026, 9, 23), date(2026, 9, 24)]),
        (date(2026, 9, 24), False, []),
        (None, False, []),
    ],
    ids=["backfill", "marker-to-invocation", "marker-caught-up", "no-marker"],
)
def test_snapshot_lookup_dates_are_bounded_to_days_a_run_can_use(marker, backfill, expected) -> None:
    assert _snapshot_lookup_dates(
        date(2026, 9, 24), last_completed_announcement_date=marker, backfill=backfill
    ) == expected


@pytest.mark.parametrize("window_start_snapshotted", [False, True])
def test_pipeline_warns_only_for_gap_days_older_than_the_recent_listing_window(
    tmp_path: Path, monkeypatch, caplog, window_start_snapshotted
) -> None:
    config = make_app_config(tmp_path)
    StateStore(config).save_completed_announcement_date(date(2026, 9, 16))
    if window_start_snapshotted:
        # The window's first day is already known, which must not move the window start.
        _save_snapshots(config, [date(2026, 9, 21)])
    # The window starts at 09-21 and lacks 09-22 (a no-announcement day), so only
    # 09-17 and 09-18 were never covered.
    fetcher = FakeFetcher(
        [make_paper(arxiv_id="2609.50040", title="Window Paper")],
        feed_dates=[date(2026, 9, 24)],
        recent_dates=[date(2026, 9, 21), date(2026, 9, 23)],
    )
    _patch_pipeline(monkeypatch, fetcher)

    with caplog.at_level("WARNING"):
        assert run(config, date(2026, 9, 24)) == 0

    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert "--date 2026-09-17" in combined
    assert "--date 2026-09-18" in combined
    assert "--date 2026-09-21" not in combined
    assert "--date 2026-09-22" not in combined
