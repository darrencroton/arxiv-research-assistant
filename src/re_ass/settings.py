"""Configuration loading and validation for re-ass."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from re_ass.bootstrap import default_config_path


_VALID_LINK_STYLES = ("wikilink", "markdown")
_VALID_ROTATION_DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """LLM provider and summarisation settings."""

    mode: str
    provider: str
    model: str | None
    timeout_seconds: int
    max_output_tokens: int
    temperature: float
    retry_attempts: int
    prompt_debug_file: Path
    download_timeout_seconds: int
    max_pdf_size_mb: int
    marker_timeout_seconds: int
    ollama_base_url: str

    def provider_config(self) -> dict[str, object]:
        config: dict[str, object] = {
            "temperature": self.temperature,
            "timeout": self.timeout_seconds,
        }
        if self.model:
            config["model"] = self.model
        if self.provider == "ollama":
            config["base_url"] = self.ollama_base_url
        return config


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Application configuration matching the settings.toml schema."""

    project_root: Path

    # output/
    output_root: Path
    summaries_dir: Path
    daily_notes_dir: Path
    weekly_notes_dir: Path
    pdfs_dir: Path

    # state/
    state_root: Path
    state_papers_dir: Path
    state_runs_dir: Path

    # logs/
    logs_root: Path
    history_log_file: Path
    last_run_log_file: Path

    # templates
    daily_template: Path
    weekly_template: Path

    # preferences
    preferences_file: Path

    # notes
    link_style: str
    weekly_note_file: str
    rotation_day: str
    archive_name_pattern: str

    # arxiv
    max_papers: int
    arxiv_page_size: int
    ranking_shortlist_size: int
    ranking_final_pool_size: int
    ranking_min_selection_score: float
    ranking_passthrough_candidate_count: int
    default_categories: tuple[str, ...]

    # llm
    llm: LlmConfig


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(base_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (base_path / candidate).resolve()


def _config_root(candidate: Path) -> Path:
    parent = candidate.parent.resolve()
    if parent.name == "user_preferences":
        return parent.parent.resolve()
    if parent.name == "defaults" and parent.parent.name == "user_preferences":
        return parent.parent.parent.resolve()
    return parent


def load_config(config_path: Path | None = None, project_root: Path | None = None) -> AppConfig:
    """Load and validate application configuration from settings.toml."""
    root = (project_root or _default_project_root()).resolve()
    candidate = Path(config_path).expanduser().resolve() if config_path else default_config_path(root)
    data: dict[str, object] = {}

    if candidate.exists():
        with candidate.open("rb") as handle:
            data = tomllib.load(handle)
        if project_root is None:
            root = _config_root(candidate)

    output_data = data.get("output", {})
    state_data = data.get("state", {})
    logs_data = data.get("logs", {})
    templates_data = data.get("templates", {})
    preferences_data = data.get("preferences", {})
    notes_data = data.get("notes", {})
    arxiv_data = data.get("arxiv", {})
    llm_data = data.get("llm", {})

    for name, section in [
        ("output", output_data),
        ("state", state_data),
        ("logs", logs_data),
        ("templates", templates_data),
        ("preferences", preferences_data),
        ("notes", notes_data),
        ("arxiv", arxiv_data),
        ("llm", llm_data),
    ]:
        if not isinstance(section, dict):
            raise ValueError(f"Invalid configuration format for [{name}] in settings.toml.")

    # Output paths
    output_root = _resolve_path(root, str(output_data.get("root", "output")))
    summaries_dir = _resolve_path(output_root, str(output_data.get("summaries_dir", "summaries")))
    daily_notes_dir = _resolve_path(output_root, str(output_data.get("daily_notes_dir", "daily-notes")))
    weekly_notes_dir = _resolve_path(output_root, str(output_data.get("weekly_notes_dir", "weekly-notes")))
    pdfs_dir = _resolve_path(output_root, str(output_data.get("pdfs_dir", "pdfs")))

    # State paths
    state_root = _resolve_path(root, str(state_data.get("root", "state")))
    state_papers_dir = _resolve_path(state_root, str(state_data.get("papers_dir", "papers")))
    state_runs_dir = _resolve_path(state_root, str(state_data.get("runs_dir", "runs")))

    # Logs
    logs_root = _resolve_path(root, str(logs_data.get("root", "logs")))
    history_log_file = _resolve_path(logs_root, str(logs_data.get("history_file", "history.log")))
    last_run_log_file = _resolve_path(logs_root, str(logs_data.get("last_run_file", "last-run.log")))

    # Templates
    daily_template = _resolve_path(root, str(templates_data.get("daily_template", "user_preferences/daily-note-template.md")))
    weekly_template = _resolve_path(root, str(templates_data.get("weekly_template", "user_preferences/weekly-note-template.md")))

    # Preferences
    preferences_file = _resolve_path(root, str(preferences_data.get("file", "user_preferences/preferences.md")))

    # Notes
    link_style = str(notes_data.get("link_style", "wikilink")).strip().lower()
    if link_style not in _VALID_LINK_STYLES:
        raise ValueError(f"notes.link_style must be one of {_VALID_LINK_STYLES}, got '{link_style}'.")
    weekly_note_file = str(notes_data.get("weekly_note_file", "this-weeks-arxiv-papers.md"))
    rotation_day = str(notes_data.get("rotation_day", "monday")).strip().lower()
    if rotation_day not in _VALID_ROTATION_DAYS:
        raise ValueError(f"notes.rotation_day must be one of {_VALID_ROTATION_DAYS}, got '{rotation_day}'.")
    archive_name_pattern = str(notes_data.get("archive_name_pattern", "{date}-weekly-arxiv.md"))

    # Arxiv
    default_categories = tuple(str(cat) for cat in arxiv_data.get("default_categories", ["astro-ph.CO", "astro-ph.GA", "astro-ph.HE"]))
    if not default_categories:
        raise ValueError("arxiv.default_categories must contain at least one category.")
    ranking_shortlist_size = int(arxiv_data.get("retrieval_pool_size", arxiv_data.get("shortlist_size", 24)))
    ranking_final_pool_size = int(arxiv_data.get("final_pool_size", min(24, ranking_shortlist_size)))
    ranking_min_selection_score = float(arxiv_data.get("min_selection_score", 75.0))
    ranking_passthrough_candidate_count = int(arxiv_data.get("passthrough_candidate_count", 50))

    # LLM
    mode = str(llm_data.get("mode", "cli")).strip().lower()
    provider = str(llm_data.get("provider", "codex")).strip().lower()
    raw_model = llm_data.get("model")
    model = str(raw_model).strip() if raw_model not in (None, "") else None

    llm = LlmConfig(
        mode=mode,
        provider=provider,
        model=model,
        timeout_seconds=int(llm_data.get("timeout_seconds", 900)),
        max_output_tokens=int(llm_data.get("max_output_tokens", 12288)),
        temperature=float(llm_data.get("temperature", 0.2)),
        retry_attempts=int(llm_data.get("retry_attempts", 3)),
        prompt_debug_file=_resolve_path(
            root,
            str(llm_data.get("prompt_debug_file", "tmp/paper_summariser/prompt.txt")),
        ),
        download_timeout_seconds=int(llm_data.get("download_timeout_seconds", 120)),
        max_pdf_size_mb=int(llm_data.get("max_pdf_size_mb", 100)),
        marker_timeout_seconds=int(llm_data.get("marker_timeout_seconds", 300)),
        ollama_base_url=str(llm_data.get("ollama_base_url", "http://localhost:11434")),
    )

    return AppConfig(
        project_root=root,
        output_root=output_root,
        summaries_dir=summaries_dir,
        daily_notes_dir=daily_notes_dir,
        weekly_notes_dir=weekly_notes_dir,
        pdfs_dir=pdfs_dir,
        state_root=state_root,
        state_papers_dir=state_papers_dir,
        state_runs_dir=state_runs_dir,
        logs_root=logs_root,
        history_log_file=history_log_file,
        last_run_log_file=last_run_log_file,
        daily_template=daily_template,
        weekly_template=weekly_template,
        preferences_file=preferences_file,
        link_style=link_style,
        weekly_note_file=weekly_note_file,
        rotation_day=rotation_day,
        archive_name_pattern=archive_name_pattern,
        max_papers=int(arxiv_data.get("max_papers", 3)),
        arxiv_page_size=int(arxiv_data.get("page_size", arxiv_data.get("max_results", 100))),
        ranking_shortlist_size=ranking_shortlist_size,
        ranking_final_pool_size=ranking_final_pool_size,
        ranking_min_selection_score=ranking_min_selection_score,
        ranking_passthrough_candidate_count=ranking_passthrough_candidate_count,
        default_categories=default_categories,
        llm=llm,
    )
