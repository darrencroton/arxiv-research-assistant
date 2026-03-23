from datetime import date
from pathlib import Path

import pytest

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
        "# 2026-03-22\n\nIntro\n\n<!-- re-ass:daily-top-paper:start -->\nOld\n<!-- re-ass:daily-top-paper:end -->\n\nFooter\n",
        encoding="utf-8",
    )

    manager.update_daily_note(date(2026, 3, 22), make_processed_paper(tmp_path, micro_summary="First summary."))

    daily_text = daily_path.read_text(encoding="utf-8")
    assert "Intro" in daily_text
    assert "Footer" in daily_text
    assert "First summary." in daily_text


def test_update_weekly_note_replaces_same_day_section(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()

    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path, micro_summary="First summary.")], "Fresh synthesis.")
    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path, micro_summary="Second summary.")], "Updated synthesis.")

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert weekly_text.count("### Tuesday") == 1
    assert "Updated synthesis." in weekly_text
    assert "Second summary." in weekly_text


def test_update_daily_note_raises_when_marker_is_missing(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    daily_path = manager.config.daily_notes_dir / "2026-03-22.md"
    daily_path.write_text("# 2026-03-22\n\nNo markers here.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="daily-top-paper"):
        manager.update_daily_note(date(2026, 3, 22), make_processed_paper(tmp_path))


def test_rotate_weekly_note_archives_previous_note_on_rotation_day(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.weekly_note_path.write_text(
        "# This Week's ArXiv Overview\n\n## Synthesis\n<!-- re-ass:weekly-synthesis:start -->\nOld synthesis.\n<!-- re-ass:weekly-synthesis:end -->\n\n---\n## Daily Additions\n<!-- re-ass:weekly-daily-additions:start -->\n### Monday\n- Entry\n<!-- re-ass:weekly-daily-additions:end -->\n",
        encoding="utf-8",
    )

    rotated = manager.rotate_weekly_note_if_needed(date(2026, 3, 23))

    archived = manager.config.weekly_notes_dir / "2026-03-23-weekly-arxiv.md"
    assert rotated is True
    assert archived.exists()
    assert "Old synthesis." in archived.read_text(encoding="utf-8")
