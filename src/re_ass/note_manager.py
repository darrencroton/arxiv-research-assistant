"""Daily and weekly note management for re-ass."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import re
import shutil
from urllib.parse import quote

import pendulum

from re_ass.models import ArxivPaper, ProcessedPaper
from re_ass.paper_identity import extract_source_id, render_link
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
_WEEKLY_TITLE_PREFIX = "# ARXIV PAPERS FOR THE WEEK"

_DAILY_TEMPLATE_DATE_PATTERN = re.compile(r"\{\{\s*date(?:\s*:\s*([^}]+?))?\s*\}\}")
_WEEK_START_MARKER_RE = re.compile(r"(?m)^<!--\s*re-ass-week-start:\s*(?P<date>\d{4}-\d{2}-\d{2})\s*-->\s*$")
_WEEK_TITLE_RE = re.compile(
    rf"^{re.escape(_WEEKLY_TITLE_PREFIX)} "
    r"(?P<start_day>\d{1,2})(?:st|nd|rd|th)"
    r"(?: (?P<start_month>[A-Za-z]+)(?: (?P<start_year>\d{4}))?)?"
    r" - "
    r"(?P<end_day>\d{1,2})(?:st|nd|rd|th) "
    r"(?P<end_month>[A-Za-z]+) (?P<end_year>\d{4})$"
)


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
    else:
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


def _escape_emphasis(text: str) -> str:
    return text.replace("*", r"\*")


def _author_initials(given_names: list[str]) -> str:
    initials = []
    for name in given_names:
        clean = name.strip(".")
        if not clean:
            continue
        initials.append(f"{clean[0]}.")
    return " ".join(initials)


def _short_author_name(author: str) -> str:
    parts = [part for part in author.split() if part]
    if len(parts) <= 1:
        return author.strip() or "Unknown"
    surname = parts[-1]
    initials = _author_initials(parts[:-1])
    if not initials:
        return surname
    return f"{surname} {initials}"


def _short_author_list(authors: tuple[str, ...]) -> str:
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        return _short_author_name(authors[0])
    if len(authors) == 2:
        return " & ".join(_short_author_name(author) for author in authors)
    return f"{_short_author_name(authors[0])} et al."


def _interest_entry(paper: ArxivPaper) -> str:
    source_id = extract_source_id(paper.entry_id or paper.arxiv_url)
    title = _escape_emphasis(paper.title)
    authors = _short_author_list(paper.authors)
    return f"- *{title}*, {authors}, [arXiv:{source_id}]({paper.arxiv_url})"


def _build_weekly_additions(
    existing_additions: str,
    run_date: date,
    papers: list[ProcessedPaper],
    *,
    interest_papers: list[ArxivPaper] | None = None,
    link_style: str,
) -> str:
    day_heading = _format_day_heading(run_date)
    entries = [
        "\n".join(
            [
                f"**Title:** {render_link(paper.filename_stem, paper.paper.title, style=link_style, from_subdir='weekly-notes')}",
                "",
                f"**Summary:** {paper.micro_summary}",
            ]
        )
        for paper in papers
    ]
    block_parts = [f"### {day_heading}"]
    content_parts: list[str] = []
    if entries:
        content_parts.append("\n\n".join(entries))
    if interest_papers:
        interest_entries = "\n".join(_interest_entry(paper) for paper in interest_papers)
        content_parts.append("\n".join(["**Other papers of interest:**", "", interest_entries]))
    if content_parts:
        block_parts.extend(["", "\n\n".join(content_parts)])
    day_block = "\n".join(block_parts)
    return _upsert_day_block(existing_additions, day_heading, day_block)


def _render_section(heading: str, body: str, *, has_suffix: bool) -> str:
    content = body.rstrip()
    if content:
        section = f"{heading}\n\n{content}"
    else:
        section = heading
    section += "\n\n" if has_suffix else "\n"
    return section


def _parse_day_blocks(existing_body: str) -> list[tuple[str, str]]:
    body = existing_body.strip()
    if not body:
        return []

    matches = list(re.finditer(r"(?m)^### .+$", body))
    if not matches:
        return []

    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        block = body[start:end].strip()
        if block.endswith("---"):
            block = block[:-3].rstrip()
        heading = match.group(0).removeprefix("### ").strip()
        blocks.append((heading, block))
    return blocks


def _upsert_day_block(existing_body: str, day_heading: str, new_block: str) -> str:
    blocks = _parse_day_blocks(existing_body)
    if not blocks:
        return new_block.rstrip()

    updated_blocks: list[str] = []
    replaced = False
    for heading, block in blocks:
        if heading == day_heading:
            updated_blocks.append(new_block.rstrip())
            replaced = True
        else:
            updated_blocks.append(block.rstrip())

    if not replaced:
        updated_blocks.append(new_block.rstrip())

    return "\n\n---\n\n".join(updated_blocks).strip()


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
    end = start + timedelta(days=4)

    if start.year == end.year and start.month == end.month:
        return f"{_ordinal(start.day)} - {_ordinal(end.day)} {start.strftime('%B %Y')}"
    if start.year == end.year:
        return f"{_ordinal(start.day)} {start.strftime('%B')} - {_ordinal(end.day)} {end.strftime('%B %Y')}"
    return f"{_ordinal(start.day)} {start.strftime('%B %Y')} - {_ordinal(end.day)} {end.strftime('%B %Y')}"


def _weekly_title(run_date: date, rotation_day: str) -> str:
    return f"{_WEEKLY_TITLE_PREFIX} {_format_week_range(run_date, rotation_day)}"


def _replace_weekly_title(text: str, title: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("# "):
            lines[index] = f"{title}\n"
            return "".join(lines)
    return f"{title}\n\n{text.lstrip()}"


def _remove_legacy_week_marker(text: str) -> str:
    return _WEEK_START_MARKER_RE.sub("", text, count=1)


def _ensure_blank_line_after_first_heading(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("# "):
            prefix = "".join(lines[: index + 1])
            suffix_lines = lines[index + 1 :]
            while suffix_lines and not suffix_lines[0].strip():
                suffix_lines.pop(0)
            suffix = "".join(suffix_lines)
            if not suffix:
                return prefix
            return f"{prefix}\n{suffix}"
    return text


def _replace_weekly_header(text: str, note_date: date, rotation_day: str) -> str:
    updated = _replace_weekly_title(text, _weekly_title(note_date, rotation_day))
    updated = _remove_legacy_week_marker(updated)
    return _ensure_blank_line_after_first_heading(updated)


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line.strip()
    return None


def _week_start_from_title(text: str) -> date | None:
    heading = _first_heading(text)
    if heading is None:
        return None

    match = _WEEK_TITLE_RE.match(heading)
    if match is None:
        return None

    end_year = int(match.group("end_year"))
    start_month = match.group("start_month") or match.group("end_month")
    start_year = int(match.group("start_year") or end_year)
    start_label = f"{match.group('start_day')} {start_month} {start_year}"
    try:
        return pendulum.from_format(start_label, "D MMMM YYYY", tz="UTC").date()
    except ValueError:
        return None


def _stored_week_start(text: str) -> date | None:
    marker_match = _WEEK_START_MARKER_RE.search(text)
    if marker_match is not None:
        try:
            return date.fromisoformat(marker_match.group("date"))
        except ValueError:
            return None
    return _week_start_from_title(text)


def _render_daily_template(template: str, run_date: date) -> str:
    render_date = pendulum.datetime(run_date.year, run_date.month, run_date.day, tz="UTC")

    def replace_date(match: re.Match[str]) -> str:
        format_string = match.group(1)
        if format_string is None:
            return run_date.isoformat()
        return render_date.format(format_string.strip(), locale="en")

    return _DAILY_TEMPLATE_DATE_PATTERN.sub(replace_date, template)


def _require_file(path: Path, description: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{description} not found: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"{description} is not a file: {path}")


class NoteManager:
    """Creates and updates user-facing notes through managed heading sections."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def bootstrap(self, reference_date: date | None = None) -> None:
        self.config.output_root.mkdir(parents=True, exist_ok=True)
        self.config.summaries_dir.mkdir(parents=True, exist_ok=True)
        self.config.daily_notes_dir.mkdir(parents=True, exist_ok=True)
        self.config.weekly_notes_dir.mkdir(parents=True, exist_ok=True)

        _require_file(self.config.daily_template, "Daily template")
        _require_file(self.config.weekly_template, "Weekly template")

        self.ensure_weekly_note_exists(reference_date)

    def ensure_weekly_note_exists(self, reference_date: date | None = None) -> Path:
        reference_date = reference_date or date.today()
        weekly_path = self.weekly_note_path
        if not weekly_path.exists():
            weekly_path.write_text(self._render_weekly_template(reference_date), encoding="utf-8")
        return weekly_path

    @property
    def weekly_note_path(self) -> Path:
        return self.config.weekly_notes_dir / self.config.weekly_note_file

    def _archive_week_start(self, note_date: date) -> date:
        return _week_start(note_date, self.config.rotation_day)

    def archived_weekly_note_path(self, note_date: date) -> Path:
        archive_name = self.config.archive_name_pattern.format(date=self._archive_week_start(note_date).isoformat())
        return self.config.weekly_notes_dir / archive_name

    def _archived_weekly_note_path_for_week_start(self, week_start: date) -> Path:
        archive_name = self.config.archive_name_pattern.format(date=week_start.isoformat())
        return self.config.weekly_notes_dir / archive_name

    def weekly_note_path_for(self, note_date: date, reference_date: date) -> Path:
        if _week_start(note_date, self.config.rotation_day) == _week_start(reference_date, self.config.rotation_day):
            return self.weekly_note_path
        return self.archived_weekly_note_path(note_date)

    def _weekly_note_link(self, note_date: date, reference_date: date) -> str:
        file_name = self.weekly_note_path_for(note_date, reference_date).name
        file_stem = Path(file_name).stem
        if self.config.link_style == "wikilink":
            return f"[[{file_stem}|See all of this week's arXiv papers]]"
        encoded_name = quote(file_name)
        return f"[See all of this week's arXiv papers](../weekly-notes/{encoded_name})"

    def _render_weekly_template(self, note_date: date) -> str:
        template_text = self.config.weekly_template.read_text(encoding="utf-8")
        return _replace_weekly_header(template_text, note_date, self.config.rotation_day)

    def _load_weekly_note_text(self, note_date: date, reference_date: date) -> str:
        weekly_path = self.weekly_note_path_for(note_date, reference_date)
        target_week_start = _week_start(note_date, self.config.rotation_day)
        if not weekly_path.exists():
            return self._render_weekly_template(note_date)

        text = weekly_path.read_text(encoding="utf-8")
        stored_week_start = _stored_week_start(text)
        if stored_week_start is None and weekly_path == self.weekly_note_path and _first_heading(text) == _WEEKLY_TITLE_PREFIX:
            return _replace_weekly_header(text, note_date, self.config.rotation_day)
        if stored_week_start is None:
            raise ValueError(f"Weekly note is missing a recognizable week heading: {weekly_path}")
        if stored_week_start != target_week_start:
            raise ValueError(
                f"Weekly note {weekly_path} belongs to {stored_week_start.isoformat()}, "
                f"not {target_week_start.isoformat()}."
            )
        return text

    def rotate_weekly_note_if_needed(self, run_date: date) -> bool:
        current_week_start = _week_start(run_date, self.config.rotation_day)
        self.ensure_weekly_note_exists(run_date)
        weekly_text = self.weekly_note_path.read_text(encoding="utf-8")
        live_week_start = _stored_week_start(weekly_text)

        if live_week_start is None and _first_heading(weekly_text) == _WEEKLY_TITLE_PREFIX:
            self.weekly_note_path.write_text(
                _replace_weekly_header(weekly_text, run_date, self.config.rotation_day),
                encoding="utf-8",
            )
            return False
        if live_week_start is None:
            raise ValueError(f"Weekly note is missing a recognizable week heading: {self.weekly_note_path}")
        if live_week_start == current_week_start:
            return False

        if weekly_text.strip() == self._render_weekly_template(live_week_start).strip():
            self.weekly_note_path.write_text(self._render_weekly_template(run_date), encoding="utf-8")
            return False

        archive_path = self._archived_weekly_note_path_for_week_start(live_week_start)
        if archive_path.exists():
            raise FileExistsError(f"Archived weekly note already exists and will not be overwritten: {archive_path}")

        shutil.move(str(self.weekly_note_path), str(archive_path))
        self.weekly_note_path.write_text(self._render_weekly_template(run_date), encoding="utf-8")
        return True

    def read_weekly_synthesis(self, note_date: date, *, reference_date: date | None = None) -> str:
        reference_date = reference_date or note_date
        text = self._load_weekly_note_text(note_date, reference_date)
        return _read_section(text, self.config.weekly_synthesis_heading)

    def read_weekly_additions(self, note_date: date, *, reference_date: date | None = None) -> str:
        reference_date = reference_date or note_date
        text = self._load_weekly_note_text(note_date, reference_date)
        return _read_section(text, self.config.weekly_additions_heading)

    def preview_weekly_additions(
        self,
        note_date: date,
        papers: list[ProcessedPaper],
        *,
        reference_date: date | None = None,
    ) -> str:
        reference_date = reference_date or note_date
        existing_additions = self.read_weekly_additions(note_date, reference_date=reference_date)
        return _build_weekly_additions(
            existing_additions,
            note_date,
            papers,
            link_style=self.config.link_style,
        )

    def update_daily_note(
        self,
        note_date: date,
        top_paper: ProcessedPaper,
        *,
        reference_date: date | None = None,
    ) -> Path:
        reference_date = reference_date or note_date
        daily_path = self.config.daily_notes_dir / f"{note_date.isoformat()}.md"
        if daily_path.exists():
            text = daily_path.read_text(encoding="utf-8")
        else:
            template = self.config.daily_template.read_text(encoding="utf-8")
            text = _render_daily_template(template, note_date)

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
                "",
                self._weekly_note_link(note_date, reference_date),
            ]
        )
        updated = _replace_section(text, self.config.daily_top_paper_heading, block)
        daily_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return daily_path

    def update_weekly_note(
        self,
        note_date: date,
        papers: list[ProcessedPaper],
        synthesis: str,
        *,
        interest_papers: list[ArxivPaper] | None = None,
        reference_date: date | None = None,
    ) -> Path:
        reference_date = reference_date or note_date
        weekly_path = self.weekly_note_path_for(note_date, reference_date)
        text = self._load_weekly_note_text(note_date, reference_date)
        updated = _replace_weekly_header(text, note_date, self.config.rotation_day)
        updated = _replace_section(updated, self.config.weekly_synthesis_heading, synthesis.strip())
        existing_additions = _read_section(updated, self.config.weekly_additions_heading)
        additions = _build_weekly_additions(
            existing_additions,
            note_date,
            papers,
            interest_papers=interest_papers,
            link_style=self.config.link_style,
        )
        updated = _replace_section(updated, self.config.weekly_additions_heading, additions)

        weekly_path.parent.mkdir(parents=True, exist_ok=True)
        weekly_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return weekly_path
