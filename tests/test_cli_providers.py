import subprocess
import logging

import pytest

from re_ass.paper_summariser.providers.cli import ClaudeCLI, CodexCLI, CopilotCLI, GeminiCLI, OpencodeCLI


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


def test_claude_cli_builds_command_with_effort(
    cli_on_path: None,
) -> None:
    provider = ClaudeCLI({"timeout": 30, "model": "claude-sonnet-4-6", "effort": "high"})

    assert provider._build_command("prompt") == [
        "claude",
        "--output-format",
        "text",
        "--model",
        "claude-sonnet-4-6",
        "--effort",
        "high",
        "-p",
        "prompt",
    ]


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


def test_codex_cli_builds_command_with_effort(
    cli_on_path: None,
) -> None:
    provider = CodexCLI({"timeout": 30, "model": "gpt-5.4", "effort": "medium"})

    assert provider._build_command("prompt") == [
        "codex",
        "exec",
        "-c",
        'model="gpt-5.4"',
        "-c",
        'model_reasoning_effort="medium"',
        "-",
    ]


def test_gemini_cli_accepts_api_key(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    provider = GeminiCLI({"timeout": 30})

    provider.validate_runtime_ready()


def test_gemini_cli_ignores_effort_and_warns_once(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    with caplog.at_level(logging.WARNING):
        provider = GeminiCLI({"timeout": 30, "effort": "high"})

    assert provider._build_command("prompt") == ["gemini", "-o", "text", "-p", "prompt"]
    warning_messages = [record.message for record in caplog.records]
    assert warning_messages == ["[WARNING] Gemini CLI ignores llm.effort; using Gemini defaults."]


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


def test_copilot_cli_builds_command_with_effort(
    cli_on_path: None,
) -> None:
    provider = CopilotCLI({"timeout": 30, "model": "gpt-5.2", "effort": "low"})

    assert provider._build_command("prompt") == [
        "copilot",
        "--allow-all-tools",
        "--output-format",
        "text",
        "--silent",
        "--model",
        "gpt-5.2",
        "--effort",
        "low",
        "-p",
        "prompt",
    ]


def test_opencode_cli_accepts_default_available_model(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="opencode/gpt-5-nano\n",
            stderr="",
        ),
    )
    provider = OpencodeCLI({"timeout": 30})

    provider.validate_runtime_ready()


def test_opencode_cli_accepts_configured_local_model(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    def fake_run(*args, **kwargs):
        assert args[0] == ["opencode", "models", "ollama"]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="ollama/qwen2.5:14b\n",
            stderr="",
        )

    monkeypatch.setattr("re_ass.paper_summariser.providers.cli.subprocess.run", fake_run)
    provider = OpencodeCLI({"timeout": 30, "model": "ollama/qwen2.5:14b"})

    provider.validate_runtime_ready()


def test_opencode_cli_accepts_cloud_model_from_environment_key(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_run(*args, **kwargs):
        assert args[0] == ["opencode", "models", "openai"]
        assert kwargs["env"]["OPENAI_API_KEY"] == "test-key"
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="openai/gpt-5.2\n",
            stderr="",
        )

    monkeypatch.setattr("re_ass.paper_summariser.providers.cli.subprocess.run", fake_run)
    provider = OpencodeCLI({"timeout": 30, "model": "openai/gpt-5.2"})

    provider.validate_runtime_ready()


def test_opencode_cli_requires_available_models_when_no_model_is_set(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="",
            stderr="",
        ),
    )
    provider = OpencodeCLI({"timeout": 30})

    with pytest.raises(ValueError, match=r"opencode models"):
        provider.validate_runtime_ready()


def test_opencode_cli_requires_provider_qualified_model_name(
    cli_on_path: None,
) -> None:
    provider = OpencodeCLI({"timeout": 30, "model": "gpt-5.2"})

    with pytest.raises(ValueError, match=r"provider/model"):
        provider.validate_runtime_ready()


def test_opencode_cli_requires_available_provider(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr="Provider not found: anthropic",
        ),
    )
    provider = OpencodeCLI({"timeout": 30, "model": "anthropic/claude-sonnet-4-6"})

    with pytest.raises(ValueError, match=r"provider 'anthropic' is not available"):
        provider.validate_runtime_ready()


def test_opencode_cli_requires_configured_model_to_be_listed(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    monkeypatch.setattr(
        "re_ass.paper_summariser.providers.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="openai/gpt-5.2\n",
            stderr="",
        ),
    )
    provider = OpencodeCLI({"timeout": 30, "model": "openai/not-a-model"})

    with pytest.raises(ValueError, match=r"model 'openai/not-a-model' is not available"):
        provider.validate_runtime_ready()


_OPENCODE_BASE_CMD = ["opencode", "run", "--pure", "--format", "json"]


def test_opencode_cli_builds_command_without_model(
    cli_on_path: None,
) -> None:
    provider = OpencodeCLI({"timeout": 30})

    assert provider._build_command("prompt") == [*_OPENCODE_BASE_CMD, "prompt"]


def test_opencode_cli_builds_command_with_model(
    cli_on_path: None,
) -> None:
    provider = OpencodeCLI({"timeout": 30, "model": "ollama/qwen2.5:14b"})

    assert provider._build_command("prompt") == [
        *_OPENCODE_BASE_CMD,
        "--model",
        "ollama/qwen2.5:14b",
        "prompt",
    ]


def test_opencode_cli_builds_command_with_variant(
    cli_on_path: None,
) -> None:
    provider = OpencodeCLI({"timeout": 30, "effort": "high"})

    assert provider._build_command("prompt") == [
        *_OPENCODE_BASE_CMD,
        "--variant",
        "high",
        "prompt",
    ]


def test_opencode_cli_denies_tools_via_environment(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    captured_env = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr="")

    monkeypatch.setattr("re_ass.paper_summariser.providers.cli.subprocess.run", fake_run)
    provider = OpencodeCLI({"timeout": 30})

    provider._run_command(["opencode", "run"])

    assert captured_env["OPENCODE_PERMISSION"] == '"deny"'
    assert captured_env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] == "true"


def test_opencode_cli_process_document_extracts_text_from_json_events(
    monkeypatch: pytest.MonkeyPatch,
    cli_on_path: None,
) -> None:
    ndjson = "\n".join([
        '{"type":"step_start","timestamp":1,"sessionID":"s1","part":{}}',
        '{"type":"text","timestamp":2,"sessionID":"s1","part":{"text":"Hello "}}',
        '{"type":"tool_use","timestamp":3,"sessionID":"s1","part":{}}',
        '{"type":"text","timestamp":4,"sessionID":"s1","part":{"text":"world."}}',
        '{"type":"step_finish","timestamp":5,"sessionID":"s1","part":{}}',
    ])
    provider = OpencodeCLI({"timeout": 30})
    monkeypatch.setattr(
        provider,
        "_run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["opencode"], 0, stdout=ndjson, stderr=""
        ),
    )

    result = provider.process_document("", False, "system", "user")

    assert result == "Hello world."


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
