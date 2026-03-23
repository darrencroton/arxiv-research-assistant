"""Stable paper identity, canonical filenames, and link rendering for re-ass."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse
import re

from re_ass.models import ArxivPaper


_ARXIV_ID_RE = re.compile(
    r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z.-]+/\d{7}(?:v\d+)?)"
)
_INVALID_FILENAME_CHARS = '/\\:*?"<>|#[]'
_FILENAME_SANITIZE_TABLE = str.maketrans({character: " " for character in _INVALID_FILENAME_CHARS})


@dataclass(frozen=True, slots=True)
class PaperIdentity:
    paper_key: str
    source_id: str
    filename_stem: str
    note_filename: str
    pdf_filename: str
    authors_short: str
    authors_full: tuple[str, ...]
    year: int


def _sanitize_filename_component(value: str) -> str:
    cleaned = value.translate(_FILENAME_SANITIZE_TABLE)
    cleaned = cleaned.replace("]]", " ").replace("[[", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Untitled"


def extract_source_id(value: str) -> str:
    parsed = urlparse(value)
    candidates = [
        parsed.path.rstrip("/"),
        value.strip(),
    ]
    for candidate in candidates:
        if candidate.startswith("/abs/"):
            candidate = candidate.removeprefix("/abs/")
        elif candidate.startswith("/pdf/"):
            candidate = candidate.removeprefix("/pdf/").removesuffix(".pdf")
        elif candidate.endswith(".pdf"):
            candidate = candidate.removesuffix(".pdf")

        if match := _ARXIV_ID_RE.search(candidate):
            return re.sub(r"v\d+$", "", match.group("id"), flags=re.IGNORECASE)
    raise ValueError(f"Could not determine an arXiv identifier from '{value}'.")


def _authors_short(authors: tuple[str, ...]) -> str:
    if not authors:
        return "Unknown"
    first_author = authors[0].strip() or "Unknown"
    surname = first_author.split()[-1]
    if len(authors) == 1:
        return surname
    return f"{surname} et al"


def derive_identity(paper: ArxivPaper) -> PaperIdentity:
    source_id = extract_source_id(paper.entry_id or paper.arxiv_url)
    authors_short = _authors_short(paper.authors)
    year = paper.published.year
    title = _sanitize_filename_component(paper.title)
    filename_stem = f"{authors_short} - {year} - {title} [arXiv {source_id}]"
    return PaperIdentity(
        paper_key=f"arxiv:{source_id}",
        source_id=source_id,
        filename_stem=filename_stem,
        note_filename=f"{filename_stem}.md",
        pdf_filename=f"{filename_stem}.pdf",
        authors_short=authors_short,
        authors_full=paper.authors,
        year=year,
    )


def render_link(filename_stem: str, display_title: str, *, style: str, from_subdir: str | None = None) -> str:
    if style == "wikilink":
        if filename_stem == display_title:
            return f"[[{filename_stem}]]"
        return f"[[{filename_stem}|{display_title}]]"
    if style != "markdown":
        raise ValueError(f"Unsupported link style '{style}'.")

    encoded_filename = quote(f"{filename_stem}.md")
    relative_path = f"../summaries/{encoded_filename}" if from_subdir else f"summaries/{encoded_filename}"
    return f"[{display_title}]({relative_path})"
