from datetime import date
from pathlib import Path

from re_ass.generation_service import GenerationError
from re_ass.note_manager import NoteManager
from re_ass.pipeline import run
from re_ass.settings import LlmConfig
from tests.support import make_app_config, make_paper


class FakeFetcher:
    def __init__(self, papers):
        self.papers = papers

    def fetch_top_papers(self, *_args, **_kwargs):
        return list(self.papers)


class FakeGenerationService:
    def __init__(self, *, failing_titles: set[str] | None = None) -> None:
        self.failing_titles = failing_titles or set()

    def generate_micro_summary(self, paper):
        return f"Summary for {paper.title}"

    def stage_pdf_download(self, paper, destination_dir: Path):
        staged = destination_dir / f"{paper.title}.pdf"
        staged.write_bytes(b"%PDF-1.4 fake")
        return staged

    def build_paper_note_content(self, paper, _staged_source_path: Path):
        if paper.title in self.failing_titles:
            raise GenerationError("simulated failure")
        return f"# {paper.title}\n\n## Abstract\n{paper.summary}\n\n## Notes\nGenerated.\n"

    def generate_weekly_synthesis(self, _existing_synthesis: str, papers):
        return f"Synthesis for {len(papers)} papers."


def test_pipeline_returns_zero_and_writes_run_summary_when_no_new_papers(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([]))
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
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService(failing_titles={"Broken Paper"}))

    exit_code = run(config, date(2026, 3, 24))

    assert exit_code == 0
    assert (config.papers_dir / "Bayer et al - 2026 - Working Paper [arXiv 2603.30001].md").exists()
    assert (config.state_papers_dir / "arxiv_2603.30002.json").exists()
    assert "Working Paper" in (config.daily_dir / "2026-03-24.md").read_text(encoding="utf-8")
    weekly_note_text = (config.weekly_dir / config.weekly_note_file).read_text(encoding="utf-8")
    assert "Working Paper" in weekly_note_text
    assert "Broken Paper" not in weekly_note_text


def test_pipeline_uses_deterministic_fallback_path_when_llm_is_disabled(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2603.30011", title="Fallback Paper", summary="Sentence one. Sentence two. Sentence three.")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper]))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    def fake_download(paper, destination_dir, _config):
        staged_path = destination_dir / f"{paper.title}.pdf"
        staged_path.write_bytes(b"%PDF-1.4")
        return staged_path

    monkeypatch.setattr("re_ass.generation_service.download_arxiv_pdf", fake_download)

    exit_code = run(config, date(2026, 3, 25))

    assert exit_code == 0
    note_path = config.papers_dir / "Bayer et al - 2026 - Fallback Paper [arXiv 2603.30011].md"
    assert note_path.exists()
    assert "## Notes" in note_path.read_text(encoding="utf-8")


def test_pipeline_accepts_mocked_enabled_llm_path(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(
        tmp_path,
        llm=LlmConfig(
            enabled=True,
            mode="cli",
            provider="claude",
            model=None,
            timeout_seconds=60,
            max_output_tokens=12288,
            temperature=0.2,
            retry_attempts=3,
            allow_local_paper_note_fallback=True,
            prompt_debug_file=tmp_path / "archive" / "prompt.txt",
            download_timeout_seconds=120,
            max_pdf_size_mb=100,
            marker_timeout_seconds=300,
            ollama_base_url="http://localhost:11434",
        ),
    )
    paper = make_paper(arxiv_id="2603.30021", title="Mocked LLM Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper]))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 26))

    assert exit_code == 0
    assert "Mocked LLM Paper" in (config.weekly_dir / config.weekly_note_file).read_text(encoding="utf-8")


def test_pipeline_leaves_papers_retryable_when_note_update_fails(tmp_path: Path, monkeypatch) -> None:
    class FailingNoteManager(NoteManager):
        def update_daily_note(self, run_date, top_paper):
            raise ValueError("broken daily template")

    config = make_app_config(tmp_path)
    paper = make_paper(arxiv_id="2603.30031", title="Retryable Paper")
    monkeypatch.setattr("re_ass.pipeline.ArxivFetcher", lambda **_kwargs: FakeFetcher([paper]))
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())
    monkeypatch.setattr("re_ass.pipeline.NoteManager", FailingNoteManager)

    exit_code = run(config, date(2026, 3, 27))

    assert exit_code == 1
    record_path = config.state_papers_dir / "arxiv_2603.30031.json"
    assert record_path.exists()
    assert '"status": "note_written"' in record_path.read_text(encoding="utf-8")


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
    monkeypatch.setattr("re_ass.pipeline.load_preferences", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("re_ass.pipeline.GenerationService", lambda **_kwargs: FakeGenerationService())

    exit_code = run(config, date(2026, 3, 23), backfill=True)

    assert exit_code == 0
    assert "Backfill Paper" in (config.daily_dir / "2026-03-23.md").read_text(encoding="utf-8")
    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "Live synthesis." in weekly_text
    assert "Backfill Paper" not in weekly_text
    assert not (config.weekly_dir / "2026-03-23-weekly-arxiv.md").exists()
