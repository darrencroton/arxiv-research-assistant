from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timezone
import logging
from pathlib import Path
import re
import shutil
import subprocess

from re_ass.models import ArxivPaper, ProcessedPaper, sanitize_note_name


LOGGER = logging.getLogger(__name__)
_PROMPT_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")


class LlmCommandError(RuntimeError):
    """Raised when the configured LLM CLI returns a failure."""


class LlmOrchestrator:
    def __init__(
        self,
        *,
        command_prefix: Sequence[str],
        timeout_seconds: int,
        enabled: bool,
        allow_local_paper_note_fallback: bool,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.command_prefix = tuple(command_prefix)
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.allow_local_paper_note_fallback = allow_local_paper_note_fallback
        self.runner = runner or subprocess.run

    def process_paper(self, paper: ArxivPaper, papers_dir: Path) -> ProcessedPaper:
        note_path = self.ensure_paper_note(paper, papers_dir)
        micro_summary = self.generate_micro_summary(paper)
        return ProcessedPaper(
            paper=paper,
            note_name=note_path.stem,
            note_path=note_path,
            micro_summary=micro_summary,
        )

    def ensure_paper_note(self, paper: ArxivPaper, papers_dir: Path) -> Path:
        papers_dir.mkdir(parents=True, exist_ok=True)
        expected_note_path = papers_dir / f"{sanitize_note_name(paper.title)}.md"
        if expected_note_path.exists():
            return expected_note_path

        if self._is_command_available():
            before_state = {path.resolve(): path.stat().st_mtime_ns for path in papers_dir.glob("*.md")}
            prompt = f"Use /summarise-paper skill to summarise {paper.arxiv_url} and write to {papers_dir}"
            try:
                response = self._run_prompt(prompt)
                detected_path = self._detect_created_note(papers_dir, before_state, paper, expected_note_path)
                if detected_path is not None:
                    return detected_path
                LOGGER.warning(
                    "Paper note command completed without creating a note for %s. CLI response: %s",
                    paper.title,
                    self._truncate_words(self._clean_summary(response), limit=40) or "<empty>",
                )
            except LlmCommandError as error:
                LOGGER.warning("Paper note generation failed for %s: %s", paper.title, error)

        if not self.allow_local_paper_note_fallback:
            raise LlmCommandError(f"Unable to create paper note for {paper.title}.")

        self._write_fallback_paper_note(expected_note_path, paper)
        return expected_note_path

    def generate_micro_summary(self, paper: ArxivPaper) -> str:
        if self._is_command_available():
            prompt = (
                "Summarise the following arXiv abstract in 1-2 sentences. "
                "Return plain text only.\n"
                f"Title: {paper.title}\n"
                f"Abstract: {paper.summary}"
            )
            try:
                response = self._run_prompt(prompt)
                cleaned = self._clean_summary(response)
                if cleaned:
                    return cleaned
            except LlmCommandError as error:
                LOGGER.warning("Micro-summary generation failed for %s: %s", paper.title, error)

        return self._fallback_micro_summary(paper.summary)

    def generate_weekly_synthesis(self, existing_synthesis: str, papers: Sequence[ProcessedPaper]) -> str:
        if self._is_command_available():
            bullet_summaries = "\n".join(f"- {paper.micro_summary}" for paper in papers)
            prompt = (
                "Update this weekly synthesis incorporating these new papers. "
                "Max 100 words. Return plain text only.\n"
                f"Current synthesis:\n{existing_synthesis}\n\n"
                f"New paper summaries:\n{bullet_summaries}"
            )
            try:
                response = self._run_prompt(prompt)
                cleaned = self._truncate_words(self._clean_summary(response), limit=100)
                if cleaned:
                    return cleaned
            except LlmCommandError as error:
                LOGGER.warning("Weekly synthesis generation failed: %s", error)

        return self._fallback_weekly_synthesis(existing_synthesis, papers)

    def _is_command_available(self) -> bool:
        if not self.enabled:
            return False
        return shutil.which(self.command_prefix[0]) is not None

    def _run_prompt(self, prompt: str) -> str:
        command = [*self.command_prefix, prompt]
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise LlmCommandError(f"Command timed out after {self.timeout_seconds} seconds.") from error
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise LlmCommandError(stderr or f"Command failed with exit code {result.returncode}.")
        return (result.stdout or "").strip()

    def _detect_created_note(
        self,
        papers_dir: Path,
        before_state: dict[Path, int],
        paper: ArxivPaper,
        expected_note_path: Path,
    ) -> Path | None:
        if expected_note_path.exists():
            return expected_note_path

        current_files = list(papers_dir.glob("*.md"))
        changed_files = [
            path
            for path in current_files
            if path.resolve() not in before_state
            or path.stat().st_mtime_ns > before_state[path.resolve()]
        ]
        if len(changed_files) == 1:
            return changed_files[0]

        matching_titles = [
            path
            for path in current_files
            if sanitize_note_name(path.stem).casefold() == sanitize_note_name(paper.title).casefold()
        ]
        if len(matching_titles) == 1:
            return matching_titles[0]

        return None

    def _write_fallback_paper_note(self, note_path: Path, paper: ArxivPaper) -> None:
        published_date = paper.published.astimezone(timezone.utc).date().isoformat()
        content = (
            f"# {paper.title}\n\n"
            f"- ArXiv: [{paper.arxiv_url}]({paper.arxiv_url})\n"
            f"- Published: {published_date}\n"
            f"- Authors: {', '.join(paper.authors)}\n"
            f"- Categories: {', '.join(paper.categories)}\n\n"
            "## Abstract\n"
            f"{paper.summary}\n\n"
            "## Notes\n"
            "LLM-generated note creation was unavailable, so this fallback note preserves the core metadata.\n"
        )
        note_path.write_text(content, encoding="utf-8")

    def _fallback_micro_summary(self, abstract: str) -> str:
        sentences = [sentence.strip() for sentence in _PROMPT_SPLIT_PATTERN.split(abstract.strip()) if sentence.strip()]
        candidate = " ".join(sentences[:2]) if sentences else abstract.strip()
        return self._truncate_words(candidate, limit=45)

    def _fallback_weekly_synthesis(self, existing_synthesis: str, papers: Sequence[ProcessedPaper]) -> str:
        summaries = [paper.micro_summary.rstrip(".") for paper in papers if paper.micro_summary.strip()]
        if not summaries:
            return self._truncate_words(existing_synthesis.strip(), limit=100)

        if self._is_placeholder_synthesis(existing_synthesis):
            base_text = "This week's notable papers include " + "; ".join(summaries) + "."
        else:
            base_text = existing_synthesis.strip().rstrip(".") + ". New additions include " + "; ".join(summaries) + "."
        return self._truncate_words(base_text, limit=100)

    def _clean_summary(self, text: str) -> str:
        cleaned = text.strip().strip('"').strip("'")
        cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def _truncate_words(self, text: str, *, limit: int) -> str:
        words = text.split()
        if len(words) <= limit:
            return " ".join(words).strip()
        return " ".join(words[:limit]).rstrip(".,;:") + "..."

    def _is_placeholder_synthesis(self, synthesis: str) -> bool:
        normalized = synthesis.casefold()
        return "automatically generated here" in normalized or normalized.startswith("*(")
