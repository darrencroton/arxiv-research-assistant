"""Preferences parsing for re-ass.

Reads user categories and ranked priorities from a Markdown preferences file.
"""

from __future__ import annotations

from pathlib import Path
import re

from re_ass.models import PreferenceConfig


_NUMBERED_ITEM_PATTERN = re.compile(r"^\s*\d+\.\s+(?P<value>.+?)\s*$")
_BULLET_ITEM_PATTERN = re.compile(r"^\s*[-*]\s+(?P<value>.+?)\s*$")
_HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+(?P<value>.+?)\s*$")
_TOP_PAPERS_PATTERN = re.compile(r"^top\s+papers?\s*:\s*(?P<value>\d+)\s*$", re.IGNORECASE)
_DEFAULT_TOP_PAPERS = 3
_MAX_TOP_PAPERS = 10


def _parse_top_papers(value: str, *, source: Path) -> int:
    match = _TOP_PAPERS_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"Invalid top papers preference in {source}: '{value}'.")
    top_papers = int(match.group("value"))
    if top_papers < 1 or top_papers > _MAX_TOP_PAPERS:
        raise ValueError(f"Top papers preference in {source} must be between 1 and {_MAX_TOP_PAPERS}.")
    return top_papers


def load_preferences(preferences_path: Path, default_categories: tuple[str, ...]) -> PreferenceConfig:
    """Parse a Markdown preferences file into a PreferenceConfig."""
    raw_text = preferences_path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()

    categories: list[str] = []
    priorities: list[str] = []
    top_papers = _DEFAULT_TOP_PAPERS
    current_section: str | None = None

    for line in lines:
        heading_match = _HEADING_PATTERN.match(line)
        if heading_match:
            heading = heading_match.group("value").strip().lower()
            if "categor" in heading:
                current_section = "categories"
            elif "priorit" in heading or "interest" in heading:
                current_section = "priorities"
            elif "setting" in heading or "output" in heading:
                current_section = "settings"
            else:
                current_section = None
            continue

        numbered_match = _NUMBERED_ITEM_PATTERN.match(line)
        if numbered_match:
            priorities.append(numbered_match.group("value").strip())
            continue

        bullet_match = _BULLET_ITEM_PATTERN.match(line)
        if bullet_match and current_section == "categories":
            categories.append(bullet_match.group("value").strip())
            continue
        if bullet_match and current_section == "settings":
            top_papers = _parse_top_papers(bullet_match.group("value"), source=preferences_path)

    if not priorities:
        raise ValueError(f"No priorities found in {preferences_path}.")

    final_categories = tuple(categories or default_categories)
    if not final_categories:
        raise ValueError("No arXiv categories configured.")

    return PreferenceConfig(
        priorities=tuple(priorities),
        categories=final_categories,
        raw_text=raw_text,
        top_papers=top_papers,
    )
