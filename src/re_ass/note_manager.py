"""Daily and weekly note management for re-ass."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import shutil

from re_ass.models import ProcessedPaper
from re_ass.paper_identity import render_link
from re_ass.settings import AppConfig


_ROTATION_DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

DEFAULT_DAILY_TEMPLATE = """# {{date}}

<!-- re-ass:daily-top-paper:start -->
## Today's Top Paper
<!-- re-ass:daily-top-paper:end -->
"""

DEFAULT_WEEKLY_TEMPLATE = """# This Week's ArXiv Overview

## Synthesis
<!-- re-ass:weekly-synthesis:start -->
*(A synthesis of this week's papers will be automatically generated here. Max 100 words.)*
<!-- re-ass:weekly-synthesis:end -->

---
## Daily Additions
<!-- re-ass:weekly-daily-additions:start -->
<!-- re-ass:weekly-daily-additions:end -->
"""

DEFAULT_PREFERENCES_FILE = """# Arxiv Priorities

## Categories
- astro-ph.CO
- astro-ph.GA

## Output
- Top papers: 3

## Priorities
1. Little red dots
2. Black holes and AGN
3. Semi-analytic galaxy formation models
4. High redshift galaxy formation
5. Two point correlation function
6. Galaxy environments
"""


def _replace_marker_block(text: str, marker_name: str, body: str) -> str:
    pattern = re.compile(
        rf"(<!-- re-ass:{re.escape(marker_name)}:start -->)(?P<body>.*?)(<!-- re-ass:{re.escape(marker_name)}:end -->)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"Missing managed marker '{marker_name}' in note content.")

    replacement = f"{match.group(1)}\n{body.rstrip()}\n{match.group(3)}"
    return text[:match.start()] + replacement + text[match.end():]


def _read_marker_block(text: str, marker_name: str) -> str:
    pattern = re.compile(
        rf"<!-- re-ass:{re.escape(marker_name)}:start -->(?P<body>.*?)<!-- re-ass:{re.escape(marker_name)}:end -->",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"Missing managed marker '{marker_name}' in note content.")
    return match.group("body").strip()


def _upsert_day_block(existing_body: str, day_name: str, new_block: str) -> str:
    pattern = re.compile(rf"(?ms)^### {re.escape(day_name)}\n.*?(?=^### |\Z)")
    if pattern.search(existing_body):
        return pattern.sub(new_block.rstrip(), existing_body, count=1).strip()
    if not existing_body.strip():
        return new_block.rstrip()
    return f"{existing_body.rstrip()}\n\n{new_block.rstrip()}".strip()


class NoteManager:
    """Creates and updates user-facing notes through explicit managed markers."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def bootstrap(self) -> None:
        self.config.output_root.mkdir(parents=True, exist_ok=True)
        self.config.summaries_dir.mkdir(parents=True, exist_ok=True)
        self.config.daily_notes_dir.mkdir(parents=True, exist_ok=True)
        self.config.weekly_notes_dir.mkdir(parents=True, exist_ok=True)

        self.config.daily_template.parent.mkdir(parents=True, exist_ok=True)
        self.config.weekly_template.parent.mkdir(parents=True, exist_ok=True)
        self.config.preferences_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.config.daily_template.exists():
            self.config.daily_template.write_text(DEFAULT_DAILY_TEMPLATE, encoding="utf-8")
        if not self.config.weekly_template.exists():
            self.config.weekly_template.write_text(DEFAULT_WEEKLY_TEMPLATE, encoding="utf-8")
        if not self.config.preferences_file.exists():
            self.config.preferences_file.write_text(DEFAULT_PREFERENCES_FILE, encoding="utf-8")

        self.ensure_weekly_note_exists()

    def ensure_weekly_note_exists(self) -> Path:
        weekly_path = self.weekly_note_path
        if not weekly_path.exists():
            shutil.copyfile(self.config.weekly_template, weekly_path)
        return weekly_path

    @property
    def weekly_note_path(self) -> Path:
        return self.config.weekly_notes_dir / self.config.weekly_note_file

    def rotate_weekly_note_if_needed(self, run_date: date) -> bool:
        self.ensure_weekly_note_exists()
        if run_date.weekday() != _ROTATION_DAYS[self.config.rotation_day]:
            return False

        archive_name = self.config.archive_name_pattern.format(date=run_date.isoformat())
        archive_path = self.config.weekly_notes_dir / archive_name
        if archive_path.exists():
            return False

        weekly_text = self.weekly_note_path.read_text(encoding="utf-8")
        template_text = self.config.weekly_template.read_text(encoding="utf-8")
        if weekly_text.strip() == template_text.strip():
            return False

        shutil.move(str(self.weekly_note_path), str(archive_path))
        shutil.copyfile(self.config.weekly_template, self.weekly_note_path)
        return True

    def read_weekly_synthesis(self) -> str:
        text = self.ensure_weekly_note_exists().read_text(encoding="utf-8")
        return _read_marker_block(text, "weekly-synthesis")

    def update_daily_note(self, run_date: date, top_paper: ProcessedPaper) -> Path:
        daily_path = self.config.daily_notes_dir / f"{run_date.isoformat()}.md"
        if daily_path.exists():
            text = daily_path.read_text(encoding="utf-8")
        else:
            template = self.config.daily_template.read_text(encoding="utf-8")
            text = template.replace("{{date}}", run_date.isoformat())

        link = render_link(
            top_paper.filename_stem,
            top_paper.paper.title,
            style=self.config.link_style,
            from_subdir="daily-notes",
        )
        block = f"## Today's Top Paper\n{link} - {top_paper.micro_summary}"
        updated = _replace_marker_block(text, "daily-top-paper", block)
        daily_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return daily_path

    def update_weekly_note(self, run_date: date, papers: list[ProcessedPaper], synthesis: str) -> Path:
        weekly_path = self.ensure_weekly_note_exists()
        text = weekly_path.read_text(encoding="utf-8")
        updated = _replace_marker_block(text, "weekly-synthesis", synthesis.strip())

        existing_additions = _read_marker_block(updated, "weekly-daily-additions")
        day_name = run_date.strftime("%A")
        entries = [
            f"- {render_link(paper.filename_stem, paper.paper.title, style=self.config.link_style, from_subdir='weekly-notes')} - {paper.micro_summary}"
            for paper in papers
        ]
        day_block = "\n".join([f"### {day_name}", *entries])
        additions = _upsert_day_block(existing_additions, day_name, day_block)
        updated = _replace_marker_block(updated, "weekly-daily-additions", additions)

        weekly_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return weekly_path
