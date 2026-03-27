from pathlib import Path

import pytest

from re_ass.settings import load_config


DEFAULT_HEADINGS = (
    "[notes]\n"
    'daily_top_paper_heading = "## TODAY\'S TOP PAPER"\n'
    'weekly_synthesis_heading = "## SYNTHESIS"\n'
    'weekly_additions_heading = "## DAILY ADDITIONS"\n'
)


def test_load_config_parses_explicit_llm_provider_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[output]\nroot = 'output'\n\n"
        f"{DEFAULT_HEADINGS}\n"
        "[arxiv]\n"
        "page_size = 75\n"
        "always_summarize_score = 91.5\n"
        "min_selection_score = 82.5\n"
        "\n"
        "[llm]\n"
        "mode = 'api'\n"
        "provider = 'openai'\n"
        "model = 'gpt-5.2'\n"
        "timeout_seconds = 1200\n"
        "max_output_tokens = 4096\n"
        "temperature = 0.1\n"
        "retry_attempts = 4\n"
        "prompt_debug_file = 'archive/prompts/last.txt'\n"
        "download_timeout_seconds = 45\n"
        "max_pdf_size_mb = 20\n"
        "marker_timeout_seconds = 180\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.llm.mode == "api"
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-5.2"
    assert config.arxiv_page_size == 75
    assert config.always_summarize_score == 91.5
    assert config.min_selection_score == 82.5
    assert config.llm.prompt_debug_file == (tmp_path / "archive" / "prompts" / "last.txt").resolve()


def test_load_config_parses_llm_effort_for_cli_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        f"{DEFAULT_HEADINGS}\n"
        "[llm]\n"
        "mode = 'cli'\n"
        "provider = 'copilot'\n"
        "effort = 'high'\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.llm.effort == "high"
    assert config.llm.provider_config()["effort"] == "high"


def test_load_config_uses_new_runtime_sections(tmp_path: Path) -> None:
    settings_dir = tmp_path / "user_preferences"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.toml").write_text(DEFAULT_HEADINGS, encoding="utf-8")

    config = load_config(project_root=tmp_path)

    assert config.output_root == (tmp_path / "output").resolve()
    assert config.summaries_dir == (tmp_path / "output" / "summaries").resolve()
    assert config.daily_notes_dir == (tmp_path / "output" / "daily-notes").resolve()
    assert config.weekly_notes_dir == (tmp_path / "output" / "weekly-notes").resolve()
    assert config.pdfs_dir == (tmp_path / "output" / "pdfs").resolve()
    assert config.state_root == (tmp_path / "state").resolve()
    assert config.logs_root == (tmp_path / "logs").resolve()
    assert config.daily_template == (tmp_path / "user_preferences" / "templates" / "daily-note-template.md").resolve()
    assert config.weekly_template == (tmp_path / "user_preferences" / "templates" / "weekly-note-template.md").resolve()
    assert config.preferences_file == (tmp_path / "user_preferences" / "preferences.md").resolve()
    assert config.arxiv_page_size == 100
    assert config.always_summarize_score == 90.0
    assert config.min_selection_score == 70.0
    assert config.daily_top_paper_heading == "## TODAY'S TOP PAPER"
    assert config.weekly_synthesis_heading == "## SYNTHESIS"
    assert config.weekly_additions_heading == "## DAILY ADDITIONS"
    assert config.weekly_synthesis_word_limit_start == 100
    assert config.weekly_synthesis_word_limit_end == 200
    assert config.llm.effort is None
    assert config.llm.prompt_debug_file == (tmp_path / "tmp" / "paper_summariser" / "prompt.txt").resolve()


def test_load_config_treats_blank_llm_effort_as_unset(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        f"{DEFAULT_HEADINGS}\n"
        "[llm]\n"
        "mode = 'cli'\n"
        "provider = 'claude'\n"
        "effort = '  '\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.llm.effort is None
    assert "effort" not in config.llm.provider_config()


def test_load_config_supports_legacy_arxiv_max_results_key(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        f"{DEFAULT_HEADINGS}\n"
        "[arxiv]\n"
        "max_results = 80\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.arxiv_page_size == 80


def test_load_config_supports_markdown_links_rotation_day_and_managed_headings(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\n"
        "link_style = 'markdown'\n"
        "rotation_day = 'sunday'\n"
        "daily_top_paper_heading = '## Featured Paper'\n"
        "weekly_synthesis_heading = '## Weekly Summary'\n"
        "weekly_additions_heading = '## Added This Week'\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.link_style == "markdown"
    assert config.rotation_day == "sunday"
    assert config.daily_top_paper_heading == "## Featured Paper"
    assert config.weekly_synthesis_heading == "## Weekly Summary"
    assert config.weekly_additions_heading == "## Added This Week"


def test_load_config_supports_weekly_synthesis_word_limit_range(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\n"
        'daily_top_paper_heading = "## TODAY\'S TOP PAPER"\n'
        'weekly_synthesis_heading = "## SYNTHESIS"\n'
        'weekly_additions_heading = "## DAILY ADDITIONS"\n'
        "weekly_synthesis_word_limit_start = 110\n"
        "weekly_synthesis_word_limit_end = 190\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.weekly_synthesis_word_limit_start == 110
    assert config.weekly_synthesis_word_limit_end == 190


def test_load_config_requires_managed_heading_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text("[notes]\nweekly_synthesis_heading = '## SYNTHESIS'\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[notes\]\.daily_top_paper_heading"):
        load_config(config_path)


def test_load_config_rejects_blank_managed_heading(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\n"
        'daily_top_paper_heading = "## TODAY\'S TOP PAPER"\n'
        'weekly_synthesis_heading = ""\n'
        'weekly_additions_heading = "## DAILY ADDITIONS"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"\[notes\]\.weekly_synthesis_heading"):
        load_config(config_path)


def test_load_config_rejects_invalid_link_style(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\n"
        "link_style = 'html'\n"
        'daily_top_paper_heading = "## TODAY\'S TOP PAPER"\n'
        'weekly_synthesis_heading = "## SYNTHESIS"\n'
        'weekly_additions_heading = "## DAILY ADDITIONS"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="notes.link_style"):
        load_config(config_path)


def test_load_config_rejects_invalid_rotation_day(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\n"
        "rotation_day = 'funday'\n"
        'daily_top_paper_heading = "## TODAY\'S TOP PAPER"\n'
        'weekly_synthesis_heading = "## SYNTHESIS"\n'
        'weekly_additions_heading = "## DAILY ADDITIONS"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="notes.rotation_day"):
        load_config(config_path)


def test_load_config_rejects_descending_weekly_synthesis_word_limit_range(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\n"
        'daily_top_paper_heading = "## TODAY\'S TOP PAPER"\n'
        'weekly_synthesis_heading = "## SYNTHESIS"\n'
        'weekly_additions_heading = "## DAILY ADDITIONS"\n'
        "weekly_synthesis_word_limit_start = 200\n"
        "weekly_synthesis_word_limit_end = 100\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="weekly_synthesis_word_limit_end"):
        load_config(config_path)


def test_load_config_rejects_invalid_llm_effort(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        f"{DEFAULT_HEADINGS}\n"
        "[llm]\n"
        "effort = 'xhigh'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="llm.effort"):
        load_config(config_path)


def test_load_config_rejects_always_summarize_score_below_interest_threshold(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        f"{DEFAULT_HEADINGS}\n"
        "[arxiv]\n"
        "always_summarize_score = 65\n"
        "min_selection_score = 70\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="always_summarize_score"):
        load_config(config_path)


def test_load_config_requires_existing_settings_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Settings file not found"):
        load_config(project_root=tmp_path)
