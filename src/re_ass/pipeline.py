"""End-to-end workflow orchestration for re-ass."""

from __future__ import annotations

from datetime import date, timedelta
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


def _weekly_synthesis_word_limit(config: AppConfig, note_date: date) -> int:
    rotation_index = _WEEKDAY_NAMES.index(config.rotation_day)
    day_index = (note_date.weekday() - rotation_index) % 7
    day_index = min(day_index, 4)
    start = config.weekly_synthesis_word_limit_start
    end = config.weekly_synthesis_word_limit_end
    if start == end:
        return start
    return start + round((end - start) * (day_index / 4))


def _ranking_summary(selection) -> list[dict[str, object]]:
    selected_keys = {item.paper_key for item in selection.selected}
    weekly_interest_keys = {item.paper_key for item in selection.weekly_interest}
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
            "weekly_interest": item.paper_key in weekly_interest_keys,
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


def _weekly_interest_summary(selection) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for item in selection.weekly_interest:
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


def _replace_file(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.replace(destination_path)


def _cleanup_path(path: Path | None) -> None:
    if path is not None and path.exists():
        path.unlink()


def _bootstrap_runtime(
    config: AppConfig,
    note_manager: NoteManager,
    state_store: StateStore,
    *,
    reference_date: date,
) -> None:
    config.output_root.mkdir(parents=True, exist_ok=True)
    config.pdfs_dir.mkdir(parents=True, exist_ok=True)
    config.logs_root.mkdir(parents=True, exist_ok=True)
    note_manager.bootstrap(reference_date)
    state_store.bootstrap()


def _run_summary_base(invocation_date: date) -> dict[str, object]:
    return {
        "run_date": invocation_date.isoformat(),
        "announcement_date": None,
        "note_date": None,
        "available_announcement_dates": [],
        "pending_announcement_dates": [],
        "visible_window_start": None,
        "visible_window_end": None,
        "candidate_count": 0,
        "candidate_keys": [],
        "max_papers": 0,
        "always_summarize_score": 0.0,
        "min_selection_score": 0.0,
        "selected_paper_keys": [],
        "weekly_interest_paper_keys": [],
        "ranking_results": [],
        "selected_results": [],
        "weekly_interest_results": [],
        "selected_papers": 0,
        "weekly_interest_papers": 0,
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


def _pending_announcement_dates(
    available_dates: list[date],
    *,
    last_completed_announcement_date: date | None,
) -> list[date]:
    if not available_dates:
        return []
    if last_completed_announcement_date is None or last_completed_announcement_date < available_dates[0]:
        return list(available_dates)
    return [day for day in available_dates if day > last_completed_announcement_date]


def _scheduled_note_dates(invocation_date: date, count: int) -> list[date]:
    if count <= 0:
        return []

    note_dates: list[date] = []
    candidate = invocation_date
    while len(note_dates) < count:
        if candidate.weekday() < 5:
            note_dates.append(candidate)
        candidate -= timedelta(days=1)
    note_dates.reverse()
    return note_dates


def _note_dates_for_pending(invocation_date: date, announcement_dates: list[date]) -> dict[date, date]:
    if not announcement_dates:
        return {}
    scheduled_dates = _scheduled_note_dates(invocation_date, len(announcement_dates))
    return {
        announcement_date: scheduled_dates[index]
        for index, announcement_date in enumerate(announcement_dates)
    }


def _populate_run_summary_dates(
    run_summary: dict[str, object],
    *,
    available_dates: list[date],
    pending_dates: list[date],
    announcement_date: date | None = None,
    note_date: date | None = None,
) -> None:
    run_summary["announcement_date"] = announcement_date.isoformat() if announcement_date is not None else None
    run_summary["note_date"] = note_date.isoformat() if note_date is not None else None
    run_summary["available_announcement_dates"] = [day.isoformat() for day in available_dates]
    run_summary["pending_announcement_dates"] = [day.isoformat() for day in pending_dates]
    if available_dates:
        run_summary["visible_window_start"] = available_dates[0].isoformat()
        run_summary["visible_window_end"] = available_dates[-1].isoformat()


def _process_selected_papers(
    config: AppConfig,
    state_store: StateStore,
    generation_service: GenerationService,
    *,
    selected_papers,
    run_summary: dict[str, object],
) -> list[ProcessedPaper]:
    successful_papers: list[ProcessedPaper] = []

    for paper in selected_papers:
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

    return successful_papers


def _run_announcement_day(
    config: AppConfig,
    *,
    invocation_date: date,
    announcement_date: date,
    note_date: date,
    available_dates: list[date],
    pending_dates: list[date],
    preferences,
    note_manager: NoteManager,
    state_store: StateStore,
    generation_service: GenerationService,
    fetcher: ArxivFetcher,
    backfill: bool,
) -> int:
    run_summary = _run_summary_base(invocation_date)
    _populate_run_summary_dates(
        run_summary,
        available_dates=available_dates,
        pending_dates=pending_dates,
        announcement_date=announcement_date,
        note_date=note_date,
    )

    try:
        candidates = fetcher.collect_candidates(
            preferences,
            announcement_date=announcement_date,
            excluded_paper_keys=state_store.completed_paper_keys(),
        )
        run_summary["candidate_count"] = len(candidates)
        run_summary["candidate_keys"] = _candidate_identity_summary(candidates)

        ranker = PaperRanker(
            provider=generation_service.provider,
            config=config.llm,
            max_papers=config.max_papers,
            always_summarize_score=config.always_summarize_score,
            min_selection_score=config.min_selection_score,
        )
        selection = ranker.rank_papers(preferences, candidates)
        selected_papers = selection.selected_papers
        weekly_interest_papers = [item.paper for item in selection.weekly_interest]

        run_summary["max_papers"] = config.max_papers
        run_summary["always_summarize_score"] = config.always_summarize_score
        run_summary["min_selection_score"] = config.min_selection_score
        run_summary["selected_paper_keys"] = _selected_identity_summary(selected_papers)
        run_summary["weekly_interest_paper_keys"] = _selected_identity_summary(weekly_interest_papers)
        run_summary["ranking_results"] = _ranking_summary(selection)
        run_summary["selected_results"] = _selected_summary(selection)
        run_summary["weekly_interest_results"] = _weekly_interest_summary(selection)
        run_summary["selected_papers"] = len(selected_papers)
        run_summary["weekly_interest_papers"] = len(weekly_interest_papers)

        successful_papers = _process_selected_papers(
            config,
            state_store,
            generation_service,
            selected_papers=selected_papers,
            run_summary=run_summary,
        )

        if successful_papers:
            note_manager.update_daily_note(note_date, successful_papers[0], reference_date=invocation_date)
            run_summary["daily_note_updated"] = True

            if not backfill:
                existing_synthesis = note_manager.read_weekly_synthesis(note_date, reference_date=invocation_date)
                weekly_additions = note_manager.preview_weekly_additions(
                    note_date,
                    successful_papers,
                    reference_date=invocation_date,
                )
                synthesis = generation_service.generate_weekly_synthesis(
                    existing_synthesis,
                    weekly_additions,
                    word_limit=_weekly_synthesis_word_limit(config, note_date),
                )
                note_manager.update_weekly_note(
                    note_date,
                    successful_papers,
                    synthesis,
                    interest_papers=weekly_interest_papers,
                    reference_date=invocation_date,
                )
                run_summary["weekly_note_updated"] = True
        elif weekly_interest_papers and not selected_papers and not backfill:
            existing_synthesis = note_manager.read_weekly_synthesis(note_date, reference_date=invocation_date)
            note_manager.update_weekly_note(
                note_date,
                [],
                existing_synthesis,
                interest_papers=weekly_interest_papers,
                reference_date=invocation_date,
            )
            run_summary["weekly_note_updated"] = True
            LOGGER.info(
                "No papers were promoted to full summaries for announcement date %s; weekly interest bullets were added without updating the daily note or synthesis.",
                announcement_date.isoformat(),
            )
        else:
            LOGGER.info(
                "No papers completed successfully for announcement date %s; daily and weekly summaries were left unchanged.",
                announcement_date.isoformat(),
            )

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
        state_store.save_run_summary(run_summary, label=f"announcement-{announcement_date.isoformat()}")
        state_store.save_completed_announcement_date(announcement_date)
        return 0
    except Exception as error:
        LOGGER.exception("Fatal pipeline error for announcement date %s", announcement_date.isoformat())
        run_summary["fatal_error"] = str(error)
        run_summary["failed_papers"] = len(run_summary["failed_keys"])
        run_summary["completed_papers"] = len(run_summary["completed_keys"])
        state_store.save_run_summary(run_summary, label=f"announcement-{announcement_date.isoformat()}-fatal")
        return 1


def run(config: AppConfig, run_date: date | None = None, *, backfill: bool = False) -> int:
    """Execute the full workflow and return an exit code."""
    invocation_date = run_date or date.today()
    note_manager = NoteManager(config)
    state_store = StateStore(config)

    overall_summary = _run_summary_base(invocation_date)

    try:
        _bootstrap_runtime(config, note_manager, state_store, reference_date=invocation_date)
        if not backfill:
            note_manager.rotate_weekly_note_if_needed(invocation_date)

        preferences = load_preferences(config.preferences_file)
        generation_service = GenerationService(config=config.llm)
        fetcher = ArxivFetcher(page_size=config.arxiv_page_size)
        available_dates = list(fetcher.available_announcement_dates(preferences.categories))

        if backfill:
            pending_dates = [invocation_date]
            note_date_map = {invocation_date: invocation_date}
        else:
            last_completed_announcement_date = state_store.load_completed_announcement_date()
            pending_dates = _pending_announcement_dates(
                available_dates,
                last_completed_announcement_date=last_completed_announcement_date,
            )
            note_date_map = _note_dates_for_pending(invocation_date, pending_dates)

        _populate_run_summary_dates(
            overall_summary,
            available_dates=available_dates,
            pending_dates=pending_dates,
        )

        if backfill and invocation_date not in available_dates:
            raise ValueError(
                f"Announcement date {invocation_date.isoformat()} is not visible in the current arXiv recent window."
            )

        if not pending_dates:
            LOGGER.info("No new announcement day is available to process.")
            state_store.save_run_summary(overall_summary, label="overall")
            return 0

        for announcement_date in pending_dates:
            note_date = note_date_map[announcement_date]
            exit_code = _run_announcement_day(
                config,
                invocation_date=invocation_date,
                announcement_date=announcement_date,
                note_date=note_date,
                available_dates=available_dates,
                pending_dates=pending_dates,
                preferences=preferences,
                note_manager=note_manager,
                state_store=state_store,
                generation_service=generation_service,
                fetcher=fetcher,
                backfill=backfill,
            )
            if exit_code != 0:
                return exit_code

        return 0
    except Exception as error:
        LOGGER.exception("Fatal pipeline error for %s", invocation_date.isoformat())
        overall_summary["fatal_error"] = str(error)
        state_store.save_run_summary(overall_summary, label="overall-fatal")
        return 1
