from __future__ import annotations

from collections import OrderedDict
from datetime import date
import shutil
from pathlib import Path
import re

from re_ass.models import ProcessedPaper
from re_ass.settings import AppConfig, DEFAULT_PREFERENCES_FILE, DEFAULT_WEEKLY_TEMPLATE


WEEKLY_NOTE_PATTERN = re.compile(
    r"(?s)^# This Week's ArXiv Overview\n\n## Synthesis\n(?P<synthesis>.*?)\n\n---\n## Daily Additions\n(?P<daily>.*)\Z"
)


class VaultManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def bootstrap(self) -> None:
        self.config.vault_root.mkdir(parents=True, exist_ok=True)
        self.config.daily_dir.mkdir(parents=True, exist_ok=True)
        self.config.papers_dir.mkdir(parents=True, exist_ok=True)
        self.config.weekly_archive_dir.mkdir(parents=True, exist_ok=True)
        self.config.templates_dir.mkdir(parents=True, exist_ok=True)

        if not self.config.preferences_file.exists():
            self.config.preferences_file.write_text(DEFAULT_PREFERENCES_FILE, encoding="utf-8")

        if not self.config.weekly_template_file.exists():
            self.config.weekly_template_file.write_text(DEFAULT_WEEKLY_TEMPLATE, encoding="utf-8")

        self.ensure_weekly_note_exists()

    def ensure_weekly_note_exists(self) -> Path:
        if not self.config.weekly_note_file.exists():
            shutil.copyfile(self.config.weekly_template_file, self.config.weekly_note_file)
        return self.config.weekly_note_file

    def rotate_weekly_note_if_needed(self, run_date: date) -> bool:
        self.ensure_weekly_note_exists()
        if run_date.weekday() != 6:
            return False

        archive_path = self.config.weekly_archive_dir / f"{run_date.isoformat()}-arxiv.md"
        if archive_path.exists():
            return False

        weekly_note_text = self.config.weekly_note_file.read_text(encoding="utf-8")
        if self._is_pristine_weekly_note(weekly_note_text):
            return False

        if self.config.weekly_note_file.exists():
            shutil.move(str(self.config.weekly_note_file), str(archive_path))

        shutil.copyfile(self.config.weekly_template_file, self.config.weekly_note_file)
        return True

    def read_weekly_synthesis(self) -> str:
        text = self.ensure_weekly_note_exists().read_text(encoding="utf-8")
        match = WEEKLY_NOTE_PATTERN.match(text)
        if not match:
            raise ValueError("Weekly note does not match the expected template structure.")
        return match.group("synthesis").strip()

    def update_daily_note(self, run_date: date, top_paper: ProcessedPaper) -> Path:
        daily_note_path = self.config.daily_dir / f"{run_date.isoformat()}.md"
        section_body = f"{top_paper.wikilink} - {top_paper.micro_summary}"

        if daily_note_path.exists():
            content = daily_note_path.read_text(encoding="utf-8")
        else:
            content = f"# {run_date.isoformat()}\n"

        block = f"## Today's Top Paper\n{section_body}\n"
        pattern = re.compile(r"(?ms)^## Today's Top Paper\n.*?(?=^## |\Z)")

        if pattern.search(content):
            updated = pattern.sub(block, content, count=1).rstrip() + "\n"
        else:
            separator = "\n\n" if content.strip() else ""
            updated = content.rstrip() + f"{separator}{block}\n"

        daily_note_path.write_text(updated, encoding="utf-8")
        return daily_note_path

    def update_weekly_note(self, run_date: date, papers: list[ProcessedPaper], synthesis: str) -> Path:
        text = self.ensure_weekly_note_exists().read_text(encoding="utf-8")
        match = WEEKLY_NOTE_PATTERN.match(text)
        if not match:
            raise ValueError("Weekly note does not match the expected template structure.")

        daily_sections = self._parse_daily_sections(match.group("daily"))
        daily_sections[run_date.strftime("%A")] = [
            f"- {paper.wikilink} - {paper.micro_summary}"
            for paper in papers
        ]

        rendered = self._render_weekly_note(synthesis, daily_sections)
        self.config.weekly_note_file.write_text(rendered, encoding="utf-8")
        return self.config.weekly_note_file

    def _parse_daily_sections(self, body: str) -> OrderedDict[str, list[str]]:
        sections: OrderedDict[str, list[str]] = OrderedDict()
        current_heading: str | None = None
        current_lines: list[str] = []

        for raw_line in body.splitlines():
            line = raw_line.rstrip()
            if line.startswith("### "):
                if current_heading is not None:
                    sections[current_heading] = current_lines[:]
                current_heading = line[4:].strip()
                current_lines = []
                continue

            if current_heading is not None and line:
                current_lines.append(line)

        if current_heading is not None:
            sections[current_heading] = current_lines[:]

        return sections

    def _render_weekly_note(self, synthesis: str, daily_sections: OrderedDict[str, list[str]]) -> str:
        lines = [
            "# This Week's ArXiv Overview",
            "",
            "## Synthesis",
            synthesis.strip(),
            "",
            "---",
            "## Daily Additions",
        ]

        for heading, entries in daily_sections.items():
            lines.append("")
            lines.append(f"### {heading}")
            lines.extend(entries)

        return "\n".join(lines).rstrip() + "\n"

    def _is_pristine_weekly_note(self, text: str) -> bool:
        return text.strip() == self.config.weekly_template_file.read_text(encoding="utf-8").strip()
