"""CLI entry point for re-ass."""

from __future__ import annotations

import argparse
from datetime import date
import logging
from pathlib import Path

from re_ass.bootstrap import ensure_user_preferences
from re_ass.settings import AppConfig, load_config


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Fetch, rank, and summarise daily arXiv papers.")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to a settings TOML configuration file.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Run the workflow for a specific ISO date (YYYY-MM-DD).",
    )
    return parser


def configure_logging(config: AppConfig | None = None) -> None:
    """Set up console and optional file logging."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if config is not None:
        config.logs_root.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(config.last_run_log_file, mode="w", encoding="utf-8")
        handlers.append(file_handler)

        history_handler = logging.FileHandler(config.history_log_file, mode="a", encoding="utf-8")
        handlers.append(history_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def cli(argv: list[str] | None = None) -> int:
    """Parse arguments, load config, and hand off to the pipeline."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.config is None:
        ensure_user_preferences()

    config = load_config(args.config)

    configure_logging(config)

    from re_ass.pipeline import run

    return run(config, args.date, backfill=args.date is not None)
