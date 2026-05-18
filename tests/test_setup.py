from pathlib import Path

import pytest

from re_ass.setup import prepare_workspace


DEFAULT_SETTINGS = (
    "[notes]\n"
    'daily_top_paper_heading = "## TODAY\'S TOP PAPER"\n'
    'weekly_synthesis_heading = "## SYNTHESIS"\n'
    'weekly_additions_heading = "## DAILY ADDITIONS"\n'
    "\n"
    "[llm]\n"
    'mode = "cli"\n'
    'provider = "claude"\n'
)

DEFAULT_PREFERENCES = (
    "# Arxiv Priorities\n\n"
    "## Categories\n"
    "- astro-ph.CO\n\n"
    "## Priorities\n"
    "1. Example priority\n"
)


def _seed_defaults(tmp_path: Path) -> None:
    defaults_dir = tmp_path / "user_preferences" / "defaults"
    defaults_dir.mkdir(parents=True)
    (defaults_dir / "settings.toml").write_text(DEFAULT_SETTINGS, encoding="utf-8")
    (defaults_dir / "preferences.md").write_text(DEFAULT_PREFERENCES, encoding="utf-8")


def test_prepare_workspace_warns_instead_of_failing_for_fresh_default_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_defaults(tmp_path)

    def failing_generation_service(*, config, tag_categories) -> None:
        raise ValueError(f"{config.provider} not configured")

    monkeypatch.setattr("re_ass.setup.GenerationService", failing_generation_service)

    summary = prepare_workspace(tmp_path)

    assert sorted(path.name for path in summary.created_user_files) == ["preferences.md", "settings.toml"]
    assert summary.provider_validated is False
    assert summary.provider_warning is not None
    assert "not treated as fatal" in summary.provider_warning
    assert "cli/claude" in summary.provider_warning

    for relative_path in (
        "output/summaries",
        "output/daily-notes",
        "output/weekly-notes",
        "output/pdfs",
        "state/papers",
        "state/runs",
        "logs",
        "logs/debug",
        "logs/launchd",
    ):
        assert (tmp_path / relative_path).exists()


def test_prepare_workspace_still_fails_for_existing_local_provider_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_defaults(tmp_path)
    user_preferences_dir = tmp_path / "user_preferences"
    user_preferences_dir.mkdir(exist_ok=True)
    (user_preferences_dir / "settings.toml").write_text(DEFAULT_SETTINGS, encoding="utf-8")

    def failing_generation_service(*, config, tag_categories) -> None:
        raise ValueError(f"{config.provider} login missing")

    monkeypatch.setattr("re_ass.setup.GenerationService", failing_generation_service)

    with pytest.raises(ValueError, match=r"LLM provider validation failed for summary \(cli/claude\): claude login missing"):
        prepare_workspace(tmp_path)


def test_prepare_workspace_reports_ranking_provider_validation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_defaults(tmp_path)
    user_preferences_dir = tmp_path / "user_preferences"
    user_preferences_dir.mkdir(exist_ok=True)
    (user_preferences_dir / "settings.toml").write_text(
        DEFAULT_SETTINGS
        + "\n"
        + "[llm-ranking]\n"
        + 'provider = "copilot"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr("re_ass.setup.GenerationService", lambda **_kwargs: object())
    monkeypatch.setattr("re_ass.setup.make_provider", lambda _config: (_ for _ in ()).throw(ValueError("copilot login missing")))

    with pytest.raises(ValueError, match=r"LLM provider validation failed for ranking \(cli/copilot\): copilot login missing"):
        prepare_workspace(tmp_path)


def test_prepare_workspace_validates_provider_when_local_config_is_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_defaults(tmp_path)

    def passing_generation_service(*, config, tag_categories) -> object:
        assert tag_categories == ("astro-ph.CO",)
        return object()

    monkeypatch.setattr("re_ass.setup.GenerationService", passing_generation_service)

    summary = prepare_workspace(tmp_path)

    assert summary.provider_validated is True
    assert summary.provider_warning is None
    assert summary.llm_mode == "cli"
    assert summary.llm_provider == "claude"
