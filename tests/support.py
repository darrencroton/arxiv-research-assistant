from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from re_ass.models import ArxivPaper, ProcessedPaper
from re_ass.paper_identity import derive_identity
from re_ass.settings import AppConfig, LlmConfig


def _seed_test_user_files(config: AppConfig) -> None:
    config.daily_template.parent.mkdir(parents=True, exist_ok=True)
    config.weekly_template.parent.mkdir(parents=True, exist_ok=True)
    config.preferences_file.parent.mkdir(parents=True, exist_ok=True)

    if not config.daily_template.exists():
        config.daily_template.write_text(
            "# {{date}}\n\n" + config.daily_top_paper_heading + "\n",
            encoding="utf-8",
        )
    if not config.weekly_template.exists():
        config.weekly_template.write_text(
            "\n".join(
                [
                    "# ARXIV PAPERS FOR THE WEEK",
                    "",
                    config.weekly_synthesis_heading,
                    "*(A synthesis of this week's papers will be automatically generated here. Max 100 words.)*",
                    "",
                    "---",
                    config.weekly_additions_heading,
                    "",
                ]
            ),
            encoding="utf-8",
        )
    if not config.preferences_file.exists():
        config.preferences_file.write_text(
            "# Arxiv Priorities\n\n"
            "## Categories\n"
            "- astro-ph.CO\n\n"
            "## Priorities\n"
            "1. Example priority\n",
            encoding="utf-8",
        )


def make_app_config(tmp_path: Path, **overrides) -> AppConfig:
    config = AppConfig(
        project_root=tmp_path,
        output_root=tmp_path / "output",
        summaries_dir=tmp_path / "output" / "summaries",
        daily_notes_dir=tmp_path / "output" / "daily-notes",
        weekly_notes_dir=tmp_path / "output" / "weekly-notes",
        pdfs_dir=tmp_path / "output" / "pdfs",
        state_root=tmp_path / "state",
        state_papers_dir=tmp_path / "state" / "papers",
        state_runs_dir=tmp_path / "state" / "runs",
        logs_root=tmp_path / "logs",
        history_log_file=tmp_path / "logs" / "history.log",
        last_run_log_file=tmp_path / "logs" / "last-run.log",
        daily_template=tmp_path / "user_preferences" / "templates" / "daily-note-template.md",
        weekly_template=tmp_path / "user_preferences" / "templates" / "weekly-note-template.md",
        preferences_file=tmp_path / "user_preferences" / "preferences.md",
        link_style="wikilink",
        weekly_note_file="this-weeks-arxiv-papers.md",
        rotation_day="monday",
        archive_name_pattern="{date}-weekly-arxiv.md",
        daily_top_paper_heading="## TODAY'S TOP PAPER",
        weekly_synthesis_heading="## SYNTHESIS",
        weekly_additions_heading="## DAILY ADDITIONS",
        weekly_synthesis_word_limit_start=100,
        weekly_synthesis_word_limit_end=200,
        max_papers=10,
        arxiv_page_size=50,
        min_selection_score=75.0,
        llm=LlmConfig(
            mode="cli",
            provider="claude",
            model=None,
            effort=None,
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
    config = replace(config, **overrides)
    _seed_test_user_files(config)
    return config


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
    note_path = tmp_path / "output" / "summaries" / identity.note_filename
    pdf_path = tmp_path / "output" / "pdfs" / identity.pdf_filename
    return ProcessedPaper(
        paper=paper,
        paper_key=identity.paper_key,
        filename_stem=identity.filename_stem,
        note_path=note_path,
        pdf_path=pdf_path,
        micro_summary=micro_summary,
    )
