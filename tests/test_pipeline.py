from datetime import date
from pathlib import Path
from types import SimpleNamespace

from re_ass.generation_service import GenerationError
from re_ass.models import PreferenceConfig
from re_ass.note_manager import NoteManager
from re_ass.paper_identity import extract_source_id
from re_ass.pipeline import run
from re_ass.paper_identity import derive_identity
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

    def __init__(self, papers, *, available_dates=None):
        self.papers = papers
        self.available_dates = list(available_dates or [date(2026, 3, 22)])

    def available_announcement_dates(self, _categories):
        return tuple(self.available_dates)

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

    def generate_weekly_synthesis(self, existing_synthesis: str, weekly_additions: str, *, word_limit: int):
        self.weekly_synthesis_calls.append(
            {
                "existing_synthesis": existing_synthesis,
                "weekly_additions": weekly_additions,
                "word_limit": word_limit,
            }
        )
        return f"Synthesis under {word_limit} words."


class FakeRanker:
    last_max_papers = None
    last_always_summarize_score = None

    def __init__(self, *, selection=None, max_papers=None, always_summarize_score=None, **_kwargs) -> None:
        self.selection = selection
        FakeRanker.last_max_papers = max_papers
        FakeRanker.last_always_summarize_score = always_summarize_score

    def rank_papers(self, _preferences, candidates):
        selection = self.selection or _build_selection(candidates, selected=candidates[: FakeRanker.last_max_papers])
        return SimpleNamespace(
            selected_papers=list(selection.selected_papers)[: FakeRanker.last_max_papers],
            candidate_count=selection.candidate_count,
            ranked=selection.ranked,
            selected=selection.selected[: FakeRanker.last_max_papers],
            weekly_interest=selection.weekly_interest,
        )


def _preferences() -> PreferenceConfig:
    return PreferenceConfig(priorities=("Example priority",), categories=("astro-ph.GA",))


def test_pipeline_returns_zero_and_writes_run_summary_when_no_new_papers(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([], available_dates=[date(2026, 3, 22)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())

    exit_code = run(config, date(2026, 3, 22))

    assert exit_code == 0
    run_summaries = list(config.state_runs_dir.glob("*.json"))
    assert len(run_summaries) == 1
    assert not any(config.daily_notes_dir.glob("*.md"))


def test_pipeline_continues_after_non_fatal_per_paper_failure(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30001", title="Working Paper"),
        make_paper(arxiv_id="2603.30002", title="Broken Paper"),
    ]
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher(papers, available_dates=[date(2026, 3, 24)]))
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
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], available_dates=[date(2026, 3, 26)]))
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
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], available_dates=[date(2026, 3, 27)]))
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
            super().__init__([], available_dates=[date(2026, 3, 20), date(2026, 3, 21), date(2026, 3, 24)])
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


def test_pipeline_skips_weekend_note_dates_when_backfilling_automatic_runs(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30101", title="Friday Reading Paper"),
        make_paper(arxiv_id="2603.30102", title="Monday Reading Paper"),
    ]

    class WeekendGapFetcher(FakeFetcher):
        def __init__(self):
            super().__init__([], available_dates=[date(2026, 3, 25), date(2026, 3, 26)])
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
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], available_dates=[date(2026, 3, 23)]))
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
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], available_dates=[date(2026, 3, 23)]))
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
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper], available_dates=[date(2026, 3, 25)]))
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
                "**Summary:** Summary for Wednesday Paper"
            ),
            "word_limit": 150,
        }
    ]


def test_pipeline_writes_weekly_interest_bullets_without_leaking_them_into_synthesis(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path, max_papers=2, always_summarize_score=90.0, min_selection_score=70.0)
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
        lambda **_kwargs: FakeFetcher([summarized, weekly_only], available_dates=[date(2026, 3, 25)]),
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
                "**Summary:** Summary for Summarized Paper"
            ),
            "word_limit": 150,
        }
    ]
    weekly_text = (config.weekly_notes_dir / config.weekly_note_file).read_text(encoding="utf-8")
    assert "**Other papers of interest:**" in weekly_text
    source_id = extract_source_id(weekly_only.entry_id)
    assert f'[arXiv:{source_id}]({weekly_only.arxiv_url})' in weekly_text
    assert "Weekly Only Paper" in weekly_text
    assert "Peng Y." not in generation_service.weekly_synthesis_calls[0]["weekly_additions"]


def test_pipeline_with_zero_max_papers_skips_daily_note_and_synthesis_when_only_weekly_interest_exists(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_app_config(tmp_path, max_papers=0, always_summarize_score=90.0, min_selection_score=70.0)
    weekly_only = make_paper(
        arxiv_id="2603.30073",
        title="Weekly Interest Only",
        authors=("Elena Banados", "Yuan Peng"),
    )
    selection = _build_selection([weekly_only], selected=[], weekly_interest=[weekly_only])
    generation_service = FakeGenerationService()
    monkeypatch.setattr(
        "re_ass.pipeline.ArxivFetcher",
        lambda **_kwargs: FakeFetcher([weekly_only], available_dates=[date(2026, 3, 25)]),
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
    assert "Weekly Interest Only" in weekly_text
    assert "## SYNTHESIS\n\n*(A synthesis of this week's papers will be automatically generated here. Max 100 words.)*" in weekly_text


def test_pipeline_still_writes_weekly_interest_when_selected_papers_all_fail(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path, max_papers=1, always_summarize_score=90.0, min_selection_score=70.0)
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
        lambda **_kwargs: FakeFetcher([selected, weekly_only], available_dates=[date(2026, 3, 25)]),
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
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher(papers, available_dates=[date(2026, 3, 26)]))
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
    assert '"max_papers": 10' in summary_text
    assert '"always_summarize_score": 90.0' in summary_text
    assert '"min_selection_score": 70.0' in summary_text
    assert '"ranking_results"' in summary_text
    assert '"selected_results"' in summary_text
    assert '"weekly_interest_results"' in summary_text


def test_pipeline_uses_configured_max_papers_for_selection_cap(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path, max_papers=5)
    papers = [make_paper(arxiv_id=f"2603.30{index:03d}", title=f"Paper {index}") for index in range(1, 7)]
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher(papers, available_dates=[date(2026, 3, 29)]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **kwargs: FakeRanker(**kwargs))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: _preferences())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 29))

    assert exit_code == 0
    assert FakeRanker.last_max_papers == 5
    assert FakeRanker.last_always_summarize_score == 90.0
