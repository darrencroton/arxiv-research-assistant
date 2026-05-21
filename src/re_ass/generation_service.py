"""App-side content generation orchestration for re-ass."""

from __future__ import annotations

import logging
import math
from pathlib import Path
import re

from re_ass.models import ArxivPaper
from re_ass.paper_summariser import PaperSummariser, PaperSummariserError
from re_ass.paper_summariser.providers import create_provider
from re_ass.paper_summariser.providers.base import Provider
from re_ass.paper_summariser.service import download_arxiv_pdf
from re_ass.prompt_logger import PromptLogger
from re_ass.settings import LlmConfig


LOGGER = logging.getLogger(__name__)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WEEKLY_SUMMARY_LINE = re.compile(r"(?m)^\*\*Summary:\*\*\s*(.+?)\s*$")
_TRAILING_ARXIV_LINK = re.compile(r"\s+\[arXiv:[^\]]+\]\([^)]+\)\s*$")
_MARKDOWN_LIST_ITEM = re.compile(r"^(?:[-*+]\s+|\d+\.\s+)")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+")
# Matches a leading H1 or H2 marker so it can be demoted to H3. The synthesis
# body sits inside an H2 managed section; any H1/H2 the model emits would be
# treated as the *next* section boundary by the note manager and would split
# the synthesis on the following write.
_TOP_LEVEL_HEADING_PREFIX = re.compile(r"^#{1,2}(?=\s)")


class GenerationError(RuntimeError):
    """Raised when the generation service cannot complete a requested step."""


def make_provider(config: LlmConfig) -> Provider:
    """Create and validate a provider from an LlmConfig."""
    provider = create_provider(config.mode, config.provider, config=config.provider_config())
    readiness_validator = getattr(provider, "validate_runtime_ready", None)
    if callable(readiness_validator):
        readiness_validator()
    return provider


class GenerationService:
    """Owns app-side text generation and the vendored paper summariser."""

    def __init__(
        self,
        *,
        config: LlmConfig,
        tag_categories: tuple[str, ...] | None = None,
        provider: Provider | None = None,
        paper_summariser: PaperSummariser | None = None,
        prompt_logger: PromptLogger | None = None,
    ) -> None:
        self.config = config
        self.prompt_logger = prompt_logger
        self.provider = provider or make_provider(self.config)
        if provider is not None:
            readiness_validator = getattr(self.provider, "validate_runtime_ready", None)
            if callable(readiness_validator):
                readiness_validator()

        if paper_summariser is None:
            if not tag_categories:
                raise ValueError("Tag categories are required when creating the default paper summariser.")
            write_prompt_fn = (
                (lambda lbl, sys, usr: prompt_logger.write(lbl, sys, usr))
                if prompt_logger else None
            )
            paper_summariser = PaperSummariser(
                provider=self.provider,
                config=self.config,
                tag_categories=tag_categories,
                write_prompt_fn=write_prompt_fn,
            )
        self.paper_summariser = paper_summariser

    def generate_micro_summary(self, paper: ArxivPaper) -> str:
        """Generate a 1-2 sentence micro-summary from title and abstract."""
        try:
            response = self._run_text_prompt(
                "Summarise the following arXiv abstract in 1-2 sentences. Return plain text only.",
                f"Title: {paper.title}\nAbstract: {paper.summary}",
                max_tokens=min(self.config.max_output_tokens, 512),
                label="micro-summary",
            )
            cleaned = self._clean_text(response)
            if cleaned:
                return cleaned
        except GenerationError as error:
            LOGGER.warning("Micro-summary generation failed for %s: %s", paper.title, error)

        return self._fallback_micro_summary(paper.summary)

    def stage_pdf_download(self, paper: ArxivPaper, destination_dir: Path) -> Path:
        """Download a paper PDF to a staging directory owned by the pipeline."""
        try:
            return download_arxiv_pdf(paper, destination_dir, self.config)
        except PaperSummariserError as error:
            raise GenerationError(str(error)) from error

    def build_paper_note_content(self, paper: ArxivPaper, staged_source_path: Path) -> str:
        """Return final note content for a paper using the vendored summariser output."""
        try:
            summary = self.paper_summariser.summarise_source(paper, staged_source_path)
            return summary.raw_summary
        except PaperSummariserError as error:
            raise GenerationError(f"Unable to create paper note for {paper.title}: {error}") from error

    def generate_weekly_synthesis(
        self,
        existing_synthesis: str,
        weekly_additions: str,
        *,
        word_limit: int,
        max_tokens: int = 4096,
    ) -> str:
        """Generate or update the weekly synthesis text from all weekly additions so far."""
        try:
            _max_tokens = min(self.config.max_output_tokens, max_tokens)
            system_prompt = (
                "Rewrite the weekly synthesis for this rolling research note from the full set of weekly paper "
                "additions gathered so far. Produce a concise markdown synthesis that explains cross-paper "
                "themes, methodological connections, tensions, and how the week's story is evolving. "
                "Prioritise synthesis over a paper-by-paper recap. Lead with the strongest cross-paper "
                "thread; bring in a counterpoint where one exists. Prose, not bullets — use bullets only "
                "when they genuinely improve readability. When several papers share a theme, group them "
                "under a short bold header or opening sentence — especially later in the week when the "
                "word budget is larger. Keep the note quickly digestible, return markdown "
                f"only. You must write no more than {word_limit} words.\n\n"
                "Output rules: return ONLY the body text. Do NOT include any '#' or '##' headings — the "
                "calling note already supplies the section heading; an H1 or H2 in your output would split "
                "the surrounding note. Use H3 ('### ') or bold prose for any internal structure."
            )
            user_prompt = (
                f"Current synthesis:\n{existing_synthesis or '(none)'}\n\n"
                f"Weekly paper additions so far:\n{weekly_additions or '(none)'}"
            )
            response = self._run_text_prompt(system_prompt, user_prompt, max_tokens=_max_tokens, label="weekly-synthesis")
            cleaned = self._clean_weekly_synthesis(response)
            if cleaned:
                word_count = len(cleaned.split())
                overage_limit = round(word_limit * 1.1)
                for retry_index in range(1, self.config.retry_attempts + 1):
                    if word_count <= overage_limit:
                        break
                    overage_pct = (word_count - word_limit) / word_count * 100
                    shorten_by = math.ceil(overage_pct / 5) * 5 + 5
                    retry_system = (
                        "You are an expert editor. A weekly synthesis is a concise markdown narrative that explains "
                        "cross-paper themes, connections, and tensions from a week of research papers. It prioritises "
                        "synthesis over a paper-by-paper recap and leads with the strongest cross-paper insights. "
                        f"Your task is to shorten the synthesis below by {shorten_by}%. "
                        "Drop weaker results, merge overlapping points, and keep only the strongest themes and tensions. "
                        "Return only the shortened synthesis in the same markdown style."
                    )
                    retry_user = f"Synthesis to shorten:\n\n{cleaned}"
                    try:
                        response = self._run_text_prompt(retry_system, retry_user, max_tokens=_max_tokens, label="weekly-synthesis-retry")
                        cleaned = self._clean_weekly_synthesis(response) or cleaned
                        word_count = len(cleaned.split())
                    except GenerationError as retry_error:
                        LOGGER.warning(
                            "Weekly synthesis retry %s/%s failed, preserving current draft: %s",
                            retry_index,
                            self.config.retry_attempts,
                            retry_error,
                        )
            if cleaned:
                return self._truncate_markdown_words(cleaned, limit=round(word_limit * 1.1))
        except GenerationError as error:
            LOGGER.warning("Weekly synthesis generation failed: %s", error)

        return self._fallback_weekly_synthesis(existing_synthesis, weekly_additions, word_limit=word_limit)

    def _run_text_prompt(self, system_prompt: str, user_prompt: str, *, max_tokens: int, label: str = "prompt") -> str:
        if self.prompt_logger:
            self.prompt_logger.write(label, system_prompt, user_prompt)
        try:
            return self.provider.process_document(
                content="",
                is_pdf=False,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
            ).strip()
        except Exception as error:
            raise GenerationError(str(error)) from error

    def _fallback_micro_summary(self, abstract: str) -> str:
        sentences = [sentence.strip() for sentence in _SENTENCE_SPLIT.split(abstract.strip()) if sentence.strip()]
        candidate = " ".join(sentences[:2]) if sentences else abstract.strip()
        return self._truncate_words(candidate, limit=45)

    def _fallback_weekly_synthesis(self, existing_synthesis: str, weekly_additions: str, *, word_limit: int) -> str:
        summaries = self._extract_weekly_micro_summaries(weekly_additions)
        if not summaries:
            return self._truncate_markdown_words(self._clean_weekly_synthesis(existing_synthesis), limit=word_limit)

        base_text = "This week's notable papers include " + "; ".join(summaries) + "."
        return self._truncate_markdown_words(self._clean_weekly_synthesis(base_text), limit=word_limit)

    def _clean_text(self, text: str) -> str:
        cleaned = text.strip().strip('"').strip("'")
        cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def _clean_weekly_synthesis(self, text: str) -> str:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip().strip('"').strip("'")
        if not cleaned:
            return ""

        output_lines: list[str] = []
        paragraph_parts: list[str] = []

        def flush_paragraph() -> None:
            if paragraph_parts:
                output_lines.append(" ".join(paragraph_parts).strip())
                paragraph_parts.clear()

        for raw_line in cleaned.split("\n"):
            stripped = raw_line.strip()
            if not stripped:
                flush_paragraph()
                if output_lines and output_lines[-1] != "":
                    output_lines.append("")
                continue

            if _MARKDOWN_LIST_ITEM.match(stripped) or _MARKDOWN_HEADING.match(stripped):
                flush_paragraph()
                output_lines.append(_TOP_LEVEL_HEADING_PREFIX.sub("###", stripped))
                continue

            paragraph_parts.append(stripped)

        flush_paragraph()

        while output_lines and output_lines[0] == "":
            output_lines.pop(0)
        while output_lines and output_lines[-1] == "":
            output_lines.pop()
        return "\n".join(output_lines)

    def _truncate_words(self, text: str, *, limit: int) -> str:
        words = text.split()
        if len(words) <= limit:
            return " ".join(words).strip()
        return " ".join(words[:limit]).rstrip(".,;:") + "..."

    def _truncate_markdown_words(self, text: str, *, limit: int) -> str:
        if limit <= 0:
            return ""

        output_lines: list[str] = []
        remaining = limit

        def append_ellipsis_to_last_content_line() -> None:
            for index in range(len(output_lines) - 1, -1, -1):
                if output_lines[index]:
                    output_lines[index] = output_lines[index].rstrip(".,;:") + "..."
                    return

        for raw_line in text.split("\n"):
            stripped = raw_line.strip()
            if not stripped:
                if output_lines and output_lines[-1] != "":
                    output_lines.append("")
                continue

            prefix = ""
            content = stripped
            for pattern in (_MARKDOWN_LIST_ITEM, _MARKDOWN_HEADING):
                match = pattern.match(stripped)
                if match is not None:
                    prefix = match.group(0)
                    content = stripped[len(prefix) :].strip()
                    break

            words = content.split()
            if len(words) <= remaining:
                output_lines.append(f"{prefix}{' '.join(words)}".rstrip())
                remaining -= len(words)
                continue

            if remaining > 0:
                truncated = " ".join(words[:remaining]).rstrip(".,;:")
                if truncated:
                    output_lines.append(f"{prefix}{truncated}...")
            else:
                append_ellipsis_to_last_content_line()
            break

        while output_lines and output_lines[-1] == "":
            output_lines.pop()
        return "\n".join(output_lines).strip()

    def _extract_weekly_micro_summaries(self, weekly_additions: str) -> list[str]:
        summaries: list[str] = []
        for match in _WEEKLY_SUMMARY_LINE.findall(weekly_additions):
            cleaned = _TRAILING_ARXIV_LINK.sub("", match).strip().rstrip(".")
            if cleaned:
                summaries.append(cleaned)
        return summaries
