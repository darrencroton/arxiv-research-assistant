from pathlib import Path

import pytest

from re_ass.settings import load_config


def test_load_config_parses_explicit_llm_provider_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "re_ass.toml"
    config_path.write_text(
        "[output]\nroot = 'output'\n\n"
        "[arxiv]\ndefault_categories = ['astro-ph.CO']\n\n"
        "[llm]\n"
        "enabled = true\n"
        "mode = 'api'\n"
        "provider = 'openai'\n"
        "model = 'gpt-5.2'\n"
        "timeout_seconds = 1200\n"
        "max_output_tokens = 4096\n"
        "temperature = 0.1\n"
        "retry_attempts = 4\n"
        "allow_local_paper_note_fallback = false\n"
        "prompt_debug_file = 'archive/prompts/last.txt'\n"
        "download_timeout_seconds = 45\n"
        "max_pdf_size_mb = 20\n"
        "marker_timeout_seconds = 180\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.llm.enabled is True
    assert config.llm.mode == "api"
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-5.2"
    assert config.llm.prompt_debug_file == (tmp_path / "archive" / "prompts" / "last.txt").resolve()


def test_load_config_uses_new_runtime_sections() -> None:
    config = load_config(project_root=Path("/tmp/re-ass-test"))

    assert config.output_root == Path("/tmp/re-ass-test/output").resolve()
    assert config.processed_root == Path("/tmp/re-ass-test/processed").resolve()
    assert config.state_root == Path("/tmp/re-ass-test/state").resolve()
    assert config.logs_root == Path("/tmp/re-ass-test/logs").resolve()
    assert config.daily_template == Path("/tmp/re-ass-test/templates/daily-note-template.md").resolve()
    assert config.weekly_template == Path("/tmp/re-ass-test/templates/weekly-note-template.md").resolve()
    assert config.llm.prompt_debug_file == Path("/tmp/re-ass-test/tmp/paper_summariser/prompt.txt").resolve()


def test_load_config_supports_markdown_links_and_rotation_day(tmp_path: Path) -> None:
    config_path = tmp_path / "re_ass.toml"
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
    config_path = tmp_path / "re_ass.toml"
    config_path.write_text(
        "[notes]\nlink_style = 'html'\n\n"
        "[arxiv]\ndefault_categories = ['astro-ph.CO']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="notes.link_style"):
        load_config(config_path)


def test_load_config_rejects_invalid_rotation_day(tmp_path: Path) -> None:
    config_path = tmp_path / "re_ass.toml"
    config_path.write_text(
        "[notes]\nrotation_day = 'funday'\n\n"
        "[arxiv]\ndefault_categories = ['astro-ph.CO']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="notes.rotation_day"):
        load_config(config_path)
