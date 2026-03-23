from pathlib import Path

import pytest

from re_ass.settings import load_config


def test_load_config_parses_explicit_llm_provider_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[output]\nroot = 'output'\n\n"
        "[arxiv]\n"
        "default_categories = ['astro-ph.CO']\n"
        "page_size = 75\n"
        "retrieval_pool_size = 12\n"
        "final_pool_size = 7\n"
        "min_selection_score = 82.5\n"
        "passthrough_candidate_count = 40\n\n"
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
    assert config.ranking_shortlist_size == 12
    assert config.ranking_final_pool_size == 7
    assert config.ranking_min_selection_score == 82.5
    assert config.ranking_passthrough_candidate_count == 40
    assert config.llm.prompt_debug_file == (tmp_path / "archive" / "prompts" / "last.txt").resolve()


def test_load_config_parses_llm_effort_for_cli_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[arxiv]\n"
        "default_categories = ['astro-ph.CO']\n\n"
        "[llm]\n"
        "mode = 'cli'\n"
        "provider = 'copilot'\n"
        "effort = 'high'\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.llm.effort == "high"
    assert config.llm.provider_config()["effort"] == "high"


def test_load_config_uses_new_runtime_sections() -> None:
    config = load_config(project_root=Path("/tmp/re-ass-test"))

    assert config.output_root == Path("/tmp/re-ass-test/output").resolve()
    assert config.summaries_dir == Path("/tmp/re-ass-test/output/summaries").resolve()
    assert config.daily_notes_dir == Path("/tmp/re-ass-test/output/daily-notes").resolve()
    assert config.weekly_notes_dir == Path("/tmp/re-ass-test/output/weekly-notes").resolve()
    assert config.pdfs_dir == Path("/tmp/re-ass-test/output/pdfs").resolve()
    assert config.state_root == Path("/tmp/re-ass-test/state").resolve()
    assert config.logs_root == Path("/tmp/re-ass-test/logs").resolve()
    assert config.daily_template == Path("/tmp/re-ass-test/user_preferences/templates/daily-note-template.md").resolve()
    assert config.weekly_template == Path("/tmp/re-ass-test/user_preferences/templates/weekly-note-template.md").resolve()
    assert config.preferences_file == Path("/tmp/re-ass-test/user_preferences/preferences.md").resolve()
    assert config.arxiv_page_size == 100
    assert config.ranking_shortlist_size == 24
    assert config.ranking_final_pool_size == 24
    assert config.ranking_min_selection_score == 75.0
    assert config.ranking_passthrough_candidate_count == 50
    assert config.llm.effort is None
    assert config.llm.prompt_debug_file == Path("/tmp/re-ass-test/tmp/paper_summariser/prompt.txt").resolve()


def test_load_config_treats_blank_llm_effort_as_unset(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[arxiv]\n"
        "default_categories = ['astro-ph.CO']\n\n"
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
        "[arxiv]\n"
        "default_categories = ['astro-ph.CO']\n"
        "max_results = 80\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.arxiv_page_size == 80


def test_load_config_supports_legacy_shortlist_size_key(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[arxiv]\n"
        "default_categories = ['astro-ph.CO']\n"
        "shortlist_size = 11\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.ranking_shortlist_size == 11


def test_load_config_supports_markdown_links_and_rotation_day(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\n"
        "link_style = 'markdown'\n"
        "rotation_day = 'sunday'\n\n"
        "[arxiv]\ndefault_categories = ['astro-ph.CO']\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.link_style == "markdown"
    assert config.rotation_day == "sunday"


def test_load_config_rejects_invalid_link_style(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\nlink_style = 'html'\n\n"
        "[arxiv]\ndefault_categories = ['astro-ph.CO']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="notes.link_style"):
        load_config(config_path)


def test_load_config_rejects_invalid_rotation_day(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[notes]\nrotation_day = 'funday'\n\n"
        "[arxiv]\ndefault_categories = ['astro-ph.CO']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="notes.rotation_day"):
        load_config(config_path)


def test_load_config_rejects_invalid_llm_effort(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.toml"
    config_path.write_text(
        "[arxiv]\n"
        "default_categories = ['astro-ph.CO']\n\n"
        "[llm]\n"
        "effort = 'xhigh'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="llm.effort"):
        load_config(config_path)
