"""Core dataclasses shared across re-ass application modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PreferenceConfig:
    """Parsed user preferences: categories plus flat or grouped priorities."""

    priorities: tuple[str, ...]
    categories: tuple[str, ...]
    science_priorities: tuple[str, ...] = ()
    method_priorities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArxivPaper:
    """A paper fetched from arXiv."""

    title: str
    summary: str
    arxiv_url: str
    entry_id: str
    authors: tuple[str, ...]
    primary_category: str
    categories: tuple[str, ...]
    published: datetime
    updated: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProcessedPaper:
    """A paper that has been successfully processed through the pipeline."""

    paper: ArxivPaper
    paper_key: str
    filename_stem: str
    note_path: Path
    pdf_path: Path | None
    micro_summary: str
