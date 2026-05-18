"""Workspace setup helpers for first-run bootstrap and provider validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from re_ass.bootstrap import default_project_root, ensure_user_preferences
from re_ass.generation_service import GenerationService, make_provider
from re_ass.preferences import load_preferences
from re_ass.settings import LlmConfig, load_config


_RUNTIME_DIRECTORIES = (
    Path("output/summaries"),
    Path("output/daily-notes"),
    Path("output/weekly-notes"),
    Path("output/pdfs"),
    Path("state/papers"),
    Path("state/runs"),
    Path("logs"),
    Path("logs/debug"),
    Path("logs/launchd"),
)


@dataclass(frozen=True, slots=True)
class SetupSummary:
    """Outcome of preparing a local re-ass workspace."""

    created_user_files: tuple[Path, ...]
    provider_validated: bool
    provider_warning: str | None
    llm_mode: str
    llm_provider: str


def _validate_provider_role(
    *,
    role: str,
    config: LlmConfig,
    tag_categories: tuple[str, ...],
) -> None:
    try:
        if role == "summary":
            GenerationService(config=config, tag_categories=tag_categories)
        else:
            make_provider(config)
    except Exception as error:
        raise ValueError(
            f"LLM provider validation failed for {role} ({config.mode}/{config.provider}): {error}"
        ) from error


def ensure_runtime_directories(project_root: Path | None = None) -> tuple[Path, ...]:
    """Create runtime directories under the project root."""
    root = (project_root or default_project_root()).resolve()
    created: list[Path] = []
    for relative_path in _RUNTIME_DIRECTORIES:
        target = root / relative_path
        if not target.exists():
            created.append(target)
        target.mkdir(parents=True, exist_ok=True)
    return tuple(created)


def prepare_workspace(project_root: Path | None = None) -> SetupSummary:
    """Bootstrap local files and validate the configured provider when appropriate."""
    root = (project_root or default_project_root()).resolve()
    ensure_runtime_directories(root)

    created_user_files = tuple(ensure_user_preferences(root))
    config = load_config(project_root=root)
    preferences = load_preferences(config.preferences_file)

    settings_bootstrapped = any(path.name == "settings.toml" for path in created_user_files)
    try:
        _validate_provider_role(
            role="summary",
            config=config.summary_llm,
            tag_categories=preferences.categories,
        )
        if config.ranking_llm is not config.summary_llm:
            _validate_provider_role(
                role="ranking",
                config=config.ranking_llm,
                tag_categories=preferences.categories,
            )
    except Exception as error:
        message = str(error)
        if not settings_bootstrapped:
            raise ValueError(message) from error

        warning = (
            f"{message}\n"
            "Setup bootstrapped the default local configuration, so this check was not treated as fatal.\n"
            "Edit user_preferences/settings.toml to choose a provider you have configured, "
            "then rerun ./scripts/setup.sh or run `uv run re-ass`."
        )
        return SetupSummary(
            created_user_files=created_user_files,
            provider_validated=False,
            provider_warning=warning,
            llm_mode=config.summary_llm.mode,
            llm_provider=config.summary_llm.provider,
        )

    return SetupSummary(
        created_user_files=created_user_files,
        provider_validated=True,
        provider_warning=None,
        llm_mode=config.summary_llm.mode,
        llm_provider=config.summary_llm.provider,
    )


def main() -> int:
    """Run the workspace bootstrap CLI."""
    try:
        summary = prepare_workspace()
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1

    for path in summary.created_user_files:
        print(f"Bootstrapped user preference file: {path}")

    if summary.provider_validated:
        print(f"Validated LLM provider prerequisites: {summary.llm_mode}/{summary.llm_provider}")
    elif summary.provider_warning:
        print(summary.provider_warning, file=sys.stderr)

    print("Setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
