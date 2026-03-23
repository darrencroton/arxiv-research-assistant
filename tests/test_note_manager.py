from datetime import date
from pathlib import Path

from re_ass.note_manager import NoteManager
from tests.support import make_app_config, make_processed_paper


def test_bootstrap_seeds_preferences_templates_and_weekly_note(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))

    manager.bootstrap()

    assert manager.config.preferences_file.exists()
    assert manager.config.daily_template.exists()
    assert manager.config.weekly_template.exists()
    assert manager.weekly_note_path.exists()


def test_update_daily_note_preserves_content_outside_managed_marker(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    daily_path = manager.config.daily_notes_dir / "2026-03-22.md"
    daily_path.write_text(
        "# 2026-03-22\n\nIntro\n\n##  TODAY'S TOP PAPER\n\nOld\n\n---\n## TASKS\n\nFooter\n",
        encoding="utf-8",
    )

    manager.update_daily_note(date(2026, 3, 22), make_processed_paper(tmp_path, micro_summary="First summary."))

    daily_text = daily_path.read_text(encoding="utf-8")
    assert "Intro" in daily_text
    assert "Footer" in daily_text
    assert "**Title:**" in daily_text
    assert "**Authors:**" not in daily_text
    assert "\n\n**Summary:** First summary." in daily_text
    assert "**Summary:** First summary." in daily_text
    assert "## TASKS" in daily_text
    assert "First summary." in daily_text


def test_update_weekly_note_replaces_same_day_section(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()

    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path, micro_summary="First summary.")], "Fresh synthesis.")
    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path, micro_summary="Second summary.")], "Updated synthesis.")

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert weekly_text.startswith("# This Week's ArXiv Overview 23-29 March 2026")
    assert weekly_text.count("### Tuesday 24th") == 1
    assert "Updated synthesis." in weekly_text
    assert "Second summary." in weekly_text


def test_update_daily_note_appends_block_when_heading_is_missing(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    daily_path = manager.config.daily_notes_dir / "2026-03-22.md"
    daily_path.write_text("# 2026-03-22\n\nNo managed heading here.\n", encoding="utf-8")

    manager.update_daily_note(date(2026, 3, 22), make_processed_paper(tmp_path))

    daily_text = daily_path.read_text(encoding="utf-8")
    assert daily_text.endswith("**Summary:** Short summary.\n")
    assert "##  TODAY'S TOP PAPER" in daily_text


def test_update_weekly_note_appends_missing_sections(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.weekly_note_path.write_text("# This Week's ArXiv Overview\n\nNotes before managed sections.\n", encoding="utf-8")

    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path)], "Fresh synthesis.")

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "Notes before managed sections." in weekly_text
    assert "## Synthesis" in weekly_text
    assert "Fresh synthesis." in weekly_text
    assert "## Daily Additions" in weekly_text
    assert "### Tuesday 24th" in weekly_text


def test_rotate_weekly_note_archives_previous_note_on_rotation_day(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.weekly_note_path.write_text(
        "# This Week's ArXiv Overview 16-22 March 2026\n\n## Synthesis\n\nOld synthesis.\n\n---\n## Daily Additions\n\n### Monday 16th\n- Entry\n",
        encoding="utf-8",
    )

    rotated = manager.rotate_weekly_note_if_needed(date(2026, 3, 23))

    archived = manager.config.weekly_notes_dir / "2026-03-23-weekly-arxiv.md"
    assert rotated is True
    assert archived.exists()
    assert "Old synthesis." in archived.read_text(encoding="utf-8")
