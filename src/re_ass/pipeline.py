"""End-to-end workflow orchestration for re-ass."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
import logging
from pathlib import Path
import tempfile

from re_ass.arxiv_fetcher import ArxivFetcher
from re_ass.generation_service import GenerationService
from re_ass.models import ProcessedPaper
from re_ass.note_manager import NoteManager
from re_ass.paper_identity import PaperIdentity, derive_identity
from re_ass.preferences import load_preferences
from re_ass.ranking import PaperRanker
from re_ass.settings import AppConfig
from re_ass.state_store import StateStore


LOGGER = logging.getLogger(__name__)
_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _local_timezone() -> timezone:
    return datetime.now().astimezone().tzinfo or timezone.utc


def _local_day_bounds(target_date: date) -> tuple[datetime, datetime]:
    local_timezone = _local_timezone()
    start = datetime.combine(target_date, time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    end = datetime.combine(_next_day(target_date), time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    return start, end


def _weekly_synthesis_word_limit(config: AppConfig, run_date: date) -> int:
    rotation_index = _WEEKDAY_NAMES.index(config.rotation_day)
    day_index = (run_date.weekday() - rotation_index) % 7
    day_index = min(day_index, 4)
    start = config.weekly_synthesis_word_limit_start
    end = config.weekly_synthesis_word_limit_end
    if start == end:
        return start
    return start + round((end - start) * (day_index / 4))


def _determine_interval(
    target_date: date,
    *,
    explicit_date: bool,
    state_store: StateStore,
) -> tuple[datetime, datetime]:
    if explicit_date:
        return _local_day_bounds(target_date)

    now_utc = datetime.now(timezone.utc)
    previous_end = state_store.latest_successful_run_end()
    if previous_end is not None and previous_end < now_utc:
        return previous_end.astimezone(timezone.utc), now_utc

    day_start, _day_end = _local_day_bounds(target_date)
    return day_start, now_utc


def _ranking_summary(selection) -> list[dict[str, object]]:
    selected_keys = {item.paper_key for item in selection.selected}
    results: list[dict[str, object]] = []
    for item in selection.ranked:
        result = {
            "paper_key": item.paper_key,
            "source_id": item.source_id,
            "title": item.paper.title,
            "published": item.paper.published.isoformat(),
            "score": item.score,
            "rationale": item.rationale,
            "selected": item.paper_key in selected_keys,
        }
        if item.science_match is not None:
            result["science_match"] = item.science_match
        if item.method_match is not None:
            result["method_match"] = item.method_match
        results.append(result)
    return results


def _selected_identity_summary(papers) -> list[str]:
    return [derive_identity(paper).paper_key for paper in papers]


def _candidate_identity_summary(papers) -> list[str]:
    return [derive_identity(paper).paper_key for paper in papers]


def _selected_summary(selection) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for item in selection.selected:
        result = {
            "paper_key": item.paper_key,
            "source_id": item.source_id,
            "title": item.paper.title,
            "score": item.score,
            "rationale": item.rationale,
        }
        if item.science_match is not None:
            result["science_match"] = item.science_match
        if item.method_match is not None:
            result["method_match"] = item.method_match
        results.append(result)
    return results


def _next_day(target_date: date) -> date:
    return date.fromordinal(target_date.toordinal() + 1)


def _replace_file(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.replace(destination_path)


def _cleanup_path(path: Path | None) -> None:
    if path is not None and path.exists():
        path.unlink()


def _bootstrap_runtime(config: AppConfig, note_manager: NoteManager, state_store: StateStore) -> None:
    config.output_root.mkdir(parents=True, exist_ok=True)
    config.pdfs_dir.mkdir(parents=True, exist_ok=True)
    config.logs_root.mkdir(parents=True, exist_ok=True)
    note_manager.bootstrap()
    state_store.bootstrap()


def _run_summary_base(target_date: date) -> dict[str, object]:
    return {
        "run_date": target_date.isoformat(),
        "interval_start": None,
        "interval_end": None,
        "candidate_count": 0,
        "candidate_keys": [],
        "max_papers": 0,
        "min_selection_score": 0.0,
        "selected_paper_keys": [],
        "ranking_results": [],
        "selected_results": [],
        "selected_papers": 0,
        "completed_papers": 0,
        "failed_papers": 0,
        "completed_keys": [],
        "failed_keys": [],
        "daily_note_updated": False,
        "weekly_note_updated": False,
        "fatal_error": None,
    }


def _save_failure_record(
    state_store: StateStore,
    *,
    paper,
    identity: PaperIdentity,
    micro_summary: str | None,
    error: Exception,
) -> None:
    state_store.save_paper_record(
        paper_key=identity.paper_key,
        source_id=identity.source_id,
        title=paper.title,
        published=paper.published.isoformat(),
        filename_stem=identity.filename_stem,
        status="failed",
        micro_summary=micro_summary,
        last_error=str(error),
    )


def run(config: AppConfig, run_date: date | None = None, *, backfill: bool = False) -> int:
    """Execute the full workflow and return an exit code."""
    target_date = run_date or date.today()
    run_summary = _run_summary_base(target_date)

    note_manager = NoteManager(config)
    state_store = StateStore(config)

    try:
        _bootstrap_runtime(config, note_manager, state_store)
        if backfill:
            LOGGER.info(
                "Explicit backfill for %s: leaving the current weekly summary unchanged.",
                target_date.isoformat(),
            )
        else:
            note_manager.rotate_weekly_note_if_needed(target_date)

        preferences = load_preferences(config.preferences_file)
        generation_service = GenerationService(config=config.llm)
        interval_start, interval_end = _determine_interval(
            target_date,
            explicit_date=run_date is not None,
            state_store=state_store,
        )
        fetcher = ArxivFetcher(
            page_size=config.arxiv_page_size,
        )
        candidates = fetcher.collect_candidates(
            preferences,
            start=interval_start,
            end=interval_end,
            excluded_paper_keys=state_store.completed_paper_keys(),
        )
        run_summary["interval_start"] = interval_start.isoformat()
        run_summary["interval_end"] = interval_end.isoformat()
        run_summary["candidate_count"] = len(candidates)
        run_summary["candidate_keys"] = _candidate_identity_summary(candidates)
        ranker = PaperRanker(
            provider=generation_service.provider,
            config=config.llm,
            max_papers=config.max_papers,
            min_selection_score=config.min_selection_score,
        )
        selection = ranker.rank_papers(preferences, candidates)

        papers = selection.selected_papers
        run_summary["max_papers"] = config.max_papers
        run_summary["min_selection_score"] = config.min_selection_score
        run_summary["selected_paper_keys"] = _selected_identity_summary(papers)
        run_summary["ranking_results"] = _ranking_summary(selection)
        run_summary["selected_results"] = _selected_summary(selection)
        run_summary["selected_papers"] = len(papers)
        successful_papers: list[ProcessedPaper] = []

        for paper in papers:
            identity = derive_identity(paper)
            LOGGER.info("Processing %s (%s)", paper.title, identity.paper_key)
            micro_summary: str | None = None
            final_note_path: Path | None = None
            final_pdf_path: Path | None = None

            with tempfile.TemporaryDirectory(prefix=f"re-ass-paper-{identity.source_id}-") as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                try:
                    state_store.save_paper_record(
                        paper_key=identity.paper_key,
                        source_id=identity.source_id,
                        title=paper.title,
                        published=paper.published.isoformat(),
                        filename_stem=identity.filename_stem,
                        status="selected",
                    )

                    micro_summary = generation_service.generate_micro_summary(paper)
                    state_store.save_paper_record(
                        paper_key=identity.paper_key,
                        source_id=identity.source_id,
                        title=paper.title,
                        published=paper.published.isoformat(),
                        filename_stem=identity.filename_stem,
                        status="micro_summary_generated",
                        micro_summary=micro_summary,
                    )

                    staged_pdf_path = generation_service.stage_pdf_download(paper, temp_dir)
                    state_store.save_paper_record(
                        paper_key=identity.paper_key,
                        source_id=identity.source_id,
                        title=paper.title,
                        published=paper.published.isoformat(),
                        filename_stem=identity.filename_stem,
                        status="pdf_downloaded",
                        micro_summary=micro_summary,
                    )

                    note_content = generation_service.build_paper_note_content(paper, staged_pdf_path)
                    staged_note_path = temp_dir / identity.note_filename
                    staged_note_path.write_text(note_content, encoding="utf-8")

                    final_pdf_path = config.pdfs_dir / identity.pdf_filename
                    _replace_file(staged_pdf_path, final_pdf_path)

                    final_note_path = config.summaries_dir / identity.note_filename
                    _replace_file(staged_note_path, final_note_path)

                    state_store.save_paper_record(
                        paper_key=identity.paper_key,
                        source_id=identity.source_id,
                        title=paper.title,
                        published=paper.published.isoformat(),
                        filename_stem=identity.filename_stem,
                        status="note_written",
                        note_path=str(final_note_path),
                        pdf_path=str(final_pdf_path),
                        micro_summary=micro_summary,
                    )
                    successful_papers.append(
                        ProcessedPaper(
                            paper=paper,
                            paper_key=identity.paper_key,
                            filename_stem=identity.filename_stem,
                            note_path=final_note_path,
                            pdf_path=final_pdf_path,
                            micro_summary=micro_summary,
                        )
                    )
                except Exception as error:
                    LOGGER.error("Failed to process %s: %s", paper.title, error)
                    _cleanup_path(final_note_path)
                    _cleanup_path(final_pdf_path)
                    _save_failure_record(
                        state_store,
                        paper=paper,
                        identity=identity,
                        micro_summary=micro_summary,
                        error=error,
                    )
                    run_summary["failed_keys"].append(identity.paper_key)

        if successful_papers:
            note_manager.update_daily_note(target_date, successful_papers[0])
            run_summary["daily_note_updated"] = True

            if not backfill:
                existing_synthesis = note_manager.read_weekly_synthesis()
                weekly_additions = note_manager.preview_weekly_additions(target_date, successful_papers)
                synthesis = generation_service.generate_weekly_synthesis(
                    existing_synthesis,
                    weekly_additions,
                    word_limit=_weekly_synthesis_word_limit(config, target_date),
                )
                note_manager.update_weekly_note(target_date, successful_papers, synthesis)
                run_summary["weekly_note_updated"] = True
        else:
            LOGGER.info("No papers completed successfully; daily and weekly summaries were left unchanged.")

        for processed_paper in successful_papers:
            identity = derive_identity(processed_paper.paper)
            state_store.save_paper_record(
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                title=processed_paper.paper.title,
                published=processed_paper.paper.published.isoformat(),
                filename_stem=identity.filename_stem,
                status="completed",
                note_path=str(processed_paper.note_path),
                pdf_path=str(processed_paper.pdf_path) if processed_paper.pdf_path is not None else None,
                micro_summary=processed_paper.micro_summary,
            )
            run_summary["completed_keys"].append(identity.paper_key)

        run_summary["completed_papers"] = len(successful_papers)
        run_summary["failed_papers"] = len(run_summary["failed_keys"])
        state_store.save_run_summary(target_date.isoformat(), run_summary)
        return 0
    except Exception as error:
        LOGGER.exception("Fatal pipeline error for %s", target_date.isoformat())
        run_summary["fatal_error"] = str(error)
        run_summary["failed_papers"] = len(run_summary["failed_keys"])
        run_summary["completed_papers"] = len(run_summary["completed_keys"])
        state_store.save_run_summary(target_date.isoformat(), run_summary)
        return 1
