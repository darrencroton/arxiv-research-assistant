"""Upstream-derived paper summariser core adapted for re-ass."""

from __future__ import annotations

from collections.abc import Callable
import concurrent.futures
from dataclasses import dataclass
from datetime import timezone
import logging
import os
from pathlib import Path
import re
from string import Template
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from re_ass.llm_retry import is_retryable_llm_error
from re_ass.models import ArxivPaper
from re_ass.settings import LlmConfig

from .providers.base import Provider


# Required for marker-pdf on Apple Silicon — must be set before PyTorch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
load_dotenv()

LOGGER = logging.getLogger(__name__)
_KNOWLEDGE_DIR = Path(__file__).resolve().parent / "project_knowledge"
_SOURCE_SCAN_CHAR_LIMIT = 4000
_EXTRACTION_NOISE_LINE_CHAR_LIMIT = 1000
_DEFAULT_PROMPT_CHAR_BUDGET = 300_000
_REFERENCE_SECTION_TITLES = {
    "BIBLIOGRAPHY",
    "LITERATURE CITED",
    "NOTES AND REFERENCES",
    "REFERENCE",
    "REFERENCES",
    "REFERENCES AND NOTES",
    "REFERENCES CITED",
    "WORKS CITED",
}
_APPENDIX_SECTION_TITLES = {
    "APPENDICES",
    "APPENDIX",
    "SUPPLEMENTARY MATERIAL",
    "SUPPLEMENTARY MATERIALS",
}
_INLINE_FOOTNOTE_RE = re.compile(r"\[\^\d+\]")
_REFERENCES_HEADING_RE = re.compile(r"^## References\s*$", re.MULTILINE)
_marker_models = None
GLOSSARY_MAX_TERMS = 12

ARXIV_CATEGORY_KEYWORD_HEADINGS = {
    "astro-ph.CO": ("COSMOLOGY", "GALAXIES"),
    "astro-ph.EP": ("PLANETARY SYSTEMS", "STARS"),
    "astro-ph.GA": ("THE GALAXY", "GALAXIES", "INTERSTELLAR MEDIUM (ISM)", "STARS"),
    "astro-ph.HE": (
        "PHYSICAL DATA AND PROCESSES",
        "STARS",
        "TRANSIENTS",
        "RESOLVED AND UNRESOLVED SOURCES AS A FUNCTION OF WAVELENGTH",
    ),
    "astro-ph.IM": (
        "ASTRONOMICAL INSTRUMENTATION, METHODS AND TECHNIQUES",
        "ASTRONOMICAL DATABASES",
        "RESOLVED AND UNRESOLVED SOURCES AS A FUNCTION OF WAVELENGTH",
    ),
    "astro-ph.SR": ("THE SUN", "STARS", "PHYSICAL DATA AND PROCESSES"),
}
DEFAULT_KEYWORD_HEADINGS = (
    "GENERAL",
    "PHYSICAL DATA AND PROCESSES",
    "ASTRONOMICAL INSTRUMENTATION, METHODS AND TECHNIQUES",
    "ASTRONOMICAL DATABASES",
)

ARXIV_FILENAME_RE = re.compile(
    r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z.-]+/\d{7}(?:v\d+)?)"
)
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
ARXIV_TEXT_RE = re.compile(
    r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)"
    r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z.-]+/\d{7}(?:v\d+)?)",
    re.IGNORECASE,
)
ARXIV_NEW_STYLE_RE = re.compile(
    r"^(?P<yy>\d{2})(?P<mm>0[1-9]|1[0-2])\.\d{4,5}(?:v\d+)?$"
)
DOI_URL_RE = re.compile(
    r"https?://(?:dx\.)?doi\.org/(?P<doi>10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)

MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


class PaperSummariserError(RuntimeError):
    """Raised when the adapted paper summariser cannot produce a note."""


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Structured source metadata that can guide or repair summary top matter."""

    source_type: str | None = None
    identifier: str | None = None
    canonical_url: str | None = None
    published_label: str | None = None
    detection_method: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedPaperSummary:
    raw_summary: str
    source_metadata: SourceMetadata
    pdf_url: str


@dataclass(frozen=True, slots=True)
class ProjectKnowledge:
    keywords: str
    paper_summary_template: str
    system_prompt_template: str
    user_prompt_template: str
    summary_worked_example: str


def build_pdf_url(paper_url: str) -> str:
    parsed = urlparse(paper_url)
    if not parsed.netloc.casefold().endswith("arxiv.org"):
        raise PaperSummariserError(f"Unsupported paper URL for PDF fetch: {paper_url}")

    path = parsed.path.rstrip("/")
    if path.startswith("/abs/"):
        identifier = path.removeprefix("/abs/")
    elif path.startswith("/pdf/"):
        identifier = path.removeprefix("/pdf/")
    else:
        raise PaperSummariserError(f"Unsupported arXiv URL format: {paper_url}")

    identifier = identifier.removesuffix(".pdf")
    if not identifier:
        raise PaperSummariserError(f"Could not determine an arXiv identifier from {paper_url}")
    return f"https://arxiv.org/pdf/{identifier}.pdf"


def extract_arxiv_identifier(paper_url: str) -> str | None:
    parsed = urlparse(paper_url)
    path = parsed.path.rstrip("/")
    if path.startswith("/abs/"):
        return path.removeprefix("/abs/")
    if path.startswith("/pdf/"):
        return path.removeprefix("/pdf/").removesuffix(".pdf")
    return None


def download_arxiv_pdf(paper: ArxivPaper, destination_dir: Path, config: LlmConfig) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    pdf_url = build_pdf_url(paper.arxiv_url)
    identifier = extract_arxiv_identifier(paper.arxiv_url) or "paper"
    destination = destination_dir / f"{identifier}.pdf"
    request = Request(pdf_url, headers={"User-Agent": "re-ass/0.1"})

    try:
        with urlopen(request, timeout=config.download_timeout_seconds) as response:
            payload = response.read(config.max_pdf_size_mb * 1024 * 1024 + 1)
    except HTTPError as error:
        raise PaperSummariserError(f"Downloading {pdf_url} returned HTTP {error.code}.") from error
    except URLError as error:
        raise PaperSummariserError(f"Downloading {pdf_url} failed: {error.reason}.") from error
    except OSError as error:
        raise PaperSummariserError(f"Downloading {pdf_url} failed: {error}.") from error

    if not payload:
        raise PaperSummariserError(f"Downloading {pdf_url} returned no content.")
    if len(payload) > config.max_pdf_size_mb * 1024 * 1024:
        raise PaperSummariserError(
            f"Downloaded PDF exceeds {config.max_pdf_size_mb}MB limit."
        )

    destination.write_bytes(payload)
    LOGGER.info("Downloaded %s to %s.", pdf_url, destination)
    return destination


class PaperSummariser:
    def __init__(
        self,
        *,
        provider: Provider,
        config: LlmConfig,
        downloader: callable | None = None,
        input_reader: callable | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.downloader = downloader or (lambda paper, destination_dir: download_arxiv_pdf(paper, destination_dir, config))
        self.input_reader = input_reader or read_input_file

    def summarise_paper(self, paper: ArxivPaper) -> GeneratedPaperSummary:
        with tempfile.TemporaryDirectory(prefix="re-ass-paper-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_path = self.downloader(paper, temp_dir)
            return self.summarise_source(paper, source_path)

    def summarise_source(self, paper: ArxivPaper, source_path: Path) -> GeneratedPaperSummary:
        project_knowledge = read_project_knowledge()
        pdf_url = build_pdf_url(paper.arxiv_url)

        content, error = self.input_reader(source_path, self.provider, self.config)
        if error:
            raise PaperSummariserError(error)
        if content is None:
            raise PaperSummariserError(f"No content returned for {paper.title}.")

        is_pdf = source_path.suffix.lower() == ".pdf"
        if is_pdf and self.provider.supports_direct_pdf():
            paper_text = ""
        elif isinstance(content, bytes):
            paper_text = content.decode("utf-8", errors="ignore")
        else:
            paper_text = content

        source_metadata = extract_source_metadata(source_path, paper_text)
        categories = (paper.primary_category, *paper.categories)
        filtered_keywords = filter_keywords_for_categories(
            project_knowledge.keywords,
            categories,
        )
        LOGGER.info(
            "Tag keyword list: %s -> %s chars for categories: %s.",
            len(project_knowledge.keywords),
            len(filtered_keywords),
            ", ".join(category for category in categories if category) or "none",
        )

        system_prompt = create_system_prompt(project_knowledge.system_prompt_template)
        paper_text, user_prompt = fit_prompt_to_provider_budget(
            self.provider,
            system_prompt,
            paper_text,
            project_knowledge.paper_summary_template,
            project_knowledge.user_prompt_template,
            source_metadata=source_metadata,
            worked_example=project_knowledge.summary_worked_example,
        )
        write_debug_prompt(self.config.prompt_debug_file, system_prompt, user_prompt)

        summary = call_llm_with_retry(
            self.provider,
            content,
            is_pdf,
            system_prompt,
            user_prompt,
            max_tokens=self.config.max_output_tokens,
            max_retries=self.config.retry_attempts,
        )
        summary = strip_preamble(summary)
        validate_summary(summary)

        tags_section = generate_tags(
            summary,
            filtered_keywords,
            self.provider,
            config=self.config,
        )
        glossary_section = generate_glossary(
            summary,
            self.provider,
            config=self.config,
        )
        if glossary_section:
            summary = insert_section(summary, glossary_section)
        summary = insert_section(summary, tags_section)
        validate_summary(summary)

        return GeneratedPaperSummary(
            raw_summary=summary,
            source_metadata=source_metadata,
            pdf_url=pdf_url,
        )


def read_project_knowledge() -> ProjectKnowledge:
    """Read project knowledge and prompt templates needed for summarisation."""
    keywords_path = _KNOWLEDGE_DIR / "keywords.txt"
    if not keywords_path.exists():
        keywords_path = _KNOWLEDGE_DIR / "astronomy-keywords.txt"
    summary_template_path = _KNOWLEDGE_DIR / "paper-summary-template.md"
    system_prompt_path = _KNOWLEDGE_DIR / "system-prompt.md"
    user_prompt_path = _KNOWLEDGE_DIR / "user-prompt.md"
    worked_example_path = _KNOWLEDGE_DIR / "summary-worked-example.md"
    return ProjectKnowledge(
        keywords=keywords_path.read_text(encoding="utf-8"),
        paper_summary_template=summary_template_path.read_text(encoding="utf-8"),
        system_prompt_template=system_prompt_path.read_text(encoding="utf-8"),
        user_prompt_template=user_prompt_path.read_text(encoding="utf-8"),
        summary_worked_example=(
            worked_example_path.read_text(encoding="utf-8")
            if worked_example_path.exists()
            else ""
        ),
    )


def parse_keyword_sections(keywords: str) -> dict[str, list[str]]:
    """Parse a grouped keyword file into heading-to-tags sections."""
    sections: dict[str, list[str]] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for raw_line in keywords.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if not line.startswith("#"):
            if current_heading is not None:
                sections[current_heading] = current_lines
            current_heading = line.upper()
            current_lines = []
            continue

        if current_heading is None:
            current_heading = "GENERAL"
        current_lines.append(line)

    if current_heading is not None:
        sections[current_heading] = current_lines

    return sections


def _render_keyword_sections(sections: dict[str, list[str]], headings: tuple[str, ...] | list[str]) -> str:
    """Render selected keyword sections in source-file format."""
    rendered_sections = []
    for heading in headings:
        lines = sections.get(heading)
        if not lines:
            continue
        rendered_sections.append("\n".join([heading, *lines]))
    return "\n\n".join(rendered_sections)


def iter_keyword_tags(keywords: str) -> list[str]:
    """Return allowed hashtag tokens from a grouped keyword list in source order."""
    tags = []
    for match in re.finditer(r"#[A-Za-z0-9][A-Za-z0-9]*", keywords or ""):
        tag = match.group(0)
        if tag not in tags:
            tags.append(tag)
    return tags


def _normalise_search_text(value: str) -> str:
    """Normalise text for lightweight tag fallback matching."""
    return re.sub(r"[^A-Za-z0-9]+", "", value or "").lower()


def _split_tag_words(value: str) -> list[str]:
    """Split a CamelCase/acronym tag into searchable words."""
    return [
        part.lower()
        for part in re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+",
            value or "",
        )
        if len(part) > 1
    ]


def filter_keywords_for_categories(keywords: str, categories: tuple[str, ...]) -> str:
    """Return keywords relevant to arXiv categories, falling back when unknown."""
    category_list = tuple(category for category in categories if category)
    if not category_list:
        return keywords

    selected_headings: list[str] = []
    seen_headings: set[str] = set()
    for category in category_list:
        for heading in ARXIV_CATEGORY_KEYWORD_HEADINGS.get(category, ()):
            if heading not in seen_headings:
                seen_headings.add(heading)
                selected_headings.append(heading)

    if not selected_headings:
        return keywords

    for heading in DEFAULT_KEYWORD_HEADINGS:
        if heading not in seen_headings:
            selected_headings.append(heading)
            seen_headings.add(heading)

    filtered = _render_keyword_sections(parse_keyword_sections(keywords), selected_headings)
    return filtered or keywords


def _is_extraction_noise_line(line: str) -> bool:
    """Return True for long marker-pdf conversion lines with little prose value."""
    if len(line) <= _EXTRACTION_NOISE_LINE_CHAR_LIMIT:
        return False

    stripped = line.strip()
    if not stripped:
        return True

    alpha_chars = sum(char.isalpha() for char in stripped)
    alpha_ratio = alpha_chars / len(stripped)
    separator_chars = sum(char in "|-_=+·. " for char in stripped)
    separator_ratio = separator_chars / len(stripped)
    html_breaks = stripped.count("<br>")

    return (
        stripped.count("|") >= 2
        or html_breaks >= 20
        or separator_ratio > 0.85
        or alpha_ratio < 0.08
    )


def normalise_extracted_text(text: str, *, source_name: str = "document") -> str:
    """Remove extraction artefacts that can overwhelm LLM context windows."""
    if not text:
        return text

    kept_lines = []
    removed_lines = 0
    removed_chars = 0

    for line in text.splitlines():
        if _is_extraction_noise_line(line):
            removed_lines += 1
            removed_chars += len(line) + 1
            continue
        kept_lines.append(line)

    if removed_lines:
        LOGGER.info(
            "Removed %s noisy extraction lines from %s (~%s chars).",
            removed_lines,
            source_name,
            removed_chars,
        )
        return "\n".join(kept_lines)

    return text


def _get_prompt_char_budget(provider: Provider) -> int:
    configured_budget = getattr(provider, "config", {}).get("max_prompt_chars")
    if configured_budget:
        return int(configured_budget)
    return _DEFAULT_PROMPT_CHAR_BUDGET


def _combined_prompt_chars(system_prompt: str, user_prompt: str) -> int:
    return len(f"{system_prompt}\n\n{user_prompt}")


def _normalise_section_title(line: str) -> str:
    if match := _MARKDOWN_HEADING_RE.match(line):
        title = match.group("title")
    else:
        title = line
    title = title.strip().strip("*_`")
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[\s:.;-]+$", "", title)
    return title.upper()


def _is_matching_section_heading(
    line: str,
    section_titles: set[str],
    *,
    section_prefixes: tuple[str, ...] = (),
) -> bool:
    title = _normalise_section_title(line)
    if title in section_titles:
        return True
    return any(title.startswith(prefix) for prefix in section_prefixes)


def _is_references_heading(line: str) -> bool:
    return _is_matching_section_heading(line, _REFERENCE_SECTION_TITLES)


def _is_appendix_heading(line: str) -> bool:
    return _is_matching_section_heading(
        line,
        _APPENDIX_SECTION_TITLES,
        section_prefixes=("APPENDIX ", "APPENDICES "),
    )


def _drop_section_and_following(paper_text: str, heading_matcher: Callable[[str], bool]) -> str:
    output = []
    for line in paper_text.splitlines():
        if heading_matcher(line):
            break
        output.append(line)
    return "\n".join(output)


def _drop_references_section(paper_text: str) -> str:
    return _drop_section_and_following(paper_text, _is_references_heading)


def _drop_appendix_section(paper_text: str) -> str:
    return _drop_section_and_following(paper_text, _is_appendix_heading)


def fit_prompt_to_provider_budget(
    provider: Provider,
    system_prompt: str,
    paper_text: str,
    template: str,
    prompt_template: str,
    *,
    source_metadata: SourceMetadata | None = None,
    worked_example: str = "",
) -> tuple[str, str]:
    """Build a user prompt, removing low-value sections only if needed."""
    user_prompt = create_user_prompt(
        paper_text,
        template,
        prompt_template,
        source_metadata=source_metadata,
        worked_example=worked_example,
    )
    budget = _get_prompt_char_budget(provider)
    combined_chars = _combined_prompt_chars(system_prompt, user_prompt)
    if combined_chars <= budget:
        return paper_text, user_prompt

    LOGGER.warning(
        "Combined prompt is %s chars, above %s budget of %s chars. Applying fallback prompt reduction.",
        combined_chars,
        getattr(provider, "provider_name", provider.__class__.__name__),
        budget,
    )

    reduced_text = paper_text
    for label, reducer in (
        ("references", _drop_references_section),
        ("appendix", _drop_appendix_section),
    ):
        candidate_text = reducer(reduced_text)
        if candidate_text == reduced_text:
            continue

        candidate_prompt = create_user_prompt(
            candidate_text,
            template,
            prompt_template,
            source_metadata=source_metadata,
            worked_example=worked_example,
        )
        candidate_chars = _combined_prompt_chars(system_prompt, candidate_prompt)
        LOGGER.info(
            "Prompt reduction dropped %s: %s -> %s combined chars.",
            label,
            combined_chars,
            candidate_chars,
        )
        reduced_text = candidate_text
        user_prompt = candidate_prompt
        combined_chars = candidate_chars

        if combined_chars <= budget:
            return reduced_text, user_prompt

    raise PaperSummariserError(
        f"Combined prompt is {combined_chars} chars after cleanup, above "
        f"{getattr(provider, 'provider_name', provider.__class__.__name__)} "
        f"budget of {budget} chars."
    )


def _get_marker_models():
    """Lazy-load and cache marker-pdf models. Only called when text extraction is needed."""
    global _marker_models
    if _marker_models is None:
        from marker.models import create_model_dict

        LOGGER.info("Loading marker-pdf models (one-time initialisation)...")
        _marker_models = create_model_dict()
        LOGGER.info("marker-pdf models loaded.")
    return _marker_models


def read_input_file(
    file_path: Path,
    provider: Provider,
    config: LlmConfig,
) -> tuple[bytes | str | None, str | None]:
    """Read content from a local PDF or text file using the upstream extraction path."""
    try:
        file_size = file_path.stat().st_size
        if file_size > config.max_pdf_size_mb * 1024 * 1024:
            return None, f"File too large: {file_size / 1024 / 1024:.1f}MB (max {config.max_pdf_size_mb}MB)"

        file_suffix = file_path.suffix.lower()
        if file_suffix == ".pdf":
            if provider.supports_direct_pdf():
                LOGGER.info("Reading PDF binary for direct upload: %s", file_path.name)
                return file_path.read_bytes(), None

            LOGGER.info("Extracting text from PDF using marker-pdf: %s", file_path.name)
            from marker.config.parser import ConfigParser
            from marker.output import text_from_rendered

            marker_config = {
                "output_format": "markdown",
                "disable_image_extraction": True,
                "use_llm": False,
            }
            config_parser = ConfigParser(marker_config)
            converter_cls = config_parser.get_converter_cls()
            converter = converter_cls(
                config=config_parser.generate_config_dict(),
                artifact_dict=_get_marker_models(),
                processor_list=config_parser.get_processors(),
                renderer=config_parser.get_renderer(),
                llm_service=config_parser.get_llm_service(),
            )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(converter, str(file_path))
                try:
                    rendered = future.result(timeout=config.marker_timeout_seconds)
                except concurrent.futures.TimeoutError as error:
                    raise RuntimeError(
                        f"marker-pdf timed out after {config.marker_timeout_seconds}s "
                        f"processing {file_path.name}"
                    ) from error

            text, _, _ = text_from_rendered(rendered)
            text = normalise_extracted_text(text, source_name=file_path.name)
            LOGGER.info("Extracted ~%s words from PDF", len(text.split()))
            return text, None

        if file_suffix == ".txt":
            LOGGER.info("Reading text file: %s", file_path.name)
            text = file_path.read_text(encoding="utf-8")
            return normalise_extracted_text(text, source_name=file_path.name), None

        return None, f"Unsupported file type: {file_path.suffix}"
    except Exception as error:  # pragma: no cover - exercised by integration paths
        LOGGER.error("Error reading input file %s: %s", file_path.name, error, exc_info=True)
        return None, f"File read/processing error: {error}"


def _trim_trailing_punctuation(value: str) -> str:
    return value.rstrip(").,;:]")


def _extract_arxiv_id_from_filename(input_path: Path) -> str | None:
    stem = input_path.stem.strip()
    if not stem:
        return None
    if full_match := ARXIV_FILENAME_RE.fullmatch(stem):
        return full_match.group("id")
    if search_match := ARXIV_FILENAME_RE.search(stem):
        return search_match.group("id")
    return None


def _extract_arxiv_id_from_text(paper_text: str) -> str | None:
    if not paper_text:
        return None
    header_window = paper_text[:_SOURCE_SCAN_CHAR_LIMIT]
    if match := ARXIV_TEXT_RE.search(header_window):
        return match.group("id")
    return None


def _extract_doi_from_text(paper_text: str) -> str | None:
    if not paper_text:
        return None
    header_window = paper_text[:_SOURCE_SCAN_CHAR_LIMIT]
    if url_match := DOI_URL_RE.search(header_window):
        return _trim_trailing_punctuation(url_match.group("doi"))
    if doi_match := DOI_RE.search(header_window):
        return _trim_trailing_punctuation(doi_match.group(0))
    return None


def _published_label_from_arxiv_id(arxiv_id: str | None) -> str | None:
    if not arxiv_id:
        return None
    if match := ARXIV_NEW_STYLE_RE.match(arxiv_id):
        year = 2000 + int(match.group("yy"))
        month = MONTH_NAMES[int(match.group("mm"))]
        return f"{month} {year}"
    return None


def _canonical_arxiv_url(arxiv_id: str | None) -> str | None:
    if not arxiv_id:
        return None
    versionless_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
    return f"https://arxiv.org/abs/{versionless_id}"


def extract_source_metadata(input_path: Path, paper_text: str) -> SourceMetadata:
    if arxiv_id := _extract_arxiv_id_from_filename(input_path):
        return SourceMetadata(
            source_type="arxiv",
            identifier=arxiv_id,
            canonical_url=_canonical_arxiv_url(arxiv_id),
            published_label=_published_label_from_arxiv_id(arxiv_id),
            detection_method="filename",
        )
    if arxiv_id := _extract_arxiv_id_from_text(paper_text):
        return SourceMetadata(
            source_type="arxiv",
            identifier=arxiv_id,
            canonical_url=_canonical_arxiv_url(arxiv_id),
            published_label=_published_label_from_arxiv_id(arxiv_id),
            detection_method="paper_text",
        )
    if doi := _extract_doi_from_text(paper_text):
        return SourceMetadata(
            source_type="doi",
            identifier=doi,
            canonical_url=f"https://doi.org/{doi}",
            detection_method="paper_text",
        )
    return SourceMetadata()


def create_system_prompt(prompt_template: str) -> str:
    return prompt_template


def _create_source_metadata_block(source_metadata: SourceMetadata | None) -> str:
    if not source_metadata or not source_metadata.canonical_url:
        return ""

    block = (
        "<source_metadata>\n"
        f"Detected source identifier: {source_metadata.source_type}:{source_metadata.identifier}\n"
        f"Canonical paper link: {source_metadata.canonical_url}\n"
    )
    if source_metadata.published_label:
        block += f"Published line date: {source_metadata.published_label}\n"
    block += "You MUST use this exact link in the Published line.\n"
    if source_metadata.published_label:
        block += "You MUST use this exact month and year in the Published line.\n"
    block += "</source_metadata>\n\n"
    return block


def _create_paper_input_block(paper_text: str) -> str:
    if not paper_text:
        return ""
    return (
        "<input>\n"
        "Paper to summarize:\n\n"
        f"---BEGIN PAPER---\n{paper_text}\n---END PAPER---\n"
        "</input>"
    )


def _create_worked_example_block(worked_example: str) -> str:
    if not worked_example.strip():
        return ""
    return (
        "<worked_example>\n"
        "This fictional example shows the required density, bullet structure, "
        "footnote style, and quote format. Imitate the style and formatting, "
        "but do not copy its topic, claims, names, or references.\n\n"
        f"{worked_example.strip()}\n"
        "</worked_example>\n\n"
    )


def create_user_prompt(
    paper_text: str,
    template: str,
    prompt_template: str,
    *,
    source_metadata: SourceMetadata | None = None,
    worked_example: str = "",
) -> str:
    return Template(prompt_template).substitute(
        SUMMARY_TEMPLATE=template,
        SOURCE_METADATA_BLOCK=_create_source_metadata_block(source_metadata),
        WORKED_EXAMPLE_BLOCK=_create_worked_example_block(worked_example),
        PAPER_INPUT_BLOCK=_create_paper_input_block(paper_text),
    )


def write_debug_prompt(prompt_path: Path, system_prompt: str, user_prompt: str) -> None:
    try:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        full_prompt = f"SYSTEM PROMPT\n{system_prompt}\n\n---\n\nUSER PROMPT\n{user_prompt}"
        prompt_path.write_text(full_prompt, encoding="utf-8")
        LOGGER.info("Debug prompt written to %s", prompt_path)
    except Exception as error:  # pragma: no cover - debug-path best effort
        LOGGER.warning("Could not write debug prompt file: %s", error)


def call_llm_with_retry(
    provider: Provider,
    content: bytes | str,
    is_pdf: bool,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    max_retries: int,
    response_validator: Callable[[str], None] | None = None,
) -> str:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            LOGGER.info("Attempt %s/%s calling LLM...", attempt + 1, max_retries)
            summary = provider.process_document(
                content=content,
                is_pdf=is_pdf,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
            )
            if not summary or not summary.strip():
                raise ValueError("LLM returned empty or whitespace-only response")
            if _INLINE_FOOTNOTE_RE.search(summary) and not _REFERENCES_HEADING_RE.search(summary):
                raise ValueError("Summary has inline footnote markers but is missing the ## References section")
            if response_validator is not None:
                response_validator(summary)
            LOGGER.info("LLM call successful (received ~%s chars)", len(summary))
            return summary
        except Exception as error:
            last_error = error
            LOGGER.error(
                "Attempt %s failed — %s: %s",
                attempt + 1,
                provider.__class__.__name__,
                error,
            )
            if not is_retryable_llm_error(error) or attempt == max_retries - 1:
                break
            wait_time = 2 ** (attempt + 1)
            LOGGER.info("Waiting %ss before retry...", wait_time)
            time.sleep(wait_time)

    raise PaperSummariserError(str(last_error or "Unknown LLM failure"))


def _normalise_generated_section(markdown: str, expected_heading: str) -> str:
    """Return a generated section starting at its expected heading."""
    lines = [line.rstrip() for line in markdown.strip().splitlines()]
    for index, line in enumerate(lines):
        if line.strip() == expected_heading:
            return "\n".join(lines[index:]).strip()
    raise ValueError(f"Generated section is missing '{expected_heading}' heading")


def _non_empty_section_lines(section_markdown: str, heading: str) -> list[str]:
    """Return non-empty lines after a generated section heading."""
    lines = [line.strip() for line in section_markdown.splitlines()]
    if not lines or lines[0] != heading:
        raise ValueError(f"Generated section must start with '{heading}'")
    return [line for line in lines[1:] if line]


def _split_markdown_table_row(line: str) -> list[str] | None:
    """Return markdown table cells for a pipe-delimited row, or None."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_markdown_separator_row(cells: list[str] | None) -> bool:
    """Return True when table cells form a markdown separator row."""
    if cells is None:
        return False
    return all(re.match(r"^:?-{3,}:?$", cell) for cell in cells)


def validate_glossary_section(section_markdown: str) -> None:
    """Validate generated glossary markdown."""
    section = _normalise_generated_section(section_markdown, "## Glossary")
    content_lines = _non_empty_section_lines(section, "## Glossary")
    if len(content_lines) < 3:
        raise ValueError("Glossary section must contain a markdown table with rows")

    header_cells = _split_markdown_table_row(content_lines[0])
    separator_cells = _split_markdown_table_row(content_lines[1])
    if header_cells != ["Term", "Definition"]:
        raise ValueError("Glossary table must use columns 'Term' and 'Definition'")
    if len(separator_cells or []) != 2 or not _is_markdown_separator_row(separator_cells):
        raise ValueError("Glossary table is missing a markdown separator row")

    term_rows = content_lines[2:]
    for line in term_rows:
        cells = _split_markdown_table_row(line)
        if cells is None or len(cells) != 2:
            raise ValueError("Glossary section must contain only a two-column table")
        if not cells[0] or not cells[1]:
            raise ValueError("Glossary table rows must include a term and definition")
    if not term_rows:
        raise ValueError("Glossary table must contain at least one term row")
    if len(term_rows) > GLOSSARY_MAX_TERMS:
        raise ValueError(f"Glossary table must contain no more than {GLOSSARY_MAX_TERMS} terms")


def _extract_tags_from_line(line: str) -> list[str]:
    """Extract canonical hashtags from a generated tag line."""
    tags = []
    for match in re.finditer(r"#[A-Za-z0-9][A-Za-z0-9_./-]*", line or ""):
        token = "#" + re.sub(r"[^A-Za-z0-9]+", "", match.group(0).lstrip("#"))
        if token not in tags:
            tags.append(token)
    return tags


def _append_unique_tag(tags: list[str], tag: str) -> None:
    """Append a tag if it is present and not already in the list."""
    if tag and tag not in tags:
        tags.append(tag)


def _render_tags_section(proper_tags: list[str], science_tags: list[str]) -> str:
    """Render a canonical Tags section from classified tag lists."""
    lines = ["## Tags", ""]
    if proper_tags:
        lines.append(" ".join(proper_tags[:5]))
    if science_tags:
        if proper_tags:
            lines.append("")
        lines.append(" ".join(science_tags[:5]))
    return "\n".join(lines).rstrip()


def normalise_tags_section(section_markdown: str, available_keywords: str | None = None) -> str:
    """Return a canonical Tags section after classifying candidate tags."""
    section = _normalise_generated_section(section_markdown, "## Tags")
    tag_lines = _non_empty_section_lines(section, "## Tags")
    allowed_science_tags = set(iter_keyword_tags(available_keywords or "")) if available_keywords is not None else None
    proper_tags: list[str] = []
    science_tags: list[str] = []
    dropped_science_tags: list[str] = []

    for line_index, line in enumerate(tag_lines):
        for tag in _extract_tags_from_line(line):
            if allowed_science_tags is not None and tag in allowed_science_tags:
                _append_unique_tag(science_tags, tag)
            elif allowed_science_tags is not None and line_index > 0:
                _append_unique_tag(dropped_science_tags, tag)
            elif allowed_science_tags is None and line_index > 0:
                _append_unique_tag(science_tags, tag)
            else:
                _append_unique_tag(proper_tags, tag)

    if dropped_science_tags:
        LOGGER.warning(
            "Dropped science tags outside supplied keyword list: %s",
            ", ".join(dropped_science_tags),
        )
    if len(proper_tags) > 5:
        LOGGER.warning("Truncated proper-noun tags to 5 entries")
    if len(science_tags) > 5:
        LOGGER.warning("Truncated science tags to 5 entries")
    if not proper_tags and not science_tags:
        raise ValueError("Tags section did not contain any parseable hashtags")

    return _render_tags_section(proper_tags, science_tags)


def validate_tags_section(section_markdown: str, available_keywords: str | None = None) -> None:
    """Validate that generated tag markdown can be normalised."""
    normalise_tags_section(section_markdown, available_keywords)


def build_glossary_prompt(summary_text: str) -> tuple[str, str]:
    """Build the focused glossary-generation prompt."""
    system_prompt = (
        "You are an astrophysics editor. Return only the requested Markdown section. "
        "Use UK English and define only specialised terms or acronyms from the summary."
    )
    user_prompt = (
        "Create a concise glossary for this completed paper summary.\n\n"
        "Return exactly this Markdown shape:\n"
        "## Glossary\n\n"
        "| Term | Definition |\n"
        "|---|---|\n"
        "| **technical term** | One clear sentence explaining the term in context. |\n\n"
        f"Use no more than {GLOSSARY_MAX_TERMS} entries. Skip common scientific terms.\n"
        "Do not include introductory text, commentary, or any other section.\n\n"
        "---BEGIN SUMMARY---\n"
        f"{summary_text}\n"
        "---END SUMMARY---"
    )
    return system_prompt, user_prompt


def build_tags_prompt(summary_text: str, keywords: str) -> tuple[str, str]:
    """Build the focused tag-generation prompt."""
    system_prompt = (
        "You are an astronomy indexing assistant. Return only the requested Markdown "
        "section and select tags from the supplied summary and keyword list."
    )
    user_prompt = (
        "Create the tag block for this completed paper summary.\n\n"
        "Return exactly this Markdown shape:\n"
        "## Tags\n\n"
        "#ProperNoun1 #ProperNoun2 #ProperNoun3\n\n"
        "#ScienceKeyword1 #ScienceKeyword2 #ScienceKeyword3\n\n"
        "Rules:\n"
        "- First hashtag line: telescopes, surveys, datasets, missions, instruments, "
        "models, software, or named catalogues from the summary only; no more than 5.\n"
        "- Second hashtag line: choose no more than 5 science-area hashtags from the "
        "available keyword list only.\n"
        "- Use spaces between hashtags, not commas, bullets, or labels.\n"
        "- If fewer than 5 tags are justified, use fewer.\n"
        "- Do not include introductory text, commentary, or any other section.\n\n"
        "Available science-area keywords:\n"
        f"{keywords}\n\n"
        "---BEGIN SUMMARY---\n"
        f"{summary_text}\n"
        "---END SUMMARY---"
    )
    return system_prompt, user_prompt


def generate_glossary(summary_text: str, provider: Provider, *, config: LlmConfig) -> str:
    """Generate and validate a glossary section from the completed summary."""
    system_prompt, user_prompt = build_glossary_prompt(summary_text)
    try:
        section = call_llm_with_retry(
            provider,
            "",
            False,
            system_prompt,
            user_prompt,
            max_tokens=config.max_output_tokens,
            max_retries=config.retry_attempts,
            response_validator=validate_glossary_section,
        )
        return _normalise_generated_section(section, "## Glossary")
    except (PaperSummariserError, ValueError) as error:
        LOGGER.warning("Glossary generation failed; skipping section: %s", error)
        return ""


def generate_tags(summary_text: str, keywords: str, provider: Provider, *, config: LlmConfig) -> str:
    """Generate a best-effort tags section from the completed summary."""
    system_prompt, user_prompt = build_tags_prompt(summary_text, keywords)
    try:
        section = call_llm_with_retry(
            provider,
            "",
            False,
            system_prompt,
            user_prompt,
            max_tokens=config.max_output_tokens,
            max_retries=config.retry_attempts,
        )
    except PaperSummariserError as error:
        LOGGER.warning("Tag LLM call failed; using fallback tags: %s", error)
        return build_fallback_tags(summary_text, keywords)
    try:
        return normalise_tags_section(section, available_keywords=keywords)
    except ValueError as error:
        LOGGER.warning("Tag output could not be normalised; using fallback tags: %s", error)
        return build_fallback_tags(summary_text, keywords)


def build_fallback_tags(summary_text: str, keywords: str) -> str:
    """Build a minimal science-tag section from keyword matches in the summary."""
    normalised_summary = _normalise_search_text(summary_text)
    science_tags = []
    for tag in iter_keyword_tags(keywords):
        words = _split_tag_words(tag.lstrip("#"))
        if words and all(word in normalised_summary for word in words):
            science_tags.append(tag)
        if len(science_tags) == 5:
            break
    if not science_tags:
        LOGGER.warning("Could not derive fallback tags from summary text")
    return _render_tags_section([], science_tags)


def insert_section(summary: str, section_markdown: str, before_heading: str = "## References") -> str:
    """Insert a generated markdown section before another heading, or append it."""
    summary_lines = summary.rstrip().splitlines()
    section = section_markdown.strip()

    insert_index = None
    for index, line in enumerate(summary_lines):
        if line.strip() == before_heading:
            insert_index = index
            break

    if insert_index is None:
        return f"{summary.rstrip()}\n\n{section}\n"

    before = "\n".join(summary_lines[:insert_index]).rstrip()
    after = "\n".join(summary_lines[insert_index:]).lstrip()
    return f"{before}\n\n{section}\n\n{after}\n"


def strip_preamble(summary_content: str) -> str:
    lines = summary_content.split("\n")
    title_index = -1
    for index, line in enumerate(lines):
        if line.strip().startswith("# "):
            title_index = index
            break
    if title_index >= 0:
        return "\n".join(lines[title_index:])
    LOGGER.warning("Could not find title heading ('# ') to strip preamble.")
    return summary_content


def validate_summary(summary_content: str) -> None:
    lines = [line.strip() for line in summary_content.split("\n") if line.strip()]
    if not lines:
        LOGGER.warning("VALIDATION WARNING: Summary content is empty.")
        return

    start_with_title = lines[0].startswith("# ")
    has_sections = any(line.startswith("## ") for line in lines)

    LOGGER.info("Validation results:")
    LOGGER.info("  Starts with title: %s", start_with_title)
    LOGGER.info("  Has section headings: %s", has_sections)

    if not start_with_title:
        LOGGER.warning("VALIDATION WARNING: Summary does not start with title heading '# '")
    if not has_sections:
        LOGGER.warning("VALIDATION WARNING: Summary does not contain any second-level section headings")


def extract_summary_sections(summary_content: str) -> str:
    lines = summary_content.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("## "):
            return "\n".join(lines[index:]).strip() + "\n"
    return summary_content.strip() + "\n"
