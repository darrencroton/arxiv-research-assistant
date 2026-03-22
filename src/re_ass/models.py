"""Core dataclasses shared across re-ass application modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PreferenceConfig:
    """Parsed user preferences: ranked priorities and arXiv categories."""

    priorities: tuple[str, ...]
    categories: tuple[str, ...]
    raw_text: str
    top_papers: int = 3


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

    @property
    def searchable_text(self) -> str:
        parts = [
            self.title,
            self.summary,
            " ".join(self.authors),
            self.primary_category,
            " ".join(self.categories),
        ]
        return " ".join(part for part in parts if part).lower()


@dataclass(frozen=True, slots=True)
class ProcessedPaper:
    """A paper that has been successfully processed through the pipeline."""

    paper: ArxivPaper
    paper_key: str
    filename_stem: str
    note_path: Path
    pdf_path: Path | None
    micro_summary: str
