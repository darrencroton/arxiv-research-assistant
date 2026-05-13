from pathlib import Path

import pytest

from re_ass.generation_service import GenerationError, GenerationService
from re_ass.paper_summariser.service import GeneratedPaperSummary, PaperSummariserError, SourceMetadata
from tests.support import make_app_config, make_paper


class StubPaperSummariser:
    def __init__(self, *, raw_summary: str | None = None, error: Exception | None = None) -> None:
        self.raw_summary = raw_summary
        self.error = error

    def summarise_source(self, paper, source_path: Path):
        if self.error is not None:
            raise self.error
        return GeneratedPaperSummary(
            raw_summary=self.raw_summary or "",
            source_metadata=SourceMetadata(),
            pdf_url=paper.arxiv_url,
        )


class ReadinessFailingProvider:
    def validate_runtime_ready(self) -> None:
        raise ValueError("login missing")


class RecordingProvider:
    def __init__(self, *, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def validate_runtime_ready(self) -> None:
        return None

    def process_document(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FailingTextProvider:
    def validate_runtime_ready(self) -> None:
        return None

    def process_document(self, **_kwargs):
        raise RuntimeError("synthetic failure")


def test_generation_service_fails_fast_when_provider_cannot_be_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("re_ass.generation_service.create_provider", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("missing binary")))

    with pytest.raises(ValueError, match="missing binary"):
        GenerationService(config=make_app_config(tmp_path).llm)


def test_generation_service_fails_fast_when_provider_is_not_runtime_ready(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="login missing"):
        GenerationService(config=make_app_config(tmp_path).llm, provider=ReadinessFailingProvider())


def test_generation_service_requires_tag_categories_for_default_summariser(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Tag categories"):
        GenerationService(config=make_app_config(tmp_path).llm, provider=RecordingProvider(response=""))


def test_generation_service_preserves_summariser_output_verbatim(tmp_path: Path) -> None:
    raw_summary = (
        "# Example Paper\n\n"
        "Authors: Doe J.\n"
        "Published: March 2026 ([Link](https://arxiv.org/abs/2603.12345))\n\n"
        "## Key Ideas\n"
        "- Point one.\n"
    )
    service = GenerationService(
        config=make_app_config(tmp_path).llm,
        provider=object(),
        paper_summariser=StubPaperSummariser(raw_summary=raw_summary),
    )

    note = service.build_paper_note_content(make_paper(title="Example Paper"), tmp_path / "paper.pdf")

    assert note == raw_summary


def test_generation_service_raises_when_summariser_fails(tmp_path: Path) -> None:
    service = GenerationService(
        config=make_app_config(tmp_path).llm,
        provider=object(),
        paper_summariser=StubPaperSummariser(error=PaperSummariserError("LLM failure")),
    )

    with pytest.raises(GenerationError, match="Unable to create paper note"):
        service.build_paper_note_content(make_paper(title="Broken Paper"), tmp_path / "paper.pdf")


def test_generate_weekly_synthesis_uses_full_weekly_additions_and_word_limit(tmp_path: Path) -> None:
    provider = RecordingProvider(response="Lead line.\n- Theme one\n- Theme two")
    service = GenerationService(
        config=make_app_config(tmp_path).llm,
        provider=provider,
        paper_summariser=StubPaperSummariser(),
    )
    weekly_additions = (
        "### Monday 23rd\n\n"
        "**Title:** [[Paper One]]\n\n"
        "**Summary:** First summary. [arXiv:2603.10001](https://arxiv.org/abs/2603.10001)\n\n"
        "---\n\n"
        "### Tuesday 24th\n\n"
        "**Title:** [[Paper Two]]\n\n"
        "**Summary:** Second summary. [arXiv:2603.10002](https://arxiv.org/abs/2603.10002)\n"
    )

    synthesis = service.generate_weekly_synthesis("Earlier synthesis.", weekly_additions, word_limit=150)

    assert synthesis == "Lead line.\n- Theme one\n- Theme two"
    assert provider.calls == [
        {
            "content": "",
            "is_pdf": False,
            "system_prompt": (
                "Rewrite the weekly synthesis for this rolling research note from the full set of weekly paper "
                "additions gathered so far. Produce a concise markdown synthesis that explains cross-paper "
                "themes, methodological connections, tensions, and how the week's story is evolving. "
                "Prioritise synthesis over a paper-by-paper recap. Lead with the strongest cross-paper "
                "thread; bring in a counterpoint where one exists. Prose, not bullets — use bullets only "
                "when they genuinely improve readability. Keep the note quickly digestible, return markdown "
                "only, and aim for around 150 words.\n\n"
                "Output rules: return ONLY the body text. Do NOT include any '#' or '##' headings — the "
                "calling note already supplies the section heading; an H1 or H2 in your output would split "
                "the surrounding note. Use H3 ('### ') or bold prose for any internal structure."
            ),
            "user_prompt": (
                "Current synthesis:\nEarlier synthesis.\n\n"
                "Weekly paper additions so far:\n"
                "### Monday 23rd\n\n"
                "**Title:** [[Paper One]]\n\n"
                "**Summary:** First summary. [arXiv:2603.10001](https://arxiv.org/abs/2603.10001)\n\n"
                "---\n\n"
                "### Tuesday 24th\n\n"
                "**Title:** [[Paper Two]]\n\n"
                "**Summary:** Second summary. [arXiv:2603.10002](https://arxiv.org/abs/2603.10002)\n"
            ),
            "max_tokens": 4096,
        }
    ]


def test_generate_weekly_synthesis_preserves_digestible_markdown_structure(tmp_path: Path) -> None:
    provider = RecordingProvider(
        response="First line\ncontinues here.\n\n- Theme one\n- Theme two\n\nClosing thought."
    )
    service = GenerationService(
        config=make_app_config(tmp_path).llm,
        provider=provider,
        paper_summariser=StubPaperSummariser(),
    )

    synthesis = service.generate_weekly_synthesis("Earlier synthesis.", "**Summary:** First summary.\n", word_limit=50)

    assert synthesis == "First line continues here.\n\n- Theme one\n- Theme two\n\nClosing thought."


def test_generate_weekly_synthesis_demotes_h1_and_h2_headings_in_model_output(tmp_path: Path) -> None:
    """A model that returns a leading H1/H2 inside the body would split the
    surrounding note on the next write (the synthesis sits inside the H2
    managed section). Demote those headings to H3 so the body is safe."""
    provider = RecordingProvider(
        response="# Weekly view\n\n## Weekly Synthesis\n\nBody paragraph.\n\n### Subsection\n\nStill fine."
    )
    service = GenerationService(
        config=make_app_config(tmp_path).llm,
        provider=provider,
        paper_summariser=StubPaperSummariser(),
    )

    synthesis = service.generate_weekly_synthesis("Earlier synthesis.", "**Summary:** s\n", word_limit=50)

    lines = synthesis.splitlines()
    assert "# Weekly view" not in lines
    assert "## Weekly Synthesis" not in lines
    assert "### Weekly view" in lines
    assert "### Weekly Synthesis" in lines
    assert "### Subsection" in lines
    assert "Body paragraph." in lines


def test_generate_weekly_synthesis_returns_full_response_without_hard_truncation(tmp_path: Path) -> None:
    provider = RecordingProvider(response="Overview paragraph.\n\n- First theme with context\n- Second theme follows")
    service = GenerationService(
        config=make_app_config(tmp_path).llm,
        provider=provider,
        paper_summariser=StubPaperSummariser(),
    )

    synthesis = service.generate_weekly_synthesis("Earlier synthesis.", "**Summary:** First summary.\n", word_limit=6)

    assert synthesis == "Overview paragraph.\n\n- First theme with context\n- Second theme follows"


def test_generate_weekly_synthesis_fallback_rebuilds_from_all_weekly_summaries(tmp_path: Path) -> None:
    service = GenerationService(
        config=make_app_config(tmp_path).llm,
        provider=FailingTextProvider(),
        paper_summariser=StubPaperSummariser(),
    )
    weekly_additions = (
        "### Monday 23rd\n\n"
        "**Summary:** First summary. [arXiv:2603.10001](https://arxiv.org/abs/2603.10001)\n\n"
        "---\n\n"
        "### Tuesday 24th\n\n"
        "**Summary:** Second summary. [arXiv:2603.10002](https://arxiv.org/abs/2603.10002)\n"
    )

    synthesis = service.generate_weekly_synthesis("Stale synthesis.", weekly_additions, word_limit=12)

    assert synthesis == "This week's notable papers include First summary; Second summary."
