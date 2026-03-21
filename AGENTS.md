# AGENTS.md

## Project Summary

- GitHub repo name: `research-assistant`
- Local package / CLI name: `re-ass`
- Purpose: fetch relevant arXiv papers, generate notes, and update an Obsidian vault

## Core Commands

- Install deps: `uv sync --group dev`
- Run tests: `uv run pytest`
- Run today: `uv run re-ass`
- Backfill a day: `uv run re-ass --date YYYY-MM-DD`

## Important Files

- `re_ass.toml`: main runtime configuration
- `obsidian_vault/re-ass-preferences.md`: ranked preferences and arXiv categories
- `src/re_ass/`: application code
- `docs/`: assumptions, reports, and follow-up notes

## Working Notes

- Keep changes simple and explicit.
- Prefer deterministic fallbacks over silent failure.
- Store simulation or retained runtime artifacts under `archive/`.
- Current known external blocker: Claude `/summarise-paper` requests WebFetch permission in this environment, so paper-note generation falls back locally unless that is resolved.
