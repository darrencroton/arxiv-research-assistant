# AGENTS.md

## Project Summary

- GitHub repo name: `arxiv-research-assistant`
- Local package / CLI name: `re-ass`
- Purpose: fetch relevant arXiv papers, generate Markdown summaries, and maintain daily/weekly outputs with explicit retained state
- Ranking architecture: one full-pool LLM ranking pass over fetched candidates, then deterministic thresholding and capping in app code

## Core Commands

- Install deps: `uv sync --group dev`
- Run tests: `uv run pytest`
- Run today: `uv run re-ass`
- Backfill a day: `uv run re-ass --date YYYY-MM-DD`

## Important Files

- `user_preferences/defaults/settings.toml`: tracked default runtime configuration
- `user_preferences/defaults/preferences.md`: tracked default ranked preferences
- `user_preferences/templates/daily-note-template.md`: tracked default daily note template with managed markers
- `user_preferences/templates/weekly-note-template.md`: tracked default weekly note template with managed markers
- `user_preferences/`: local config plus tracked defaults/templates
- `scripts/setup.sh`: first-time local bootstrap
- `scripts/launchd/`: public-safe launchd template and renderer
- `output/`, `state/`, `logs/`: active runtime directories (`state/papers`, `state/runs`, `state/listings` for write-once per-day listing snapshots; `output/summaries`, `output/daily-notes`, `output/weekly-notes`, `output/pdfs`; `logs/debug/` for LLM prompt debug output (numbered files per run); `logs/launchd/` for rendered plists)
- `src/re_ass/preferences.py`: Markdown preference parsing for categories and flat or science/method priority sections
- `src/re_ass/ranking.py`: full-pool LLM ranking and deterministic threshold/cap selection
- `src/re_ass/arxiv_rate_limit.py`: shared crawl-delay clock, retry schedule (`RETRY_DELAYS_SECONDS`), default request headers, response decoding (`decode_response_text`), and HTTP-error description (`describe_http_error`) for every arxiv-host request (RSS feed, `/list` gap fill, abstract pages, PDFs); the separate `arxiv.Client` metadata path has its own delay/retry and does not use this module.
- `src/re_ass/arxiv_fetcher.py`: RSS-first announcement listing (`load_announcement_feed`) with the `/list` recent-listing page as gap fill (`load_recent_listings`, `export.arxiv.org` first then `arxiv.org`), snapshot seeding (`seed_listings`, `listing_for_day`), plus per-paper candidate metadata via `arxiv.Client` with an abstract-page fallback.
- `src/re_ass/paper_summariser/`: upstream-derived paper-note pipeline
- `src/re_ass/`: application code around ranking, orchestration, state, and note updates

## Pipeline Flow

`main.py` (CLI entry) → `pipeline.py` (orchestration) → `arxiv_fetcher.py` (RSS listing, /list gap fill, candidate metadata) → `ranking.py` (LLM rank + threshold/cap) → `note_manager.py` (daily/weekly note updates) → `state_store.py` (completion records)

Supporting: `settings.py` (config loading), `preferences.py` (user preference parsing), `paper_summariser/` (PDF download + extraction), `generation_service.py` (LLM provider abstraction and `make_provider` helper), `models.py` (shared data types)

## Environment

- Python >=3.13
- First run: `scripts/setup.sh` (creates local config from defaults)
- LLM provider credentials: configured via `user_preferences/settings.toml`; provider-specific API keys or CLI auth as needed
- No linting/formatting tooling is configured

## Working Notes

- Keep changes simple and explicit.
- Prefer deterministic fallbacks over silent failure.
- Store simulation or retained runtime artifacts under `archive/`.
- Keep the paper-note path upstream-first: adapt at the app boundary instead of rewriting the provider/extraction stack.
- Paper identity is stable and arXiv-derived; do not fall back to title-based duplicate suppression.
- `user_preferences/preferences.md` should contain categories plus priorities only; users can keep a single ordered list or split priorities into `Science` and `Methods`, with strong fits requiring one hit from each section when both are present.
- `scripts/setup.sh` and `GenerationService` must fail early when the configured CLI provider is present but not authenticated for non-interactive use. Gemini CLI support is for API-key or Vertex-AI-backed automation credentials only, not interactive OAuth.
- Daily and weekly summary updates must stay inside managed markers.
- Standard runs map arXiv announcement days to note dates using `[notes].shift_announcements_to_next_weekday` (default `true`: next local weekday note, with Friday landing on Monday). Catch-up fills visible pending batches according to that mapping, deferring batches whose mapped note date is still in the future and updating the relevant weekly notes (including archived prior-week notes across weekly boundaries). Catch-up covers the current RSS announcement day plus, when a gap needs filling, whatever the `/list` recent-listing window or a saved listing snapshot still shows.
- Explicit `--date` backfills exist to recover missed days, so they process exactly that announcement day and place it where a standard run would have: the note date is the next local weekday when `shift_announcements_to_next_weekday` is on (else the day itself), and both that daily note and the weekly note for that date are updated (the current one, or the archived one for a past week). A backfill whose note date is after today is refused (the scheduled run will process it). Backfills also rotate the weekly note first, against today (`run(..., today=)` is the test hook), so a past-week write never creates an archive that a later rotation would collide with. Weekly day blocks are kept in week order, so a backfilled earlier day lands before later ones. `pipeline._publish_weekly_note` is the shared weekly write; the synthesis word budget follows the latest day the note's week can hold as of the reference date. A day already covered by a listing snapshot needs no `/list` request (the one RSS request is still made).
- `state/papers/*.json` is the authoritative completion record; note or PDF presence alone is not.
- `state/listings/<YYYY-MM-DD>.json` snapshots (`StateStore.save_listing_snapshot`, `load_listing_snapshot` per day) record every announcement day's per-category ids, from the RSS feed (`source: rss`) or `/list` (`source: list`), so a day the feed has rolled past stays recoverable offline once a completed marker exists (a failed very first run is out of scope). They are write-once (first source wins), record empty categories as `[]`, and a snapshot missing any currently configured category is ignored so that day is refetched; unreadable files are skipped with a WARNING and save failures only warn. `pipeline._snapshot_lookup_dates` bounds what a run loads: the backfill day, or every weekday after the completed marker up to the invocation date, and nothing on a first run (no marker). So snapshot days on or before the marker never become pending, ones after it that a run did not process do, and history does not accumulate in the run's date lists. `snapshot_announcement_dates` in the run summary lists only the days actually loaded.
- `state/runs/*.json` should remain audit-friendly and include full ranking plus final-selection diagnostics.
- `[llm]` is the base LLM config used for both ranking and summarisation. Optional `[llm-ranking]` and `[llm-summary]` sections override only the fields that differ; absent sections reuse the base `LlmConfig` object (same identity). `AppConfig` exposes `.llm`, `.ranking_llm`, and `.summary_llm`; pipeline code uses `.ranking_llm` for `PaperRanker` and `.summary_llm` for `GenerationService`.
- The announcement listing source is arXiv's sanctioned RSS feed (`rss.arxiv.org/rss/<cat>[+<cat>...]`): one request lists the latest announcement day across all configured categories at once, and only its `new`/`cross` items are counted (`replace`/`replace-cross` are excluded).
- `pipeline._listing_gap_dates` (pure, pipeline-side) decides whether a weekday between the last completed marker and the RSS day was missed; when it was, or the feed is unusable, `ArxivFetcher.load_recent_listings` fills the gap from `/list` (all-or-nothing across categories; it returns the announcement days its pages showed, `()` on failure). The gap is computed against every known listing day (feed plus snapshots), so days already snapshotted are never refetched. If that fallback also fails, `pipeline._warn_unfilled_listing_gap` logs one WARNING with the missed dates and the `uv run re-ass --date ...` commands to recover them, and the run continues with the RSS day; a gap day older than the oldest day `/list` showed counts as unfilled and warns the same way, while one inside that range but absent is a no-announcement day and does not; the completed-announcement marker still advances past the missed dates. When no feed day is ready yet (its note date is still in the future), nothing is processed, the marker stays put, and the gap stays pending for the next run. If RSS and `/list` both fail outright (nothing loads at all), the run exits 1 with the marker untouched, and the next scheduled run's catch-up picks it up; a `--date` backfill uses a snapshot if it has that day, else tries RSS then `/list` the same way; a first run with no marker yet processes only the current feed day (no gap check).
- `/list` is robots-permitted (`Allow: /list`, `Crawl-delay: 15`) but the interactive main site (`arxiv.org`) is subject to arXiv-side 406 windows that are not header-related and can't be waited out within a single run, so `/list` is used only as gap fill, never as the primary listing source. Gap fill tries `export.arxiv.org/list/<cat>/pastweek` first and `arxiv.org` second, per category; a page is accepted only if it fetched, parsed to at least one day, and its newest listed day is not older than the newest date needed (export's copy can lag for the newest day). Freshness is judged on the newest day shown, not on the needed day being present, because a category can have no papers on a day.
- Abstract-page fallback fetches (`arxiv_fetcher._fetch_abstract_html`) and PDF downloads (`paper_summariser.service.build_pdf_url`) use `export.arxiv.org`, arXiv's site "specifically set aside for programmatic access" (<https://info.arxiv.org/help/bulk_data.html>); the `/list` gap fill also prefers `export.arxiv.org`, guarded by the staleness check above. OAI-PMH's per-category, date-filtered harvesting is not a substitute for `/list` either, since a date there matches any record touched that day (including revisions and new cross-listings), not only papers newly announced that day.
- All arxiv-host HTTP (RSS feed, `/list`, abstract pages, PDFs) shares one crawl-delay clock via `arxiv_rate_limit.py`; the RSS feed, `/list`, and PDF downloads also retry on a transient HTTP status per that module's schedule, while the abstract-page fetch makes a single attempt since it is itself a fallback. Candidate metadata queries (`arxiv.Client`, hitting `export.arxiv.org/api/query`) are a separate path with their own built-in delay/retry (`num_retries=3, delay_seconds=3`), not this limiter.
