"""Tests for the preferences module."""

from pathlib import Path

import pytest

from re_ass.preferences import load_preferences


def test_load_preferences_parses_categories_and_priorities(tmp_path: Path) -> None:
    preferences_file = tmp_path / "preferences.md"
    preferences_file.write_text(
        "# Arxiv Priorities\n\n"
        "## Categories\n"
        "- cs.AI\n"
        "- cs.CL\n\n"
        "## Priorities\n"
        "1. Agents\n"
        "2. RAG\n",
        encoding="utf-8",
    )

    preferences = load_preferences(preferences_file, ("cs.LG",))

    assert preferences.categories == ("cs.AI", "cs.CL")
    assert preferences.priorities == ("Agents", "RAG")
    assert preferences.top_papers == 3
    assert "## Priorities" in preferences.raw_text


def test_load_preferences_uses_default_categories_when_missing(tmp_path: Path) -> None:
    preferences_file = tmp_path / "preferences.md"
    preferences_file.write_text(
        "# Arxiv Priorities\n"
        "1. Agents\n"
        "2. Tool Use\n",
        encoding="utf-8",
    )

    preferences = load_preferences(preferences_file, ("cs.AI", "cs.CL"))

    assert preferences.categories == ("cs.AI", "cs.CL")
    assert preferences.priorities == ("Agents", "Tool Use")
    assert preferences.top_papers == 3


def test_load_preferences_parses_top_papers_setting(tmp_path: Path) -> None:
    preferences_file = tmp_path / "preferences.md"
    preferences_file.write_text(
        "# Arxiv Priorities\n\n"
        "## Categories\n"
        "- cs.AI\n\n"
        "## Output\n"
        "- Top papers: 5\n\n"
        "## Priorities\n"
        "1. Agents\n",
        encoding="utf-8",
    )

    preferences = load_preferences(preferences_file, ("cs.LG",))

    assert preferences.top_papers == 5


def test_load_preferences_rejects_top_papers_above_maximum(tmp_path: Path) -> None:
    preferences_file = tmp_path / "preferences.md"
    preferences_file.write_text(
        "# Arxiv Priorities\n\n"
        "## Output\n"
        "- Top papers: 11\n\n"
        "## Priorities\n"
        "1. Agents\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="between 1 and 10"):
        load_preferences(preferences_file, ("cs.AI",))


def test_load_preferences_requires_priorities(tmp_path: Path) -> None:
    preferences_file = tmp_path / "preferences.md"
    preferences_file.write_text("# Empty\n\n## Categories\n- cs.AI\n", encoding="utf-8")

    with pytest.raises(ValueError, match="No priorities found"):
        load_preferences(preferences_file, ("cs.AI",))
