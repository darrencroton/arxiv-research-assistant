"""Daily and weekly note management for re-ass."""

from __future__ import annotations

from datetime import date, timedelta
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

##  TODAY'S TOP PAPER
"""

DEFAULT_WEEKLY_TEMPLATE = """# This Week's ArXiv Overview

## Synthesis
*(A synthesis of this week's papers will be automatically generated here. Max 100 words.)*

---
## Daily Additions
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


def _find_heading_line(lines: list[str], heading: str) -> int | None:
    for index, line in enumerate(lines):
        if line.rstrip("\n") == heading:
            return index
    return None


def _is_top_level_heading(line: str) -> bool:
    return line.startswith("## ")


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    heading_index = _find_heading_line(lines, heading)
    if heading_index is None:
        return None

    next_heading_index: int | None = None
    for index in range(heading_index + 1, len(lines)):
        if _is_top_level_heading(lines[index]):
            next_heading_index = index
            break

    if next_heading_index is None:
        return heading_index, len(lines)

    end_index = next_heading_index
    probe = next_heading_index - 1
    while probe > heading_index and not lines[probe].strip():
        probe -= 1
    if probe > heading_index and lines[probe].strip() == "---":
        end_index = probe

    while end_index > heading_index + 1 and not lines[end_index - 1].strip():
        end_index -= 1

    return heading_index, end_index


def _replace_section(text: str, heading: str, body: str) -> str:
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        return _append_section(text, heading, body)

    heading_index, end_index = bounds
    prefix = "".join(lines[:heading_index])
    suffix = "".join(lines[end_index:])
    section = _render_section(heading, body, has_suffix=bool(suffix))
    return prefix + section + suffix


def _append_section(text: str, heading: str, body: str) -> str:
    prefix = text.rstrip()
    if prefix:
        prefix += "\n\n"
    return prefix + _render_section(heading, body, has_suffix=False)


def _read_section(text: str, heading: str) -> str:
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        return ""

    heading_index, end_index = bounds
    body = "".join(lines[heading_index + 1:end_index]).strip()
    return body


def _render_section(heading: str, body: str, *, has_suffix: bool) -> str:
    content = body.rstrip()
    if content:
        section = f"{heading}\n\n{content}"
    else:
        section = heading
    section += "\n\n" if has_suffix else "\n"
    return section


def _upsert_day_block(existing_body: str, day_heading: str, new_block: str) -> str:
    pattern = re.compile(rf"(?ms)^### {re.escape(day_heading)}\n.*?(?=^### |\Z)")
    if pattern.search(existing_body):
        return pattern.sub(new_block.rstrip(), existing_body, count=1).strip()
    if not existing_body.strip():
        return new_block.rstrip()
    return f"{existing_body.rstrip()}\n\n{new_block.rstrip()}".strip()


def _ordinal(day_number: int) -> str:
    if 10 <= day_number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_number % 10, "th")
    return f"{day_number}{suffix}"


def _format_day_heading(run_date: date) -> str:
    return f"{run_date.strftime('%A')} {_ordinal(run_date.day)}"


def _week_start(run_date: date, rotation_day: str) -> date:
    rotation_index = _ROTATION_DAYS[rotation_day]
    delta_days = (run_date.weekday() - rotation_index) % 7
    return run_date - timedelta(days=delta_days)


def _format_week_range(run_date: date, rotation_day: str) -> str:
    start = _week_start(run_date, rotation_day)
    end = start + timedelta(days=6)

    if start.year == end.year and start.month == end.month:
        return f"{start.day}-{end.day} {start.strftime('%B %Y')}"
    if start.year == end.year:
        return f"{start.day} {start.strftime('%B')} - {end.day} {end.strftime('%B %Y')}"
    return f"{start.day} {start.strftime('%B %Y')} - {end.day} {end.strftime('%B %Y')}"


def _weekly_title(run_date: date, rotation_day: str) -> str:
    return f"# This Week's ArXiv Overview {_format_week_range(run_date, rotation_day)}"


def _replace_weekly_title(text: str, title: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("# "):
            lines[index] = f"{title}\n"
            return "".join(lines)
    return f"{title}\n\n{text.lstrip()}"


class NoteManager:
    """Creates and updates user-facing notes through managed heading sections."""

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
        return _read_section(text, "## Synthesis")

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
        block = "\n".join(
            [
                f"**Title:** {link}",
                "",
                f"**Summary:** {top_paper.micro_summary}",
            ]
        )
        updated = _replace_section(text, "##  TODAY'S TOP PAPER", block)
        daily_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return daily_path

    def update_weekly_note(self, run_date: date, papers: list[ProcessedPaper], synthesis: str) -> Path:
        weekly_path = self.ensure_weekly_note_exists()
        text = weekly_path.read_text(encoding="utf-8")
        updated = _replace_weekly_title(text, _weekly_title(run_date, self.config.rotation_day))
        updated = _replace_section(updated, "## Synthesis", synthesis.strip())

        existing_additions = _read_section(updated, "## Daily Additions")
        day_heading = _format_day_heading(run_date)
        entries = [
            f"- {render_link(paper.filename_stem, paper.paper.title, style=self.config.link_style, from_subdir='weekly-notes')} - {paper.micro_summary}"
            for paper in papers
        ]
        day_block = "\n".join([f"### {day_heading}", *entries])
        additions = _upsert_day_block(existing_additions, day_heading, day_block)
        updated = _replace_section(updated, "## Daily Additions", additions)

        weekly_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return weekly_path
