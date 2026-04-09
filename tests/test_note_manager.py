from datetime import date
from pathlib import Path

import pytest

from re_ass.note_manager import NoteManager
from tests.support import make_app_config, make_paper, make_processed_paper


def test_bootstrap_prepares_output_dirs_and_weekly_note(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))

    manager.bootstrap()

    assert manager.config.daily_notes_dir.exists()
    assert manager.config.weekly_notes_dir.exists()
    assert manager.weekly_note_path.exists()


def test_bootstrap_requires_existing_template_files(tmp_path: Path) -> None:
    config = make_app_config(tmp_path)
    config.daily_template.unlink()
    manager = NoteManager(config)

    with pytest.raises(FileNotFoundError, match="Daily template not found"):
        manager.bootstrap()


def test_update_daily_note_preserves_content_outside_managed_marker(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    daily_path = manager.config.daily_notes_dir / "2026-03-22.md"
    daily_path.write_text(
        (
            "# 2026-03-22\n\n"
            f"Intro\n\n{manager.config.daily_top_paper_heading}\n\nOld\n\n---\n## TASKS\n\nFooter\n"
        ),
        encoding="utf-8",
    )

    manager.update_daily_note(date(2026, 3, 22), make_processed_paper(tmp_path, micro_summary="First summary."))

    daily_text = daily_path.read_text(encoding="utf-8")
    assert "Intro" in daily_text
    assert "Footer" in daily_text
    assert "**Title:**" in daily_text
    assert "**Authors:** Bayer M. & Doe J." in daily_text
    assert "\n**Summary:** First summary." in daily_text
    assert "\n\n[[this-weeks-arxiv-papers|See all of this week's arXiv papers]]" in daily_text
    assert "**Summary:** First summary." in daily_text
    assert "## TASKS" in daily_text
    assert "First summary." in daily_text


def test_update_weekly_note_replaces_same_day_section(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap(reference_date=date(2026, 3, 24))

    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path, micro_summary="First summary.")], "Fresh synthesis.")
    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path, micro_summary="Second summary.")], "Updated synthesis.")

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert weekly_text.startswith("# ARXIV PAPERS FOR THE WEEK 23rd - 27th March 2026")
    assert "<!-- re-ass-week-start:" not in weekly_text
    assert weekly_text.count("### Tuesday 24th") == 1
    assert weekly_text.count("**Title:**") == 1
    assert "**Authors:** Bayer M. & Doe J." in weekly_text
    assert "\n**Summary:** Second summary." in weekly_text
    assert "Updated synthesis." in weekly_text
    assert "Second summary." in weekly_text


def test_update_weekly_note_appends_other_papers_of_interest_inside_day_block(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap(reference_date=date(2026, 3, 24))
    summarized = make_processed_paper(tmp_path, micro_summary="First summary.")
    interest = make_paper(
        arxiv_id="2603.22222",
        title="Interesting Overflow Paper",
        authors=("Kevin Wang", "Yingjie Peng"),
    )

    manager.update_weekly_note(
        date(2026, 3, 24),
        [summarized],
        "Fresh synthesis.",
        interest_papers=[interest],
    )

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "### Tuesday 24th" in weekly_text
    assert "\n\n**Other papers of interest:**\n\n" in weekly_text
    assert "- *Interesting Overflow Paper*, Wang K. & Peng Y., [arXiv:2603.22222](https://arxiv.org/abs/2603.22222)" in weekly_text
    assert "**Summary:** First summary.\n\n**Other papers of interest:**" in weekly_text


def test_featured_entries_use_same_truncated_author_rules_as_other_papers(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap(reference_date=date(2026, 3, 24))
    paper = make_paper(
        arxiv_id="2603.33333",
        title="Featured Paper",
        authors=("Sara Lebowitz", "Jane Doe", "Alex Smith"),
    )
    processed = make_processed_paper(tmp_path, paper=paper, micro_summary="Featured summary.")

    manager.update_daily_note(date(2026, 3, 24), processed)
    manager.update_weekly_note(date(2026, 3, 24), [processed], "Fresh synthesis.")

    daily_text = (manager.config.daily_notes_dir / "2026-03-24.md").read_text(encoding="utf-8")
    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")

    assert "**Authors:** Lebowitz S. et al." in daily_text
    assert "**Authors:** Lebowitz S. et al." in weekly_text


def test_update_daily_note_appends_block_when_heading_is_missing(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    daily_path = manager.config.daily_notes_dir / "2026-03-22.md"
    daily_path.write_text("# 2026-03-22\n\nNo managed heading here.\n", encoding="utf-8")

    manager.update_daily_note(date(2026, 3, 22), make_processed_paper(tmp_path))

    daily_text = daily_path.read_text(encoding="utf-8")
    assert daily_text.endswith("[[this-weeks-arxiv-papers|See all of this week's arXiv papers]]\n")
    assert manager.config.daily_top_paper_heading in daily_text


def test_update_daily_note_renders_obsidian_style_date_template(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.config.daily_template.write_text(
        "# DAILY NOTE: {{date:dddd Do MMMM YYYY}}\n\n" + manager.config.daily_top_paper_heading + "\n",
        encoding="utf-8",
    )

    manager.update_daily_note(date(2026, 3, 23), make_processed_paper(tmp_path))

    daily_text = (manager.config.daily_notes_dir / "2026-03-23.md").read_text(encoding="utf-8")
    assert daily_text.startswith("# DAILY NOTE: Monday 23rd March 2026\n")


def test_update_daily_note_renders_multiple_date_placeholders(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.config.daily_template.write_text(
        "# {{date}}\n\nDate title: {{date:dddd Do MMMM YYYY}}\n\n" + manager.config.daily_top_paper_heading + "\n",
        encoding="utf-8",
    )

    manager.update_daily_note(date(2026, 3, 23), make_processed_paper(tmp_path))

    daily_text = (manager.config.daily_notes_dir / "2026-03-23.md").read_text(encoding="utf-8")
    assert daily_text.startswith("# 2026-03-23\n")
    assert "Date title: Monday 23rd March 2026" in daily_text


def test_update_weekly_note_appends_missing_sections(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.weekly_note_path.write_text("# ARXIV PAPERS FOR THE WEEK\n\nNotes before managed sections.\n", encoding="utf-8")

    manager.update_weekly_note(date(2026, 3, 24), [make_processed_paper(tmp_path)], "Fresh synthesis.")

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "Notes before managed sections." in weekly_text
    assert manager.config.weekly_synthesis_heading in weekly_text
    assert "Fresh synthesis." in weekly_text
    assert manager.config.weekly_additions_heading in weekly_text
    assert "### Tuesday 24th" in weekly_text


def test_bootstrap_weekly_note_uses_heading_without_hidden_week_marker(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))

    manager.bootstrap(reference_date=date(2026, 3, 24))

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert weekly_text.startswith("# ARXIV PAPERS FOR THE WEEK 23rd - 27th March 2026\n\n")
    assert "<!-- re-ass-week-start:" not in weekly_text


def test_update_weekly_note_removes_legacy_week_marker_and_keeps_single_blank_line_before_separator(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.weekly_note_path.write_text(
        (
            "# ARXIV PAPERS FOR THE WEEK 23rd - 27th March 2026\n"
            "<!-- re-ass-week-start: 2026-03-23 -->\n"
            "## SYNTHESIS\n\n"
            "Old synthesis.\n\n"
            "---\n"
            "## DAILY ADDITIONS\n"
        ),
        encoding="utf-8",
    )

    manager.update_weekly_note(
        date(2026, 3, 24),
        [make_processed_paper(tmp_path, micro_summary="Fresh summary.")],
        "First paragraph.\n\nSecond paragraph.",
    )

    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "<!-- re-ass-week-start:" not in weekly_text
    assert "Second paragraph.\n\n---\n## DAILY ADDITIONS" in weekly_text
    assert "Second paragraph.\n\n\n---" not in weekly_text


def test_preview_weekly_additions_merges_new_day_without_writing_file(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.weekly_note_path.write_text(
        "# ARXIV PAPERS FOR THE WEEK 23rd - 27th March 2026\n\n"
        "## SYNTHESIS\n\n"
        "Existing synthesis.\n\n"
        "---\n"
        "## DAILY ADDITIONS\n\n"
        "### Monday 23rd\n\n"
        "**Title:** [[Existing]]\n\n"
        "**Summary:** Existing summary.\n",
        encoding="utf-8",
    )

    preview = manager.preview_weekly_additions(date(2026, 3, 24), [make_processed_paper(tmp_path, micro_summary="New summary.")])

    assert "### Monday 23rd" in preview
    assert "### Tuesday 24th" in preview
    assert "New summary." in preview
    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")
    assert "### Tuesday 24th" not in weekly_text


def test_rotate_weekly_note_archives_previous_note_on_rotation_day(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()
    manager.weekly_note_path.write_text(
        "# ARXIV PAPERS FOR THE WEEK 16th - 20th March 2026\n\n## SYNTHESIS\n\nOld synthesis.\n\n---\n## DAILY ADDITIONS\n\n### Monday 16th\n\n**Title:** [[Entry]]\n\n**Summary:** Test summary.\n",
        encoding="utf-8",
    )

    rotated = manager.rotate_weekly_note_if_needed(date(2026, 3, 23))

    archived = manager.config.weekly_notes_dir / "2026-03-16-weekly-arxiv.md"
    assert rotated is True
    assert archived.exists()
    assert "Old synthesis." in archived.read_text(encoding="utf-8")


def test_catch_up_writes_the_target_week_without_reusing_a_stale_archive(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap(reference_date=date(2026, 3, 25))
    manager.weekly_note_path.write_text(
        "# ARXIV PAPERS FOR THE WEEK 9th - 13th March 2026\n"
        "<!-- re-ass-week-start: 2026-03-09 -->\n\n"
        "## SYNTHESIS\n\n"
        "Old synthesis.\n\n"
        "---\n"
        "## DAILY ADDITIONS\n\n"
        "### Tuesday 10th\n\n"
        "**Title:** [[Old Paper]]\n\n"
        "**Summary:** Old summary.\n",
        encoding="utf-8",
    )

    rotated = manager.rotate_weekly_note_if_needed(date(2026, 3, 25))
    archived_stale_week = manager.config.weekly_notes_dir / "2026-03-09-weekly-arxiv.md"
    assert rotated is True
    assert archived_stale_week.exists()

    manager.update_weekly_note(
        date(2026, 3, 17),
        [make_processed_paper(tmp_path, micro_summary="Catch-up summary.")],
        "Fresh synthesis.",
        reference_date=date(2026, 3, 25),
    )

    catch_up_week = manager.config.weekly_notes_dir / "2026-03-16-weekly-arxiv.md"
    catch_up_text = catch_up_week.read_text(encoding="utf-8")
    assert catch_up_week.exists()
    assert catch_up_text.startswith("# ARXIV PAPERS FOR THE WEEK 16th - 20th March 2026")
    assert "Catch-up summary." in catch_up_text
    assert "### Tuesday 10th" not in catch_up_text
    assert "Old summary." not in catch_up_text
    assert "<!-- re-ass-week-start:" not in catch_up_text


def test_update_notes_uses_configured_managed_headings(tmp_path: Path) -> None:
    config = make_app_config(
        tmp_path,
        daily_top_paper_heading="## Highlighted Paper",
        weekly_synthesis_heading="## Weekly Synthesis",
        weekly_additions_heading="## Weekly Additions",
    )
    manager = NoteManager(config)
    manager.config.daily_template.write_text(
        "# {{date}}\n\n## Tasks\n\n- \n\n## Highlighted Paper\n",
        encoding="utf-8",
    )
    manager.config.weekly_template.write_text(
        "# ARXIV PAPERS FOR THE WEEK\n\n## Weekly Synthesis\n\n---\n## Weekly Additions\n",
        encoding="utf-8",
    )
    manager.bootstrap(reference_date=date(2026, 3, 23))

    paper = make_processed_paper(tmp_path, micro_summary="Custom heading summary.")
    manager.update_daily_note(date(2026, 3, 23), paper)
    manager.update_weekly_note(date(2026, 3, 23), [paper], "Custom synthesis.")

    daily_text = (manager.config.daily_notes_dir / "2026-03-23.md").read_text(encoding="utf-8")
    weekly_text = manager.weekly_note_path.read_text(encoding="utf-8")

    assert "## Highlighted Paper" in daily_text
    assert "**Summary:** Custom heading summary." in daily_text
    assert "## Weekly Synthesis" in weekly_text
    assert "Custom synthesis." in weekly_text
    assert "## Weekly Additions" in weekly_text
    assert "### Monday 23rd" in weekly_text


def test_update_daily_note_links_to_archived_weekly_note_for_catch_up_days(tmp_path: Path) -> None:
    manager = NoteManager(make_app_config(tmp_path))
    manager.bootstrap()

    manager.update_daily_note(
        date(2026, 3, 21),
        make_processed_paper(tmp_path, micro_summary="Catch-up summary."),
        reference_date=date(2026, 3, 25),
    )

    daily_text = (manager.config.daily_notes_dir / "2026-03-21.md").read_text(encoding="utf-8")
    assert "[[2026-03-16-weekly-arxiv|See all of this week's arXiv papers]]" in daily_text
