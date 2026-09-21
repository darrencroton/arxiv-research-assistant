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
- `output/`, `state/`, `logs/`: active runtime directories (`output/summaries`, `output/daily-notes`, `output/weekly-notes`, `output/pdfs`; `logs/debug/` for LLM prompt debug output (numbered files per run); `logs/launchd/` for rendered plists)
- `src/re_ass/preferences.py`: Markdown preference parsing for categories and flat or science/method priority sections
- `src/re_ass/ranking.py`: full-pool LLM ranking and deterministic threshold/cap selection
- `src/re_ass/arxiv_rate_limit.py`: shared crawl-delay pacing and User-Agent for the listing, abstract-page, and PDF requests in `arxiv_fetcher.py`/`generation_service.py`, plus the retry schedules the listing fetch and PDF download each use on top of that pacing (see AGENTS.md's Working Notes for exactly which call sites retry and which don't; the separate `arxiv.Client` metadata path has its own delay/retry and does not use this module)
- `src/re_ass/paper_summariser/`: upstream-derived paper-note pipeline
- `src/re_ass/`: application code around ranking, orchestration, state, and note updates

## Pipeline Flow

`main.py` (CLI entry) → `pipeline.py` (orchestration) → `arxiv_fetcher.py` (fetch candidates) → `ranking.py` (LLM rank + threshold/cap) → `note_manager.py` (daily/weekly note updates) → `state_store.py` (completion records)

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
- Standard runs map arXiv announcement days to note dates using `[notes].shift_announcements_to_next_weekday` (default `true`: next local weekday note, with Friday landing on Monday). Catch-up fills visible pending batches according to that mapping, deferring batches whose mapped note date is still in the future and updating the relevant weekly notes (including archived prior-week notes across weekly boundaries). Catch-up only covers days still visible in arXiv recent listings.
- Explicit `--date` backfills are surgical: process exactly that announcement day into that date's daily note; do not touch the current weekly summary.
- `state/papers/*.json` is the authoritative completion record; note or PDF presence alone is not.
- `state/runs/*.json` should remain audit-friendly and include full ranking plus final-selection diagnostics.
- `[llm]` is the base LLM config used for both ranking and summarisation. Optional `[llm-ranking]` and `[llm-summary]` sections override only the fields that differ; absent sections reuse the base `LlmConfig` object (same identity). `AppConfig` exposes `.llm`, `.ranking_llm`, and `.summary_llm`; pipeline code uses `.ranking_llm` for `PaperRanker` and `.summary_llm` for `GenerationService`.
- arxiv.org/robots.txt declares `Crawl-delay: 15` under `User-agent: *` for `/list`, `/abs`, and `/pdf` alike. The listing fetch, abstract-page fallback fetch, and PDF download each go through `arxiv_rate_limit.py`'s shared limiter for crawl-delay pacing against one clock, regardless of which of the two domains (arxiv.org or export.arxiv.org) they land on. Candidate metadata queries (`arxiv.Client`, hitting `export.arxiv.org/api/query`) are a separate path with their own built-in delay/retry (`num_retries=3, delay_seconds=3`), not this limiter.
- Per <https://info.arxiv.org/help/bulk_data.html>, arXiv asks programmatic clients to use `export.arxiv.org` ("specifically set aside for programmatic access") rather than the interactive main site. Abstract-page fallback fetches (`arxiv_fetcher._fetch_abstract_html`) and PDF downloads (`paper_summariser.service.build_pdf_url`) do this; both serve the same content as their arxiv.org equivalents. The announcement-day listing fetch (`arxiv_fetcher._fetch_listing_html`) stays on arxiv.org because `export.arxiv.org`'s copy of `/list/{category}/pastweek` lags by days and cannot answer a "what's new" query; OAI-PMH's per-category, date-filtered harvesting (`oaipmh.arxiv.org/oai`) is not a substitute for it either, since a date there matches any record touched that day — including old papers revised or newly cross-listed — not only papers newly announced that day. See `archive/investigation-2026-09-22-export-arxiv-org-migration.md` for the evidence behind this domain split.
- The listing fetch is the one call site still exposed to arxiv.org's main-site bot mitigation. A 406 there clears within roughly an hour rather than persisting for days, so `_fetch_listing_html` retries on its own longer `LISTING_RETRY_DELAYS_SECONDS` schedule (up to ~17 minutes across a run) to give a single run a real chance to recover; `RETRY_DELAYS_SECONDS` (shorter) covers PDF downloads (`generation_service.stage_pdf_download`). The abstract-page fallback fetch (`arxiv_fetcher._fetch_abstract_html`) makes a single attempt with no retry of its own — it is itself a fallback path, reached only when the primary metadata query has already hit a transient error. Repeated rapid retries against an already-406'd resource still risk being read as an attack per arXiv's own robots.txt warning — so a stuck backlog of failed PDFs should be retried in a slow, spaced-out follow-up pass (or left for the next scheduled run), not forced through in one back-to-back sweep. There is no authenticated API/token for casual PDF access; arXiv's own guidance for a flagged legitimate use case is to contact the arXiv administrators in advance, not to request a key.
