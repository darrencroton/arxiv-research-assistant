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


def test_generation_service_fails_fast_when_provider_cannot_be_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("re_ass.generation_service.create_provider", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("missing binary")))

    with pytest.raises(ValueError, match="missing binary"):
        GenerationService(config=make_app_config(tmp_path).llm)


def test_generation_service_fails_fast_when_provider_is_not_runtime_ready(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="login missing"):
        GenerationService(config=make_app_config(tmp_path).llm, provider=ReadinessFailingProvider())


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
