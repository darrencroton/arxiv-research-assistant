# AGENTS.md

## Project Summary

- GitHub repo name: `research-assistant`
- Local package / CLI name: `re-ass`
- Purpose: fetch relevant arXiv papers, generate Markdown summaries, and maintain daily/weekly outputs with explicit retained state
- Ranking architecture: small-pool direct selection, otherwise hybrid retrieval + local reranking + final LLM selection

## Core Commands

- Install deps: `uv sync --group dev`
- Run tests: `uv run pytest`
- Run today: `uv run re-ass`
- Backfill a day: `uv run re-ass --date YYYY-MM-DD`

## Important Files

- `user_preferences/defaults/settings.toml`: tracked default runtime configuration
- `user_preferences/defaults/preferences.md`: tracked default ranked preferences and optional `Top papers: X` output preference
- `user_preferences/defaults/daily-note-template.md`: tracked default daily note template with managed markers
- `user_preferences/defaults/weekly-note-template.md`: tracked default weekly note template with managed markers
- `user_preferences/`: gitignored mutable local copies created by setup/bootstrap
- `scripts/setup.sh`: first-time local bootstrap
- `scripts/launchd/`: public-safe launchd template and renderer
- `output/`, `processed/`, `state/`, `logs/`: active runtime directories
- `tmp/`: local scratch/debug output, never committed
- `src/re_ass/preferences.py`: Markdown preference parsing, including output-count preferences
- `src/re_ass/ranking.py`: retrieval, fusion, reranking, and final-selection pipeline
- `src/re_ass/paper_summariser/`: upstream-derived paper-note pipeline
- `src/re_ass/`: application code around ranking, orchestration, state, and note updates
- `docs/`: assumptions, reports, and follow-up notes

## Working Notes

- Keep changes simple and explicit.
- Prefer deterministic fallbacks over silent failure.
- Store simulation or retained runtime artifacts under `archive/`.
- Keep the paper-note path upstream-first: adapt at the app boundary instead of rewriting the provider/extraction stack.
- Paper identity is stable and arXiv-derived; do not fall back to title-based duplicate suppression.
- `user_preferences/preferences.md` defaults to 3 saved papers; users may request 1-10 papers there, capped by `user_preferences/settings.toml` `[arxiv].max_papers`.
- Candidate pools of 50 or fewer should be sent wholesale to the final selector without retrieval truncation.
- `scripts/setup.sh` and `GenerationService` should fail early when the configured CLI provider is present but not authenticated for non-interactive use.
- Gemini CLI support in this repo is for API-key or Vertex-AI-backed automation credentials, not piggybacked interactive OAuth.
- Daily and weekly summary updates must stay inside managed markers.
- Explicit `--date` backfills must not rotate or rewrite the current weekly summary.
- `state/papers/*.json` is the authoritative completion record; note or PDF presence alone is not.
- `state/runs/*.json` should remain audit-friendly and include retrieval, rerank, and final-selection diagnostics.
