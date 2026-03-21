from datetime import date, datetime, timezone
from pathlib import Path

from re_ass.models import ArxivPaper, ProcessedPaper
from re_ass.settings import AppConfig, DEFAULT_WEEKLY_TEMPLATE
from re_ass.vault_manager import VaultManager


def make_config(tmp_path: Path) -> AppConfig:
    vault_root = tmp_path / "vault"
    return AppConfig(
        project_root=tmp_path,
        vault_root=vault_root,
        preferences_file=vault_root / "re-ass-preferences.md",
        weekly_note_file=vault_root / "this-weeks-arxiv-papers.md",
        daily_dir=vault_root / "Daily",
        papers_dir=vault_root / "Papers",
        weekly_archive_dir=vault_root / "Weekly_Archive",
        templates_dir=vault_root / "Templates",
        weekly_template_file=vault_root / "Templates" / "weekly-arxiv-template.md",
        max_papers=3,
        fetch_window_hours=24,
        fallback_window_hours=168,
        arxiv_max_results=50,
        default_categories=("cs.AI", "cs.CL"),
        llm_enabled=False,
        llm_command_prefix=("claude", "-p"),
        llm_timeout_seconds=60,
        allow_local_paper_note_fallback=True,
    )


def make_processed_paper(note_name: str, summary: str) -> ProcessedPaper:
    paper = ArxivPaper(
        title=note_name,
        summary=summary,
        arxiv_url=f"https://arxiv.org/abs/{note_name}",
        entry_id=f"https://arxiv.org/abs/{note_name}",
        authors=("Author One",),
        primary_category="cs.AI",
        categories=("cs.AI",),
        published=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
        updated=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
    )
    return ProcessedPaper(
        paper=paper,
        note_name=note_name,
        note_path=Path("/tmp") / f"{note_name}.md",
        micro_summary=summary,
    )


def test_bootstrap_seeds_expected_files(tmp_path: Path) -> None:
    manager = VaultManager(make_config(tmp_path))

    manager.bootstrap()

    assert manager.config.preferences_file.exists()
    assert manager.config.weekly_template_file.exists()
    assert manager.config.weekly_note_file.read_text(encoding="utf-8") == DEFAULT_WEEKLY_TEMPLATE


def test_update_daily_note_replaces_existing_section(tmp_path: Path) -> None:
    manager = VaultManager(make_config(tmp_path))
    manager.bootstrap()

    manager.update_daily_note(date(2026, 3, 21), make_processed_paper("Paper One", "First summary."))
    manager.update_daily_note(date(2026, 3, 21), make_processed_paper("Paper Two", "Second summary."))

    daily_note = (manager.config.daily_dir / "2026-03-21.md").read_text(encoding="utf-8")
    assert daily_note.count("## Today's Top Paper") == 1
    assert "[[Paper Two]] - Second summary." in daily_note


def test_rotate_weekly_note_moves_previous_note_on_sunday(tmp_path: Path) -> None:
    manager = VaultManager(make_config(tmp_path))
    manager.bootstrap()
    manager.config.weekly_note_file.write_text(
        "# This Week's ArXiv Overview\n\n## Synthesis\nOld synthesis.\n\n---\n## Daily Additions\n\n### Saturday\n- [[Paper]] - Summary.\n",
        encoding="utf-8",
    )

    rotated = manager.rotate_weekly_note_if_needed(date(2026, 3, 22))

    archive_path = manager.config.weekly_archive_dir / "2026-03-22-arxiv.md"
    assert rotated is True
    assert archive_path.exists()
    assert "Old synthesis." in archive_path.read_text(encoding="utf-8")
    assert manager.config.weekly_note_file.read_text(encoding="utf-8") == DEFAULT_WEEKLY_TEMPLATE


def test_rotate_weekly_note_skips_pristine_template_on_first_sunday(tmp_path: Path) -> None:
    manager = VaultManager(make_config(tmp_path))
    manager.bootstrap()

    rotated = manager.rotate_weekly_note_if_needed(date(2026, 3, 22))

    archive_path = manager.config.weekly_archive_dir / "2026-03-22-arxiv.md"
    assert rotated is False
    assert not archive_path.exists()
    assert manager.config.weekly_note_file.read_text(encoding="utf-8") == DEFAULT_WEEKLY_TEMPLATE


def test_update_weekly_note_replaces_same_day_section(tmp_path: Path) -> None:
    manager = VaultManager(make_config(tmp_path))
    manager.bootstrap()

    manager.update_weekly_note(
        date(2026, 3, 21),
        [make_processed_paper("Paper One", "First summary.")],
        "Fresh weekly synthesis.",
    )
    manager.update_weekly_note(
        date(2026, 3, 21),
        [make_processed_paper("Paper Two", "Second summary.")],
        "Updated weekly synthesis.",
    )

    weekly_note = manager.config.weekly_note_file.read_text(encoding="utf-8")
    assert weekly_note.count("### Saturday") == 1
    assert "Updated weekly synthesis." in weekly_note
    assert "[[Paper Two]] - Second summary." in weekly_note
