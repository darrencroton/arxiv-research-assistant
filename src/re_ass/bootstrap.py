"""Bootstrap helpers for repo-local user preference files."""

from __future__ import annotations

from pathlib import Path
import shutil


USER_PREFERENCES_DIRNAME = "user_preferences"
DEFAULTS_DIRNAME = "defaults"
USER_PREFERENCE_FILENAMES = (
    "settings.toml",
    "preferences.md",
)


def default_project_root() -> Path:
    """Return the repository root for the installed package."""
    return Path(__file__).resolve().parents[2]


def user_preferences_dir(project_root: Path | None = None) -> Path:
    """Return the mutable user preferences directory."""
    root = (project_root or default_project_root()).resolve()
    return root / USER_PREFERENCES_DIRNAME


def user_preferences_defaults_dir(project_root: Path | None = None) -> Path:
    """Return the tracked defaults directory used for first-run copies."""
    return user_preferences_dir(project_root) / DEFAULTS_DIRNAME


def default_config_path(project_root: Path | None = None) -> Path:
    """Return the default mutable settings path."""
    return user_preferences_dir(project_root) / "settings.toml"


def ensure_user_preferences(project_root: Path | None = None) -> list[Path]:
    """Copy tracked defaults into the mutable user directory when missing."""
    root = (project_root or default_project_root()).resolve()
    defaults_dir = user_preferences_defaults_dir(root)
    user_dir = user_preferences_dir(root)

    user_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    for filename in USER_PREFERENCE_FILENAMES:
        source = defaults_dir / filename
        target = user_dir / filename
        if not source.exists():
            raise FileNotFoundError(f"Missing tracked user-preference default: {source}")
        if target.exists():
            continue
        shutil.copyfile(source, target)
        created.append(target)

    return created
