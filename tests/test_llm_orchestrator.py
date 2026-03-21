from pathlib import Path
import subprocess

import pytest

from re_ass.llm_orchestrator import LlmOrchestrator
from re_ass.models import ArxivPaper


def make_paper() -> ArxivPaper:
    return ArxivPaper(
        title="Agents for Research",
        summary="This paper studies tool-using agents. It compares planning and execution loops.",
        arxiv_url="https://arxiv.org/abs/1234.5678",
        entry_id="https://arxiv.org/abs/1234.5678",
        authors=("Jane Doe", "John Smith"),
        primary_category="cs.AI",
        categories=("cs.AI", "cs.CL"),
        published=__import__("datetime").datetime(2026, 3, 21, 12, 0, tzinfo=__import__("datetime").timezone.utc),
        updated=__import__("datetime").datetime(2026, 3, 21, 12, 0, tzinfo=__import__("datetime").timezone.utc),
    )


def test_process_paper_falls_back_when_command_is_unavailable(tmp_path: Path) -> None:
    orchestrator = LlmOrchestrator(
        command_prefix=("definitely-missing-re-ass-command", "-p"),
        timeout_seconds=30,
        enabled=True,
        allow_local_paper_note_fallback=True,
    )

    processed = orchestrator.process_paper(make_paper(), tmp_path)

    assert processed.note_path.exists()
    assert "## Abstract" in processed.note_path.read_text(encoding="utf-8")
    assert processed.micro_summary.startswith("This paper studies tool-using agents.")


def test_process_paper_uses_llm_output_when_command_succeeds(tmp_path: Path) -> None:
    seen_prompts: list[str] = []

    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        prompt = str(command[-1])
        seen_prompts.append(prompt)
        if prompt.startswith("Use /summarise-paper skill"):
            note_path = tmp_path / "LLM Generated Note.md"
            note_path.write_text("# LLM Generated Note\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
        if prompt.startswith("Summarise the following arXiv abstract"):
            return subprocess.CompletedProcess(command, 0, stdout="Short LLM summary.", stderr="")
        raise AssertionError(f"Unexpected prompt: {prompt}")

    orchestrator = LlmOrchestrator(
        command_prefix=("echo",),
        timeout_seconds=30,
        enabled=True,
        allow_local_paper_note_fallback=True,
        runner=fake_runner,
    )

    processed = orchestrator.process_paper(make_paper(), tmp_path)

    assert processed.note_name == "LLM Generated Note"
    assert processed.micro_summary == "Short LLM summary."
    assert any(prompt.startswith("Use /summarise-paper skill") for prompt in seen_prompts)


def test_process_paper_logs_when_llm_returns_without_creating_note(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        prompt = str(command[-1])
        if prompt.startswith("Use /summarise-paper skill"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="I need permission to fetch web content before I can summarise the paper.",
                stderr="",
            )
        if prompt.startswith("Summarise the following arXiv abstract"):
            return subprocess.CompletedProcess(command, 0, stdout="Short LLM summary.", stderr="")
        raise AssertionError(f"Unexpected prompt: {prompt}")

    orchestrator = LlmOrchestrator(
        command_prefix=("echo",),
        timeout_seconds=30,
        enabled=True,
        allow_local_paper_note_fallback=True,
        runner=fake_runner,
    )

    processed = orchestrator.process_paper(make_paper(), tmp_path)

    assert processed.note_path.exists()
    assert "without creating a note" in caplog.text
    assert "permission to fetch web content" in caplog.text
