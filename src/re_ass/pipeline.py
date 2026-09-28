"""End-to-end workflow orchestration for re-ass."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, timedelta
import logging
from pathlib import Path
import tempfile

from re_ass.arxiv_fetcher import ArxivFetcher
from re_ass.generation_service import GenerationService, make_provider
from re_ass.models import ArxivPaper, ProcessedPaper
from re_ass.note_manager import NoteManager
from re_ass.paper_identity import PaperIdentity, derive_identity
from re_ass.paper_summariser.service import log_summary_text_quality_warnings
from re_ass.preferences import load_preferences
from re_ass.prompt_logger import PromptLogger
from re_ass.ranking import PaperRanker, RankingError
from re_ass.settings import AppConfig, LlmConfig
from re_ass.state_store import StateStore


LOGGER = logging.getLogger(__name__)
_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _weekly_synthesis_word_limit(config: AppConfig, note_date: date, reference_date: date) -> int:
    """Synthesis budget for the note's week, scaled to the latest day that week can hold.

    That is the later of note_date and reference_date within the same week, or
    the week's last day once reference_date has moved on to a later week, so
    backfilling an earlier day never shrinks a synthesis covering later days.
    """
    rotation_index = _WEEKDAY_NAMES.index(config.rotation_day)
    note_offset = (note_date.weekday() - rotation_index) % 7
    reference_offset = (reference_date.weekday() - rotation_index) % 7
    same_week = note_date - timedelta(days=note_offset) == reference_date - timedelta(days=reference_offset)
    day_index = min(max(note_offset, reference_offset) if same_week else 4, 4)
    start = config.weekly_synthesis_word_limit_start
    end = config.weekly_synthesis_word_limit_end
    if start == end:
        return start
    return start + round((end - start) * (day_index / 4))


def _ranking_summary(selection) -> list[dict[str, object]]:
    selected_keys = {item.paper_key for item in selection.selected}
    weekly_interest_keys = {item.paper_key for item in selection.weekly_interest}
    return [
        _ranked_item_summary(
            item,
            include_published=True,
            selected=item.paper_key in selected_keys,
            weekly_interest=item.paper_key in weekly_interest_keys,
        )
        for item in selection.ranked
    ]


def _paper_keys(papers) -> list[str]:
    return [derive_identity(paper).paper_key for paper in papers]


def _ranked_items_summary(items) -> list[dict[str, object]]:
    return [_ranked_item_summary(item) for item in items]


def _ranked_item_summary(
    item,
    *,
    include_published: bool = False,
    selected: bool | None = None,
    weekly_interest: bool | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {"paper_key": item.paper_key, "source_id": item.source_id}
    if include_published:
        result["published"] = item.paper.published.isoformat()
    result["title"] = item.paper.title
    if item.science_match is not None:
        result["science_match"] = item.science_match
    if item.method_match is not None:
        result["method_match"] = item.method_match
    result["score"] = item.score
    if selected is not None:
        result["selected"] = selected
    result["rationale"] = item.rationale
    if getattr(item, "score_filled", False):
        result["score_filled"] = True
    if weekly_interest is not None:
        result["weekly_interest"] = weekly_interest
    return result


def _paper_record_data(paper: ArxivPaper, identity: PaperIdentity) -> dict[str, str]:
    return {
        "paper_key": identity.paper_key,
        "source_id": identity.source_id,
        "title": paper.title,
        "published": paper.published.isoformat(),
        "filename_stem": identity.filename_stem,
    }


def _save_paper_status(
    state_store: StateStore,
    *,
    paper: ArxivPaper,
    identity: PaperIdentity,
    status: str,
    note_path: Path | None = None,
    pdf_path: Path | None = None,
    micro_summary: str | None = None,
    last_error: str | None = None,
) -> None:
    state_store.save_paper_record(
        **_paper_record_data(paper, identity),
        status=status,
        note_path=str(note_path) if note_path is not None else None,
        pdf_path=str(pdf_path) if pdf_path is not None else None,
        micro_summary=micro_summary,
        last_error=last_error,
    )


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


def _llm_config_stamp(llm: LlmConfig) -> dict[str, object]:
    return {
        "mode": llm.mode,
        "provider": llm.provider,
        "model": llm.model,
        "effort": llm.effort,
        "temperature": llm.temperature,
        "max_output_tokens": llm.max_output_tokens,
    }


def _llm_stamp(config: AppConfig) -> dict[str, object]:
    """Snapshot of the LLM config the pipeline is about to use.

    Persisted into every run summary so the A/B compare script (and any
    after-the-fact audit) can identify the model/provider/effort actually
    in play for each LLM role without re-reading the settings TOML, which
    may have drifted by then.
    """
    base = _llm_config_stamp(config.llm)
    return {
        **base,
        "base": base,
        "ranking": _llm_config_stamp(config.ranking_llm),
        "summary": _llm_config_stamp(config.summary_llm),
    }


def _run_summary_base(invocation_date: date, llm_stamp: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "run_date": invocation_date.isoformat(),
        "llm": llm_stamp or {},
        "announcement_date": None,
        "note_date": None,
        "available_announcement_dates": [],
        "pending_announcement_dates": [],
        "feed_announcement_dates": [],
        "snapshot_announcement_dates": [],
        "listing_gap_dates": [],
        "listing_gap_fallback": None,
        "visible_window_start": None,
        "visible_window_end": None,
        "candidate_count": 0,
        "candidate_keys": [],
        "min_summarize_score": 0.0,
        "min_selection_score": 0.0,
        "max_summarized_papers": 0,
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
    _save_paper_status(
        state_store,
        paper=paper,
        identity=identity,
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


def _next_weekday(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _backfill_note_date(config: AppConfig, announcement_date: date) -> date:
    """Note date a standard run would have used for this announcement day."""
    return _next_weekday(announcement_date) if config.shift_announcements_to_next_weekday else announcement_date


def _listing_gap_dates(
    listed_dates: Collection[date],
    *,
    last_completed_announcement_date: date | None,
    backfill_date: date | None,
) -> list[date]:
    """Weekday announcement days that no known listing (feed or snapshot) covers.

    Backfill: the requested date, unless it is already listed. Standard run:
    every weekday strictly between the completed marker and the latest listed
    date that isn't itself listed; empty on a first run (no marker yet) or
    once the marker has caught up to the latest listed date.
    """
    if backfill_date is not None:
        return [] if backfill_date in listed_dates else [backfill_date]

    if last_completed_announcement_date is None or not listed_dates:
        return []

    latest_listed_date = max(listed_dates)
    gap_dates: list[date] = []
    candidate = last_completed_announcement_date
    while True:
        candidate = _next_weekday(candidate)
        if candidate >= latest_listed_date:
            break
        if candidate not in listed_dates:
            gap_dates.append(candidate)
    return gap_dates


def _snapshot_lookup_dates(
    invocation_date: date,
    *,
    last_completed_announcement_date: date | None,
    backfill: bool,
) -> list[date]:
    """Announcement days worth loading a saved listing snapshot for.

    Backfill: the requested day. Standard run: every weekday after the
    completed marker up to the invocation date (older days are already done,
    later ones cannot be announced yet). No marker means a first run, which
    looks at the current feed day only, so nothing is looked up.
    """
    if backfill:
        return [invocation_date]
    if last_completed_announcement_date is None:
        return []
    days: list[date] = []
    candidate = _next_weekday(last_completed_announcement_date)
    while candidate <= invocation_date:
        days.append(candidate)
        candidate = _next_weekday(candidate)
    return days


def _load_listing_snapshots(
    state_store: StateStore,
    categories: tuple[str, ...],
    days: list[date],
) -> dict[str, dict[date, list[str]]]:
    """Per-category listings for those of days that have a complete saved snapshot."""
    listings: dict[str, dict[date, list[str]]] = {category: {} for category in categories}
    for day in days:
        snapshot = state_store.load_listing_snapshot(day, categories)
        if snapshot is not None:
            for category, ids in snapshot.items():
                listings[category][day] = ids
    return listings


def _save_listing_snapshots(
    state_store: StateStore,
    fetcher: ArxivFetcher,
    categories: tuple[str, ...],
    days: Collection[date],
    *,
    source: str,
) -> None:
    """Persist each day's listing so it never needs refetching; failures only warn.

    Snapshots are a recovery aid for later runs, so a write error must not
    abort the current one.
    """
    for day in sorted(days):
        try:
            state_store.save_listing_snapshot(day, fetcher.listing_for_day(categories, day), source=source)
        except OSError as error:
            LOGGER.warning(
                "Could not save the %s listing snapshot for %s under %s: %s",
                source, day.isoformat(), state_store.listings_dir, error,
            )


def _warn_unfilled_listing_gap(gap_dates: list[date]) -> None:
    commands = "\n".join(f"  uv run re-ass --date {day.isoformat()}" for day in gap_dates)
    LOGGER.warning(
        "Announcement day(s) %s to %s were not covered by the arXiv RSS feed and the "
        "recent-listing fallback did not cover them; continuing with the current feed day. The "
        "completed-announcement marker will advance past these dates, so they will not be "
        "retried automatically. Only days still visible in arXiv's recent listing can be "
        "recovered. To recover a day, run:\n%s\n"
        "Each backfill writes that announcement day where a standard run would have, "
        "updating the daily note and the weekly note for that note date.",
        gap_dates[0].isoformat(),
        gap_dates[-1].isoformat(),
        commands,
    )


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
                _save_paper_status(state_store, paper=paper, identity=identity, status="selected")

                micro_summary = generation_service.generate_micro_summary(paper)
                _save_paper_status(
                    state_store,
                    paper=paper,
                    identity=identity,
                    status="micro_summary_generated",
                    micro_summary=micro_summary,
                )

                staged_pdf_path = generation_service.stage_pdf_download(paper, temp_dir)
                _save_paper_status(
                    state_store,
                    paper=paper,
                    identity=identity,
                    status="pdf_downloaded",
                    micro_summary=micro_summary,
                )

                note_content = generation_service.build_paper_note_content(paper, staged_pdf_path)
                staged_note_path = temp_dir / identity.note_filename
                staged_note_path.write_text(note_content, encoding="utf-8")
                log_summary_text_quality_warnings(note_content, staged_note_path)

                final_pdf_path = config.pdfs_dir / identity.pdf_filename
                _replace_file(staged_pdf_path, final_pdf_path)

                final_note_path = config.summaries_dir / identity.note_filename
                _replace_file(staged_note_path, final_note_path)

                _save_paper_status(
                    state_store,
                    paper=paper,
                    identity=identity,
                    status="note_written",
                    note_path=final_note_path,
                    pdf_path=final_pdf_path,
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


def _publish_weekly_note(
    config: AppConfig,
    note_manager: NoteManager,
    generation_service: GenerationService,
    *,
    note_date: date,
    reference_date: date,
    successful_papers: list[ProcessedPaper],
    weekly_interest_papers: list[ArxivPaper],
) -> None:
    """Write an announcement day's results into the weekly note.

    With successful papers: the regenerated synthesis and day block. With
    none: only the interest bullets (existing synthesis kept), if there are
    any. The note is resolved against reference_date, so a past note_date
    lands in the archived weekly note. Callers own the daily note and any
    run-summary bookkeeping.
    """
    if successful_papers:
        existing_synthesis = note_manager.read_weekly_synthesis(note_date, reference_date=reference_date)
        weekly_additions = note_manager.preview_weekly_additions(
            note_date,
            successful_papers,
            reference_date=reference_date,
        )
        synthesis = generation_service.generate_weekly_synthesis(
            existing_synthesis,
            weekly_additions,
            word_limit=_weekly_synthesis_word_limit(config, note_date, reference_date),
            max_tokens=config.weekly_synthesis_max_tokens,
        )
    elif weekly_interest_papers:
        synthesis = note_manager.read_weekly_synthesis(note_date, reference_date=reference_date)
    else:
        return
    note_manager.update_weekly_note(
        note_date,
        successful_papers,
        synthesis,
        interest_papers=weekly_interest_papers,
        reference_date=reference_date,
    )


def _run_announcement_day(
    config: AppConfig,
    *,
    invocation_date: date,
    reference_date: date,
    announcement_date: date,
    note_date: date,
    available_dates: list[date],
    pending_dates: list[date],
    preferences,
    note_manager: NoteManager,
    state_store: StateStore,
    generation_service: GenerationService,
    fetcher: ArxivFetcher,
    listing_summary: dict[str, object],
) -> int:
    run_summary = _run_summary_base(invocation_date, _llm_stamp(config))
    _populate_run_summary_dates(
        run_summary,
        available_dates=available_dates,
        pending_dates=pending_dates,
        announcement_date=announcement_date,
        note_date=note_date,
    )
    run_summary.update(listing_summary)

    try:
        candidates = fetcher.collect_candidates(
            preferences,
            announcement_date=announcement_date,
            excluded_paper_keys=state_store.completed_paper_keys(),
        )
        run_summary["candidate_count"] = len(candidates)
        run_summary["candidate_keys"] = _paper_keys(candidates)

        ranking_provider = (
            generation_service.provider
            if config.ranking_llm is config.summary_llm
            else make_provider(config.ranking_llm)
        )
        ranker = PaperRanker(
            provider=ranking_provider,
            config=config.ranking_llm,
            min_summarize_score=config.min_summarize_score,
            min_selection_score=config.min_selection_score,
            max_summarized_papers=config.max_summarized_papers,
            batch_size=config.ranking_llm.ranking_batch_size,
            prompt_logger=generation_service.prompt_logger,
        )
        selection = ranker.rank_papers(preferences, candidates)
        selected_papers = selection.selected_papers
        weekly_interest_papers = [item.paper for item in selection.weekly_interest]

        run_summary["min_summarize_score"] = config.min_summarize_score
        run_summary["min_selection_score"] = config.min_selection_score
        run_summary["max_summarized_papers"] = config.max_summarized_papers
        run_summary["selected_paper_keys"] = _paper_keys(selected_papers)
        run_summary["weekly_interest_paper_keys"] = _paper_keys(weekly_interest_papers)
        run_summary["ranking_results"] = _ranking_summary(selection)
        run_summary["selected_results"] = _ranked_items_summary(selection.selected)
        run_summary["weekly_interest_results"] = _ranked_items_summary(selection.weekly_interest)
        run_summary["selected_papers"] = len(selected_papers)
        run_summary["weekly_interest_papers"] = len(weekly_interest_papers)

        if not selected_papers and candidates:
            # Candidates were fetched and ranked but none cleared any threshold.
            note_manager.mark_daily_no_papers(note_date, reference_date=reference_date)
            run_summary["daily_note_updated"] = True
            LOGGER.info(
                "No papers cleared the selection threshold for announcement date %s; daily note marked with no-papers placeholder.",
                announcement_date.isoformat(),
            )
            # Partial-match papers (e.g. dual_match failures) may still appear in
            # weekly_interest even when nothing was selected — preserve them.
            _publish_weekly_note(
                config,
                note_manager,
                generation_service,
                note_date=note_date,
                reference_date=reference_date,
                successful_papers=[],
                weekly_interest_papers=weekly_interest_papers,
            )
            run_summary["weekly_note_updated"] = bool(weekly_interest_papers)
            successful_papers = []
        elif not selected_papers:
            # No candidates to process (all already completed or truly empty day).
            successful_papers = []
        else:
            successful_papers = _process_selected_papers(
                config,
                state_store,
                generation_service,
                selected_papers=selected_papers,
                run_summary=run_summary,
            )
            if successful_papers:
                note_manager.update_daily_note(note_date, successful_papers[0], reference_date=reference_date)
                run_summary["daily_note_updated"] = True
            _publish_weekly_note(
                config,
                note_manager,
                generation_service,
                note_date=note_date,
                reference_date=reference_date,
                successful_papers=successful_papers,
                weekly_interest_papers=weekly_interest_papers,
            )
            run_summary["weekly_note_updated"] = bool(successful_papers or weekly_interest_papers)
            if not successful_papers and weekly_interest_papers:
                LOGGER.info(
                    "No papers completed successfully for announcement date %s; weekly interest bullets were added without updating the daily note or synthesis.",
                    announcement_date.isoformat(),
                )
            elif not successful_papers:
                LOGGER.info(
                    "No papers completed successfully for announcement date %s; daily and weekly summaries were left unchanged.",
                    announcement_date.isoformat(),
                )

        for processed_paper in successful_papers:
            identity = derive_identity(processed_paper.paper)
            _save_paper_status(
                state_store,
                paper=processed_paper.paper,
                identity=identity,
                status="completed",
                note_path=processed_paper.note_path,
                pdf_path=processed_paper.pdf_path,
                micro_summary=processed_paper.micro_summary,
            )
            run_summary["completed_keys"].append(identity.paper_key)

        run_summary["completed_papers"] = len(successful_papers)
        run_summary["failed_papers"] = len(run_summary["failed_keys"])
        # Never move the marker backwards: a --date backfill can target a day
        # older than standard runs have already completed, and the next
        # scheduled run would then reprocess an already-finished day. Read it
        # before saving the run summary, which the marker falls back to when
        # no checkpoint file exists yet.
        current_marker = state_store.load_completed_announcement_date()
        state_store.save_run_summary(run_summary, label=f"announcement-{announcement_date.isoformat()}")
        if current_marker is None or announcement_date > current_marker:
            state_store.save_completed_announcement_date(announcement_date)
        return 0
    except RankingError as error:
        LOGGER.error(
            "Ranking failed for announcement date %s: %s",
            announcement_date.isoformat(),
            error,
        )
        run_summary["fatal_error"] = str(error)
        run_summary["failed_papers"] = len(run_summary["failed_keys"])
        run_summary["completed_papers"] = len(run_summary["completed_keys"])
        state_store.save_run_summary(run_summary, label=f"announcement-{announcement_date.isoformat()}-fatal")
        return 1
    except Exception as error:
        LOGGER.exception("Fatal pipeline error for announcement date %s", announcement_date.isoformat())
        run_summary["fatal_error"] = str(error)
        run_summary["failed_papers"] = len(run_summary["failed_keys"])
        run_summary["completed_papers"] = len(run_summary["completed_keys"])
        state_store.save_run_summary(run_summary, label=f"announcement-{announcement_date.isoformat()}-fatal")
        return 1


def run(
    config: AppConfig,
    run_date: date | None = None,
    *,
    backfill: bool = False,
    today: date | None = None,
) -> int:
    """Execute the full workflow and return an exit code.

    A backfill's run_date names the announcement day to process, so notes are
    resolved against today (injectable for tests) instead; a standard run is
    resolved against its own invocation date.
    """
    current_date = today or date.today()
    invocation_date = run_date or current_date
    reference_date = current_date if backfill else invocation_date
    note_manager = NoteManager(config)
    state_store = StateStore(config)

    overall_summary = _run_summary_base(invocation_date, _llm_stamp(config))

    try:
        _bootstrap_runtime(config, note_manager, state_store, reference_date=reference_date)
        # Rotate before any weekly write so a backfill into a past week never
        # creates an archive file that a later rotation would collide with.
        note_manager.rotate_weekly_note_if_needed(reference_date)
        backfill_note_date = _backfill_note_date(config, invocation_date) if backfill else None
        if backfill_note_date is not None and backfill_note_date > reference_date:
            raise ValueError(
                f"Announcement date {invocation_date.isoformat()} is not due yet: it maps to note date "
                f"{backfill_note_date.isoformat()}, after {reference_date.isoformat()}. The scheduled run will process it."
            )

        preferences = load_preferences(config.preferences_file)
        prompt_logger = PromptLogger(config.logs_root / "debug")
        prompt_logger.clear()
        generation_service = GenerationService(
            config=config.summary_llm,
            tag_categories=preferences.categories,
            prompt_logger=prompt_logger,
        )
        fetcher = ArxivFetcher(page_size=config.arxiv_page_size)

        last_completed_announcement_date = state_store.load_completed_announcement_date()
        snapshot_listings = _load_listing_snapshots(
            state_store,
            preferences.categories,
            _snapshot_lookup_dates(
                invocation_date,
                last_completed_announcement_date=last_completed_announcement_date,
                backfill=backfill,
            ),
        )
        fetcher.seed_listings(snapshot_listings)
        snapshot_dates = {day for day_to_ids in snapshot_listings.values() for day in day_to_ids}
        feed_dates = fetcher.load_announcement_feed(preferences.categories)
        _save_listing_snapshots(state_store, fetcher, preferences.categories, feed_dates, source="rss")
        backfill_date = invocation_date if backfill else None
        listed_dates = snapshot_dates | set(feed_dates)
        gap_dates = _listing_gap_dates(
            listed_dates,
            last_completed_announcement_date=last_completed_announcement_date,
            backfill_date=backfill_date,
        )

        # RSS alone can't answer a gap or a wholly unusable feed; /list is the
        # gap-fill path for both. A backfill only needs /list for a date nothing
        # lists yet, and otherwise the feed day is enough on its own.
        gap_fallback_ran = bool(gap_dates) or (not backfill and not feed_dates)
        # The announcement days the fallback's pages showed; () when it failed.
        recent_listing_dates = (
            fetcher.load_recent_listings(preferences.categories, tuple(gap_dates)) if gap_fallback_ran else None
        )

        available_dates = list(fetcher.available_announcement_dates(preferences.categories))
        _save_listing_snapshots(state_store, fetcher, preferences.categories, recent_listing_dates or (), source="list")
        if not backfill and not available_dates:
            raise RuntimeError(
                f"No announcement listing available: the arXiv RSS feed for {', '.join(preferences.categories)} "
                "was unusable and the recent-listing fallback failed."
            )

        # A gap day older than the window the fallback's pages showed was never covered;
        # one inside it but absent had no announcement (e.g. a holiday). A failed
        # fallback showed no window, so every gap day is unfilled.
        unfilled_gap_dates: list[date] = []
        if recent_listing_dates is not None and not backfill:
            window_start = min(recent_listing_dates, default=None)
            unfilled_gap_dates = [day for day in gap_dates if window_start is None or day < window_start]

        listing_summary: dict[str, object] = {
            "feed_announcement_dates": [day.isoformat() for day in feed_dates],
            "snapshot_announcement_dates": [day.isoformat() for day in sorted(snapshot_dates)],
            "listing_gap_dates": [day.isoformat() for day in gap_dates],
            "listing_gap_fallback": (
                "not_needed" if recent_listing_dates is None else ("filled" if recent_listing_dates else "failed")
            ),
        }
        overall_summary.update(listing_summary)

        if backfill:
            pending_dates = [invocation_date]
            note_date_map = {invocation_date: backfill_note_date}
            ready_dates = pending_dates
        else:
            pending_dates = _pending_announcement_dates(
                available_dates,
                last_completed_announcement_date=last_completed_announcement_date,
            )
            if config.shift_announcements_to_next_weekday:
                note_date_map = {
                    announcement_date: _next_weekday(announcement_date)
                    for announcement_date in pending_dates
                }
            else:
                note_date_map = _note_dates_for_pending(invocation_date, pending_dates)
            ready_dates = [
                announcement_date
                for announcement_date in pending_dates
                if note_date_map[announcement_date] <= invocation_date
            ]

        _populate_run_summary_dates(
            overall_summary,
            available_dates=available_dates,
            pending_dates=pending_dates,
        )

        if backfill and invocation_date not in available_dates:
            if listing_summary["listing_gap_fallback"] == "failed":
                raise ValueError(
                    f"Announcement date {invocation_date.isoformat()} could not be backfilled: the "
                    "recent-listing fetch failed on both export.arxiv.org and arxiv.org. Retry this backfill "
                    "later once arXiv's recent-listing page is reachable again."
                )
            raise ValueError(
                f"Announcement date {invocation_date.isoformat()} is not visible in the current arXiv recent window."
            )

        if not pending_dates:
            LOGGER.info("No new announcement day is available to process.")
            state_store.save_run_summary(overall_summary, label="overall")
            return 0
        if not ready_dates:
            if unfilled_gap_dates:
                # Nothing is processed, so the marker stays put and the next
                # run retries the gap fill; only warn that it is still pending.
                LOGGER.warning(
                    "Recent-listing fallback did not cover missed announcement day(s) %s to %s; no feed day is "
                    "ready to process yet, so they stay pending and the next run will retry the gap fill.",
                    unfilled_gap_dates[0].isoformat(),
                    unfilled_gap_dates[-1].isoformat(),
                )
            LOGGER.info("No pending announcement day maps to a note date on or before %s.", invocation_date.isoformat())
            state_store.save_run_summary(overall_summary, label="overall")
            return 0
        if unfilled_gap_dates:
            _warn_unfilled_listing_gap(unfilled_gap_dates)

        dates_to_process = ready_dates
        for announcement_date in dates_to_process:
            note_date = note_date_map[announcement_date]
            exit_code = _run_announcement_day(
                config,
                invocation_date=invocation_date,
                reference_date=reference_date,
                announcement_date=announcement_date,
                note_date=note_date,
                available_dates=available_dates,
                pending_dates=pending_dates,
                preferences=preferences,
                note_manager=note_manager,
                state_store=state_store,
                generation_service=generation_service,
                fetcher=fetcher,
                listing_summary=listing_summary,
            )
            if exit_code != 0:
                return exit_code

        return 0
    except Exception as error:
        LOGGER.exception("Fatal pipeline error for %s", invocation_date.isoformat())
        overall_summary["fatal_error"] = str(error)
        state_store.save_run_summary(overall_summary, label="overall-fatal")
        return 1
