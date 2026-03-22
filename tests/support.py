from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from re_ass.models import ArxivPaper, ProcessedPaper
from re_ass.paper_identity import derive_identity
from re_ass.settings import AppConfig, LlmConfig


def make_app_config(tmp_path: Path, **overrides) -> AppConfig:
    config = AppConfig(
        project_root=tmp_path,
        output_root=tmp_path / "output",
        papers_dir=tmp_path / "output" / "papers",
        daily_dir=tmp_path / "output" / "daily",
        weekly_dir=tmp_path / "output" / "weekly",
        processed_root=tmp_path / "processed",
        state_root=tmp_path / "state",
        state_papers_dir=tmp_path / "state" / "papers",
        state_runs_dir=tmp_path / "state" / "runs",
        logs_root=tmp_path / "logs",
        history_log_file=tmp_path / "logs" / "history.log",
        last_run_log_file=tmp_path / "logs" / "last-run.log",
        daily_template=tmp_path / "templates" / "daily-note-template.md",
        weekly_template=tmp_path / "templates" / "weekly-note-template.md",
        preferences_file=tmp_path / "preferences.md",
        link_style="wikilink",
        weekly_note_file="this-weeks-arxiv-papers.md",
        rotation_day="monday",
        archive_name_pattern="{date}-weekly-arxiv.md",
        max_papers=10,
        arxiv_page_size=50,
        ranking_shortlist_size=24,
        ranking_final_pool_size=24,
        ranking_min_selection_score=75.0,
        ranking_passthrough_candidate_count=50,
        default_categories=("astro-ph.CO",),
        llm=LlmConfig(
            mode="cli",
            provider="claude",
            model=None,
            timeout_seconds=60,
            max_output_tokens=12288,
            temperature=0.2,
            retry_attempts=3,
            prompt_debug_file=tmp_path / "tmp" / "paper_summariser" / "prompt.txt",
            download_timeout_seconds=120,
            max_pdf_size_mb=100,
            marker_timeout_seconds=300,
            ollama_base_url="http://localhost:11434",
        ),
    )
    return replace(config, **overrides)


def make_paper(
    *,
    arxiv_id: str = "2603.15732",
    title: str = "Field-Level Inference from Galaxies: BAO Reconstruction",
    summary: str = "This paper studies field-level inference for BAO reconstruction.",
    authors: tuple[str, ...] = ("Marius Bayer", "Jane Doe"),
    primary_category: str = "astro-ph.CO",
    categories: tuple[str, ...] = ("astro-ph.CO",),
    published: datetime | None = None,
) -> ArxivPaper:
    published = published or datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    return ArxivPaper(
        title=title,
        summary=summary,
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        entry_id=f"https://arxiv.org/abs/{arxiv_id}",
        authors=authors,
        primary_category=primary_category,
        categories=categories,
        published=published,
        updated=published,
    )


def make_processed_paper(tmp_path: Path, *, paper: ArxivPaper | None = None, micro_summary: str = "Short summary.") -> ProcessedPaper:
    paper = paper or make_paper()
    identity = derive_identity(paper)
    note_path = tmp_path / "output" / "papers" / identity.note_filename
    pdf_path = tmp_path / "processed" / identity.pdf_filename
    return ProcessedPaper(
        paper=paper,
        paper_key=identity.paper_key,
        filename_stem=identity.filename_stem,
        note_path=note_path,
        pdf_path=pdf_path,
        micro_summary=micro_summary,
    )
