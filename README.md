# research-assistant

`research-assistant` is a local arXiv-to-Obsidian workflow.

The Python package and CLI are still named `re-ass`.

## Current Scope

- reads ranked interests from `obsidian_vault/re-ass-preferences.md`
- fetches and ranks matching arXiv papers
- updates daily and weekly Obsidian notes
- supports historical backfill with `--date`
- defaults to deterministic local paper-note generation
- can be wired to Claude `/summarise-paper` once the external permission issue is resolved

## Quick Start

```bash
uv sync --group dev
uv run pytest
uv run re-ass
```

Backfill a specific day:

```bash
uv run re-ass --date 2026-03-21
```

## Configuration

- Main config: `re_ass.toml`
- Preferences: `obsidian_vault/re-ass-preferences.md`
- Docs and reports: `docs/`
