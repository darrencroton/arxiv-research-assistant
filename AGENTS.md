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
- `output/`, `state/`, `logs/`: active runtime directories (`output/summaries`, `output/daily-notes`, `output/weekly-notes`, `output/pdfs`)
- `tmp/`: local scratch/debug output, never committed
- `src/re_ass/preferences.py`: Markdown preference parsing for categories and flat or science/method priority sections
- `src/re_ass/ranking.py`: full-pool LLM ranking and deterministic threshold/cap selection
- `src/re_ass/paper_summariser/`: upstream-derived paper-note pipeline
- `src/re_ass/`: application code around ranking, orchestration, state, and note updates
- `docs/`: assumptions, reports, and follow-up notes

## Working Notes

- Keep changes simple and explicit.
- Prefer deterministic fallbacks over silent failure.
- Store simulation or retained runtime artifacts under `archive/`.
- Keep the paper-note path upstream-first: adapt at the app boundary instead of rewriting the provider/extraction stack.
- Paper identity is stable and arXiv-derived; do not fall back to title-based duplicate suppression.
- `user_preferences/preferences.md` should contain categories plus priorities only; users can keep a single ordered list or split priorities into `Science` and `Methods`, with strong fits requiring one hit from each section when both are present.
- `scripts/setup.sh` and `GenerationService` should fail early when the configured CLI provider is present but not authenticated for non-interactive use.
- Gemini CLI support in this repo is for API-key or Vertex-AI-backed automation credentials, not piggybacked interactive OAuth.
- Daily and weekly summary updates must stay inside managed markers.
- Standard automatic runs are "reading note" runs: fetch the newest visible arXiv announcement batch and write it into today's daily note, even when the underlying arXiv announcement date is earlier.
- Automatic catch-up after missed runs is still relative to today's note: the newest visible pending batch goes into today's daily note, then older pending batches backfill earlier weekday daily notes working backwards from today, skipping weekends.
- Automatic catch-up should update whichever weekly notes those backfilled daily notes belong to, including archived prior-week notes when a catch-up spans a weekly boundary.
- Explicit `--date` backfills are surgical single-day pulls: process exactly that announcement day, write/update that date's daily note, and do not rotate or rewrite the current weekly summary.
- Automatic catch-up only covers announcement days still visible in arXiv recent listings; older recovery requires an explicit single-day backfill if the day is still visible at all.
- `state/papers/*.json` is the authoritative completion record; note or PDF presence alone is not.
- `state/runs/*.json` should remain audit-friendly and include full ranking plus final-selection diagnostics.
