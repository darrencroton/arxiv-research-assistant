# research-assistant

`research-assistant` is a local arXiv workflow that produces generic Markdown outputs.

The Python package and CLI are still named `re-ass`.

## Prerequisite

Install `uv` and make sure it is on `PATH`.

## What It Does

- reads ranked interests from `preferences.md`
- fetches recent arXiv papers and ranks them against those interests
- generates paper summaries under `output/papers/`
- updates daily and weekly summaries under `output/daily/` and `output/weekly/`
- retains processed PDFs under `processed/`
- records explicit machine state under `state/`
- writes local run logs under `logs/`
- supports historical backfill with `--date`
- keeps the upstream-derived paper summariser vendored under `src/re_ass/paper_summariser/`

## Fresh Install

```bash
./scripts/setup.sh
```

That script:

- installs project dependencies with `uv`
- creates the local runtime directories used by the app
- creates `tmp/` for local scratch output such as prompt-debug files and rendered launchd assets

## Run

```bash
uv run pytest
uv run re-ass
```

Edit `preferences.md` before your first real run so ranking uses your own categories and priorities.

Backfill a specific day:

```bash
uv run re-ass --date 2026-03-21
```

Explicit `--date` backfills write the paper summaries and that day's daily summary, but leave the current weekly summary unchanged.

## Runtime Layout

```text
output/
  papers/      generated paper summaries
  daily/       daily summaries
  weekly/      current weekly summary plus weekly archives
processed/     retained PDFs for completed papers
state/
  papers/      per-paper JSON records
  runs/        per-run JSON summaries
logs/
  history.log
  last-run.log
tmp/
  paper_summariser/
  launchd/
templates/
  daily-note-template.md
  weekly-note-template.md
preferences.md
```

## Obsidian Integration

The output layer is generic Markdown, but Obsidian is still the main expected consumer.

- Symlink `output/papers/`, `output/daily/`, or `output/weekly/` into your vault if you want the generated summaries to appear there directly.
- Point `[templates]` in `re_ass.toml` at template files inside your vault, or symlink `templates/*.md` to your Obsidian template files.
- `notes.link_style` defaults to `wikilink`. Set it to `markdown` if you want relative Markdown links instead.

## Configuration

Main config lives in `re_ass.toml`.

- `[output]` controls the user-facing Markdown directories.
- `[processed]`, `[state]`, and `[logs]` control retained artifacts, machine state, and diagnostics.
- `tmp/` is used for local scratch/debug output and is never committed.
- `[templates]` points at the daily and weekly template files.
- `[preferences]` points at `preferences.md`.
- `[notes]` controls link style, weekly summary filename, weekly rotation day, and archive naming.
- `[llm]` controls summarisation and synthesis generation.

Supported providers:

- CLI: `claude`, `codex`, `gemini`, `copilot`
- API: `claude`, `openai`, `gemini`, `perplexity`, `ollama`

`llm.enabled = false` by default. When disabled, `re-ass` still produces deterministic fallback paper summaries, micro-summaries, and weekly synthesis.

## Templates

The app edits summary files only inside managed markers.

- Daily template marker: `re-ass:daily-top-paper`
- Weekly template markers: `re-ass:weekly-synthesis`, `re-ass:weekly-daily-additions`

Content outside those markers is preserved untouched.

## State And Logs

- `state/papers/*.json` is the authoritative duplicate-suppression record.
- `state/runs/*.json` stores per-run summaries, including zero-new-paper runs.
- `logs/history.log` is append-only.
- `logs/last-run.log` is replaced on every run.
- `tmp/paper_summariser/prompt.txt` is the optional prompt-debug artifact when enabled.

## Launchd

Render a local plist from the public template:

```bash
./scripts/launchd/render-plist.sh
```

This writes a machine-local plist to `tmp/launchd/com.user.re-ass.plist` using your actual repo path and `uv` binary path, without committing either.

## Validation

```bash
uv run python -m compileall src tests
uv run pytest
```
