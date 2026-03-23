from pathlib import Path

from re_ass.bootstrap import ensure_user_preferences


def test_ensure_user_preferences_copies_missing_files_from_defaults(tmp_path: Path) -> None:
    defaults_dir = tmp_path / "user_preferences" / "defaults"
    defaults_dir.mkdir(parents=True)
    for filename in ("settings.toml", "preferences.md"):
        (defaults_dir / filename).write_text(f"default for {filename}\n", encoding="utf-8")

    created = ensure_user_preferences(tmp_path)

    assert sorted(path.name for path in created) == ["preferences.md", "settings.toml"]
    for path in created:
        assert path.parent == tmp_path / "user_preferences"
        assert path.exists()


def test_ensure_user_preferences_preserves_existing_user_files(tmp_path: Path) -> None:
    defaults_dir = tmp_path / "user_preferences" / "defaults"
    defaults_dir.mkdir(parents=True)
    for filename in ("settings.toml", "preferences.md"):
        (defaults_dir / filename).write_text(f"default for {filename}\n", encoding="utf-8")

    user_preferences_dir = tmp_path / "user_preferences"
    user_preferences_dir.mkdir(exist_ok=True)
    existing = user_preferences_dir / "preferences.md"
    existing.write_text("my edits\n", encoding="utf-8")

    created = ensure_user_preferences(tmp_path)

    assert existing.read_text(encoding="utf-8") == "my edits\n"
    assert sorted(path.name for path in created) == ["settings.toml"]
