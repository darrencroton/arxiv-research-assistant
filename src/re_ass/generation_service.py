"""App-side content generation orchestration for re-ass."""

from __future__ import annotations

import logging
from pathlib import Path
import re

from re_ass.models import ArxivPaper
from re_ass.paper_summariser import PaperSummariser, PaperSummariserError
from re_ass.paper_summariser.providers import create_provider
from re_ass.paper_summariser.providers.base import Provider
from re_ass.paper_summariser.service import download_arxiv_pdf
from re_ass.settings import LlmConfig


LOGGER = logging.getLogger(__name__)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WEEKLY_SUMMARY_LINE = re.compile(r"(?m)^\*\*Summary:\*\*\s*(.+?)\s*$")


class GenerationError(RuntimeError):
    """Raised when the generation service cannot complete a requested step."""


class GenerationService:
    """Owns app-side text generation and the vendored paper summariser."""

    def __init__(
        self,
        *,
        config: LlmConfig,
        provider: Provider | None = None,
        paper_summariser: PaperSummariser | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or create_provider(
            self.config.mode,
            self.config.provider,
            config=self.config.provider_config(),
        )
        readiness_validator = getattr(self.provider, "validate_runtime_ready", None)
        if callable(readiness_validator):
            readiness_validator()

        self.paper_summariser = paper_summariser or PaperSummariser(
            provider=self.provider,
            config=self.config,
        )

    def generate_micro_summary(self, paper: ArxivPaper) -> str:
        """Generate a 1-2 sentence micro-summary from title and abstract."""
        if self.provider is not None:
            try:
                response = self._run_text_prompt(
                    "Summarise the following arXiv abstract in 1-2 sentences. Return plain text only.",
                    f"Title: {paper.title}\nAbstract: {paper.summary}",
                    max_tokens=min(self.config.max_output_tokens, 512),
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

    def generate_weekly_synthesis(self, existing_synthesis: str, weekly_additions: str, *, word_limit: int) -> str:
        """Generate or update the weekly synthesis text from all weekly additions so far."""
        if self.provider is not None:
            try:
                response = self._run_text_prompt(
                    (
                        "Rewrite the weekly synthesis for this rolling research note from the full set of weekly paper "
                        "additions gathered so far. Produce one cohesive plain-text paragraph that synthesises cross-paper "
                        "themes, methodological connections, tensions, and how the week's story is evolving. Prefer "
                        "synthesis over listing papers one by one. Stay within "
                        f"{word_limit} words."
                    ),
                    (
                        f"Current synthesis:\n{existing_synthesis or '(none)'}\n\n"
                        f"Weekly paper additions so far:\n{weekly_additions or '(none)'}"
                    ),
                    max_tokens=min(self.config.max_output_tokens, 768),
                )
                cleaned = self._truncate_words(self._clean_text(response), limit=word_limit)
                if cleaned:
                    return cleaned
            except GenerationError as error:
                LOGGER.warning("Weekly synthesis generation failed: %s", error)

        return self._fallback_weekly_synthesis(existing_synthesis, weekly_additions, word_limit=word_limit)

    def _run_text_prompt(self, system_prompt: str, user_prompt: str, *, max_tokens: int) -> str:
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
            return self._truncate_words(existing_synthesis.strip(), limit=word_limit)

        base_text = "This week's notable papers include " + "; ".join(summaries) + "."
        return self._truncate_words(base_text, limit=word_limit)

    def _clean_text(self, text: str) -> str:
        cleaned = text.strip().strip('"').strip("'")
        cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def _truncate_words(self, text: str, *, limit: int) -> str:
        words = text.split()
        if len(words) <= limit:
            return " ".join(words).strip()
        return " ".join(words[:limit]).rstrip(".,;:") + "..."

    def _extract_weekly_micro_summaries(self, weekly_additions: str) -> list[str]:
        return [match.rstrip(".") for match in _WEEKLY_SUMMARY_LINE.findall(weekly_additions) if match.strip()]
