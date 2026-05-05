import pytest

from re_ass.paper_summariser.providers import create_provider, get_supported_provider_names


def test_create_provider_builds_cli_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")

    provider = create_provider("cli", "codex", config={"model": "gpt-5.4", "timeout": 42})

    assert provider.mode == "cli"
    assert provider.provider_name == "codex"
    assert provider.model == "gpt-5.4"


def test_supported_cli_providers_include_opencode() -> None:
    assert "opencode" in get_supported_provider_names("cli")


def test_create_provider_rejects_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_provider("api", "openai", config={"model": "gpt-5.2"})


def test_create_provider_rejects_missing_cli_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _command: None)

    with pytest.raises(ValueError, match="not found on PATH"):
        create_provider("cli", "codex", config={"model": "gpt-5.4"})
