# research-assistant (`re-ass`)

`research-assistant` is a local arXiv workflow that fetches in-range arXiv candidates, ranks them against your preferences, and writes upstream-style paper summaries plus daily and weekly notes.

## Prerequisite

Install `uv` and make sure it is on `PATH`.

## What It Does

- reads ranked interests from `user_preferences/preferences.md`
- fetches all in-range arXiv candidates for the configured categories
- for small candidate pools, sends the full metadata set directly to the final selector
- for larger candidate pools, uses hybrid retrieval, local reranking, and a final LLM selection stage
- generates paper summaries under `output/papers/` using the vendored summariser output directly
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
- validates the configured LLM provider prerequisites on this machine
- fails early if the configured CLI provider is installed but not authenticated or otherwise not ready for non-interactive use

Before running setup, choose a working provider in `user_preferences/defaults/settings.toml` if you want to change the tracked defaults, or just let setup bootstrap `user_preferences/settings.toml` and edit the user-local copy afterwards. The default config expects the `claude` CLI on `PATH` and already authenticated.

CLI provider auth/setup requirements:

- `claude`: run `claude auth login` first. If your Claude plan supports it, `claude setup-token` may also work for long-lived non-interactive auth.
- `codex`: run `codex login`, or save an API-key login with `printenv OPENAI_API_KEY | codex login --with-api-key`.
- `copilot`: run `copilot login`, or set `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`.
- `gemini`: for `re-ass`, use `GEMINI_API_KEY`, or Vertex AI credentials (`GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION`). Do not rely on interactive Google OAuth for this app.

If you switch to an API provider, set the required API key first.

## Run

```bash
uv run re-ass   # to run the research assistant with defaults
uv run pytest   # to run the test suite (debugging and verification)
```

Edit `user_preferences/preferences.md` before your first real run so ranking uses your own categories, priorities, and optional output settings.

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
user_preferences/
  settings.toml               mutable local settings (gitignored)
  preferences.md             mutable local preferences (gitignored)
  daily-note-template.md     mutable local daily template (gitignored)
  weekly-note-template.md    mutable local weekly template (gitignored)
  defaults/
    settings.toml
    preferences.md
    daily-note-template.md
    weekly-note-template.md
```

## Obsidian Integration

The output layer is generic Markdown, but Obsidian is still the main expected consumer.

- Symlink `output/papers/`, `output/daily/`, or `output/weekly/` into your vault if you want the generated summaries to appear there directly.
- Point `[templates]` in `user_preferences/settings.toml` at template files inside your vault, or symlink the files under `user_preferences/` to your Obsidian template files.
- `notes.link_style` defaults to `wikilink`. Set it to `markdown` if you want relative Markdown links instead.

## Configuration

Main config lives in `user_preferences/settings.toml`.

- `[output]` controls the user-facing Markdown directories.
- `[processed]`, `[state]`, and `[logs]` control retained artifacts, machine state, and diagnostics.
- `tmp/` is used for local scratch/debug output and is never committed.
- `[templates]` points at the daily and weekly template files.
- `[preferences]` points at `user_preferences/preferences.md`.
- `[notes]` controls link style, weekly summary filename, weekly rotation day, and archive naming.
- `[arxiv]` controls the hard maximum number of selected papers, arXiv page size, retrieval/rerank pool sizes, selection thresholds, and default categories.
- `[llm]` controls the mandatory provider used for reranking, paper summaries, and weekly synthesis.

`user_preferences/preferences.md` can optionally include:

```markdown
## Output
- Top papers: 5
```

If omitted, the app saves 3 papers by default. Preference values must be between 1 and 10 and are still capped by `[arxiv].max_papers`.

Current default ranking behavior:

- if a run produces 50 or fewer candidates, the full metadata set is sent to the final selector without retrieval truncation
- if a run produces more than 50 candidates, the app uses lexical retrieval, alias-aware semantic retrieval, reciprocal-rank fusion, local reranking, and then bounded final selection

Supported providers:

- CLI: `claude`, `codex`, `gemini`, `copilot`
- API: `claude`, `openai`, `gemini`, `perplexity`, `ollama`

`re-ass` requires a working configured provider. If the configured CLI binary or API credentials are missing, setup and runtime fail fast instead of writing degraded fallback paper notes.

CLI auth notes:

- `claude` and `codex` are preflighted with their built-in auth-status commands during setup and app startup.
- `copilot` uses best-effort readiness checks: token environment variables, `gh auth status`, and local credential-store probes.
- `gemini` is treated as an automation provider only when API-key or Vertex AI credentials are configured.

## Templates

The app edits summary files only inside managed markers.

- Daily template marker: `re-ass:daily-top-paper`
- Weekly template markers: `re-ass:weekly-synthesis`, `re-ass:weekly-daily-additions`

Content outside those markers is preserved untouched.

## State And Logs

- `state/papers/*.json` is the authoritative duplicate-suppression record.
- `state/runs/*.json` stores per-run summaries, including interval bounds, candidate counts, retrieval diagnostics, rerank scores, final-selection rationales, and selected paper IDs.
- `logs/history.log` is append-only.
- `logs/last-run.log` is replaced on every run.
- `tmp/paper_summariser/prompt.txt` stores the latest summariser prompt-debug artifact.

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
