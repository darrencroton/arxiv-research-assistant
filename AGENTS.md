# AGENTS.md

## Project Summary

- GitHub repo name: `research-assistant`
- Local package / CLI name: `re-ass`
- Purpose: fetch relevant arXiv papers, generate Markdown summaries, and maintain daily/weekly outputs with explicit retained state

## Core Commands

- Install deps: `uv sync --group dev`
- Run tests: `uv run pytest`
- Run today: `uv run re-ass`
- Backfill a day: `uv run re-ass --date YYYY-MM-DD`

## Important Files

- `re_ass.toml`: main runtime configuration
- `preferences.md`: ranked preferences and arXiv categories
- `templates/daily-note-template.md`: daily note template with managed markers
- `templates/weekly-note-template.md`: weekly note template with managed markers
- `scripts/setup.sh`: first-time local bootstrap
- `scripts/launchd/`: public-safe launchd template and renderer
- `output/`, `processed/`, `state/`, `logs/`: active runtime directories
- `tmp/`: local scratch/debug output, never committed
- `src/re_ass/paper_summariser/`: upstream-derived paper-note pipeline
- `src/re_ass/`: application code around ranking, orchestration, state, and note updates
- `docs/`: assumptions, reports, and follow-up notes

## Working Notes

- Keep changes simple and explicit.
- Prefer deterministic fallbacks over silent failure.
- Store simulation or retained runtime artifacts under `archive/`.
- Keep the paper-note path upstream-first: adapt at the app boundary instead of rewriting the provider/extraction stack.
- Paper identity is stable and arXiv-derived; do not fall back to title-based duplicate suppression.
- Daily and weekly summary updates must stay inside managed markers.
- Explicit `--date` backfills must not rotate or rewrite the current weekly summary.
- `state/papers/*.json` is the authoritative completion record; note or PDF presence alone is not.
