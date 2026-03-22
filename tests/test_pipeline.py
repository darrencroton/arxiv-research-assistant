from datetime import date, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from re_ass.generation_service import GenerationError
from re_ass.note_manager import NoteManager
from re_ass.pipeline import run
from re_ass.paper_identity import derive_identity
from tests.support import make_app_config, make_paper


def _build_selection(candidates, *, selected=None):
    selected = list(selected or candidates)
    retrieval_pool = []
    reranked = []
    final_selected = []

    for offset, paper in enumerate(candidates):
        identity = derive_identity(paper)
        retrieval_pool.append(
            SimpleNamespace(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                lexical_score=max(0.0, 1.0 - offset * 0.1),
                semantic_score=max(0.0, 0.9 - offset * 0.1),
                fused_score=max(0.0, 0.95 - offset * 0.1),
                best_priority_index=offset,
                matched_priority_count=max(1, len(candidates) - offset),
                matched_priorities=("Agents",),
                retrieval_channels=("lexical", "semantic"),
                retrieval_notes=("fixture",),
            )
        )
        reranked.append(
            SimpleNamespace(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                lexical_score=max(0.0, 1.0 - offset * 0.1),
                semantic_score=max(0.0, 0.9 - offset * 0.1),
                fused_score=max(0.0, 0.95 - offset * 0.1),
                rerank_score=float(100 - offset),
                rationale=f"Reason for {paper.title}",
                best_priority_index=offset,
                matched_priority_count=max(1, len(candidates) - offset),
                matched_priorities=("Agents",),
                retrieval_channels=("lexical", "semantic"),
                retrieval_notes=("fixture",),
            )
        )

    for offset, paper in enumerate(selected):
        identity = derive_identity(paper)
        final_selected.append(
            SimpleNamespace(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                selection_score=float(98 - offset),
                rerank_score=float(100 - offset),
                rationale=f"Selected {paper.title}",
            )
        )

    return SimpleNamespace(
        selected_papers=selected,
        candidate_count=len(candidates),
        retrieval_pool=retrieval_pool,
        reranked=reranked,
        selected=final_selected,
        final_pool=reranked,
        used_passthrough=False,
        shortlist=retrieval_pool,
    )


class FakeFetcher:
    last_call = None

    def __init__(self, papers):
        self.papers = papers

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

    def generate_weekly_synthesis(self, _existing_synthesis: str, papers):
        return f"Synthesis for {len(papers)} papers."


class FakeRanker:
    def __init__(self, *, selection=None) -> None:
        self.selection = selection

    def select_top_papers(self, _preferences, candidates, *, max_papers):
        selection = self.selection or _build_selection(candidates, selected=candidates[:max_papers])
        return SimpleNamespace(
            selected_papers=list(selection.selected_papers)[:max_papers],
            candidate_count=selection.candidate_count,
            retrieval_pool=selection.retrieval_pool,
            reranked=selection.reranked,
            selected=selection.selected[:max_papers],
            final_pool=selection.final_pool,
            used_passthrough=getattr(selection, "used_passthrough", False),
            shortlist=selection.retrieval_pool,
        )


def test_pipeline_returns_zero_and_writes_run_summary_when_no_new_papers(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **_kwargs: FakeRanker())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())

    exit_code = run(config, date(2026, 3, 22))

    assert exit_code == 0
    run_summaries = list(config.state_runs_dir.glob("*.json"))
    assert len(run_summaries) == 1
    assert not any(config.daily_dir.glob("*.md"))


def test_pipeline_continues_after_non_fatal_per_paper_failure(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30001", title="Working Paper"),
        make_paper(arxiv_id="2603.30002", title="Broken Paper"),
    ]
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher(papers))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **_kwargs: FakeRanker(selection=_build_selection(papers, selected=papers)))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService(failing_titles={"Broken Paper"}))

    exit_code = run(config, date(2026, 3, 24))

    assert exit_code == 0
    assert (config.papers_dir / "Bayer et al - 2026 - Working Paper [arXiv 2603.30001].md").exists()
    assert not (config.papers_dir / "Bayer et al - 2026 - Broken Paper [arXiv 2603.30002].md").exists()
    assert (config.state_papers_dir / "arxiv_2603.30002.json").exists()
    assert "Working Paper" in (config.daily_dir / "2026-03-24.md").read_text(encoding="utf-8")
    weekly_note_text = (config.weekly_dir / config.weekly_note_file).read_text(encoding="utf-8")
    assert "Working Paper" in weekly_note_text
    assert "Broken Paper" not in weekly_note_text


def test_pipeline_fails_hard_when_provider_construction_fails(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: (_ for _ in ()).throw(ValueError("provider missing")))

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 1
    assert not any(config.papers_dir.glob("*.md"))
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
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **_kwargs: FakeRanker())
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "re_ass.pipeline.GenerationService",
        lambda **_kwargs: FakeGenerationService(note_content_by_title={"Verbatim Paper": raw_summary}),
    )

    exit_code = run(config, date(2026, 3, 26))

    assert exit_code == 0
    note_path = config.papers_dir / "Bayer et al - 2026 - Verbatim Paper [arXiv 2603.30011].md"
    assert note_path.read_text(encoding="utf-8") == raw_summary


def test_pipeline_leaves_papers_retryable_when_note_update_fails(tmp_path: Path, monkeypatch) -> None:
    class FailingNoteManager(NoteManager):
        def update_daily_note(self, run_date, top_paper):
            raise ValueError("broken daily template")

    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2603.30031", title="Retryable Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **_kwargs: FakeRanker())
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())
    monkeypatch.setattr("re_ass.pipeline.NoteManager", FailingNoteManager)

    exit_code = run(config, date(2026, 3, 27))

    assert exit_code == 1
    record_path = config.state_papers_dir / "arxiv_2603.30031.json"
    assert record_path.exists()
    assert '"status": "note_written"' in record_path.read_text(encoding="utf-8")


def test_pipeline_backfill_uses_stable_local_day_interval(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2603.30035", title="Interval Paper")
    fixed_tz = timezone(timedelta(hours=11))
    monkeypatch.setattr("re_ass.pipeline._local_timezone", lambda: fixed_tz)
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **_kwargs: FakeRanker())
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 23), backfill=True)

    assert exit_code == 0
    assert FakeFetcher.last_call["start"].isoformat() == "2026-03-22T13:00:00+00:00"
    assert FakeFetcher.last_call["end"].isoformat() == "2026-03-23T13:00:00+00:00"


def test_pipeline_backfill_leaves_current_weekly_summary_unchanged(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    manager = NoteManager(config)
    manager.bootstrap()
    manager.weekly_note_path.write_text(
        "# This Week's ArXiv Overview\n\n"
        "## Synthesis\n"
        "<!-- re-ass:weekly-synthesis:start -->\n"
        "Live synthesis.\n"
        "<!-- re-ass:weekly-synthesis:end -->\n\n"
        "---\n"
        "## Daily Additions\n"
        "<!-- re-ass:weekly-daily-additions:start -->\n"
        "### Sunday\n"
        "- Existing summary\n"
        "<!-- re-ass:weekly-daily-additions:end -->\n",
        encoding="utf-8",
    )
    paper = make_paper(arxiv_id="2603.30041", title="Backfill Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper]))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **_kwargs: FakeRanker())
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 23), backfill=True)

    assert exit_code == 0
    assert "Backfill Paper" in (config.daily_dir / "2026-03-23.md").read_text(encoding="utf-8")
    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "Live synthesis." in weekly_text
    assert "Backfill Paper" not in weekly_text
    assert not (config.weekly_dir / "2026-03-23-weekly-arxiv.md").exists()


def test_pipeline_records_interval_and_ranking_diagnostics(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    papers = [
        make_paper(arxiv_id="2603.30051", title="Ranked One"),
        make_paper(arxiv_id="2603.30052", title="Ranked Two"),
    ]
    selection = _build_selection(papers, selected=papers[:1])
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher(papers))
    monkeypatch.setattr("re_ass.pipeline.PaperRanker", lambda **_kwargs: FakeRanker(selection=selection))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 28))

    assert exit_code == 0
    summary_text = next(config.state_runs_dir.glob("*.json")).read_text(encoding="utf-8")
    assert '"interval_start"' in summary_text
    assert '"interval_end"' in summary_text
    assert '"candidate_count": 2' in summary_text
    assert '"retrieval_pool_size": 2' in summary_text
    assert '"shortlist_size": 2' in summary_text
    assert '"final_pool_size": 2' in summary_text
    assert '"ranking_results"' in summary_text
    assert '"final_selection"' in summary_text
