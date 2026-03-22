import subprocess

import pytest

from re_ass.paper_summariser.providers.cli import ClaudeCLI, CodexCLI, CopilotCLI, GeminiCLI


@pytest.fixture
def cli_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.shutil.which",
        lambda command: f"/usr/bin/{command}",
    )


def test_claude_cli_readiness_requires_login(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr="Not logged in",
        ),
    )

    provider = ClaudeCLI({"timeout": 30})

    with pytest.raises(ValueError, match=r"claude auth login"):
        provider.validate_runtime_ready()


def test_claude_cli_process_document_adds_auth_hint(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    provider = ClaudeCLI({"timeout": 30})
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["claude"],
            1,
            stdout="",
            stderr="Not logged in · Please run /login",
        ),
    )

    with pytest.raises(RuntimeError, match=r"claude auth login"):
        provider.process_document("", False, "system", "user")


def test_codex_cli_readiness_requires_login(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="Not logged in",
            stderr="",
        ),
    )

    provider = CodexCLI({"timeout": 30})

    with pytest.raises(ValueError, match=r"codex login"):
        provider.validate_runtime_ready()


def test_gemini_cli_accepts_api_key(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    provider = GeminiCLI({"timeout": 30})

    provider.validate_runtime_ready()


def test_gemini_cli_requires_supported_automation_credentials(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.shutil.which",
        lambda command: "/usr/bin/gemini" if command == "gemini" else None,
    )

    provider = GeminiCLI({"timeout": 30})

    with pytest.raises(ValueError, match=r"GEMINI_API_KEY"):
        provider.validate_runtime_ready()


def test_copilot_cli_accepts_token_env_var(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "test-token")

    provider = CopilotCLI({"timeout": 30})

    provider.validate_runtime_ready()


def test_copilot_cli_requires_login_or_token(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.shutil.which",
        lambda command: "/usr/bin/copilot" if command == "copilot" else None,
    )
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr=""),
    )

    provider = CopilotCLI({"timeout": 30})

    with pytest.raises(ValueError, match=r"copilot login"):
        provider.validate_runtime_ready()
