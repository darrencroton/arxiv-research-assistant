from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import logging
from pathlib import Path

from re_ass.arxiv_fetcher import ArxivFetcher
from re_ass.config_manager import load_preferences
from re_ass.llm_orchestrator import LlmOrchestrator
from re_ass.settings import AppConfig, load_config
from re_ass.vault_manager import VaultManager


LOGGER = logging.getLogger(__name__)


def _determine_window_end(target_date: date, *, explicit_date: bool) -> datetime:
    local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
    if explicit_date:
        next_day = target_date + timedelta(days=1)
        return datetime.combine(next_day, time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch, rank, and summarise daily arXiv papers.")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to a re_ass TOML configuration file.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Run the workflow for a specific ISO date (YYYY-MM-DD).",
    )
    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run(config: AppConfig, run_date: date | None = None) -> int:
    target_date = run_date or date.today()
    vault_manager = VaultManager(config)
    vault_manager.bootstrap()
    vault_manager.rotate_weekly_note_if_needed(target_date)

    preferences = load_preferences(config.preferences_file, config.default_categories)
    fetcher = ArxivFetcher(
        max_results=config.arxiv_max_results,
        fetch_window_hours=config.fetch_window_hours,
        fallback_window_hours=config.fallback_window_hours,
        now_provider=lambda: _determine_window_end(target_date, explicit_date=run_date is not None),
    )
    papers = fetcher.fetch_top_papers(preferences, config.max_papers)
    if not papers:
        LOGGER.info("No recent matching arXiv papers found.")
        return 0

    orchestrator = LlmOrchestrator(
        command_prefix=config.llm_command_prefix,
        timeout_seconds=config.llm_timeout_seconds,
        enabled=config.llm_enabled,
        allow_local_paper_note_fallback=config.allow_local_paper_note_fallback,
    )

    processed_papers = [orchestrator.process_paper(paper, config.papers_dir) for paper in papers]
    top_paper = processed_papers[0]
    vault_manager.update_daily_note(target_date, top_paper)

    existing_synthesis = vault_manager.read_weekly_synthesis()
    synthesis = orchestrator.generate_weekly_synthesis(existing_synthesis, processed_papers)
    vault_manager.update_weekly_note(target_date, processed_papers, synthesis)

    LOGGER.info("Processed %s paper(s) for %s.", len(processed_papers), target_date.isoformat())
    return 0


def cli(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    return run(config, args.date)
