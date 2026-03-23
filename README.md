# ArXiv Research Assistant (`re-ass`)

`research-assistant` fetches recent arXiv papers, ranks them against your interests, and writes:

- paper summaries to `output/summaries/`
- daily notes to `output/daily-notes/`
- a rolling weekly note to `output/weekly-notes/`
- downloaded PDFs to `output/pdfs/`

## Requirements

- `uv` on `PATH`
- one configured LLM provider

## New User Guides

If you are setting this up for the first time, start with these guides:

- [Automation with `launchd`](scripts/launchd/README.md)
- [Custom daily and weekly templates](user_preferences/templates/README.md)

## Setup

Run:

```bash
./scripts/setup.sh
```

This installs dependencies, creates the working directories, creates your local configuration files in `user_preferences/`, and checks that the selected provider is ready to use.

The default provider is `claude`. If you want a different provider, edit `user_preferences/settings.toml` after setup.

Provider setup:

- `claude`: run `claude auth login`
- `codex`: run `codex login`, or `printenv OPENAI_API_KEY | codex login --with-api-key`
- `copilot`: run `copilot login`, or set `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`
- `gemini`: set `GEMINI_API_KEY`, or use Vertex AI credentials
- API providers: set the required API key before running the app

For automation, your provider must already be authenticated for non-interactive use.

## Configure Before First Run

Edit these files after setup:

- `user_preferences/settings.toml`: provider, output paths, note settings
- `user_preferences/preferences.md`: arXiv categories, topics to prioritise, number of top papers

Decide where you want your notes to live:

- keep the defaults and let `re-ass` write into `output/`
- point output paths at folders inside your vault or notes directory
- or symlink `output/` subdirectories into your vault

Template options:

- `user_preferences/templates/daily-note-template.md`: default daily note template
- `user_preferences/templates/weekly-note-template.md`: default weekly note template
- or point `[templates]` in `user_preferences/settings.toml` at your own template files anywhere on disk

If you want to bring your own note templates, read [Custom daily and weekly templates](user_preferences/templates/README.md) before your first run. The app updates exact heading sections, so template structure matters.

## First Manual Run

Run a manual test before setting up automation:

```bash
uv run re-ass
```

This should:

- fetch new arXiv candidates for the current interval
- rank and summarise the selected papers
- write or update the daily note
- write or update the rolling weekly note
- write diagnostics under `state/runs/`

Backfill a specific day:

```bash
uv run re-ass --date 2026-03-21
```

An explicit `--date` backfill updates that day's outputs without rotating the current weekly note.

## Automation

The repo includes a macOS `launchd` template and renderer in `scripts/launchd/`, but automation is not installed automatically for you.

For setup, installation, schedule customisation, and troubleshooting, see:

- [Automation with `launchd`](scripts/launchd/README.md)

## Files

```text
output/
  summaries/      paper summaries
  daily-notes/    daily notes
  weekly-notes/   current weekly note and archives
  pdfs/           downloaded PDFs
state/
  papers/         per-paper completion records
  runs/           per-run diagnostics
logs/
  history.log
  last-run.log
user_preferences/
  settings.toml               your configuration
  preferences.md             your categories and priorities
  defaults/                  repo default settings and preferences
  templates/                 built-in daily and weekly templates
```

## Configuration

Main config: `user_preferences/settings.toml`

- `[output]`: where summaries, notes, and PDFs are written
- `[templates]`: which daily and weekly templates to use
- `[preferences]`: which preferences file to read
- `[notes]`: link style, weekly filename, rotation day, archive naming
- `[arxiv]`: limits, categories, and ranking pool sizes
- `[llm]`: provider and model settings

`[llm]` also supports an optional `effort` setting for CLI providers:

- `effort = ""`: use the provider default
- `effort = "low"`, `"medium"`, or `"high"`: set reasoning effort for `claude`, `codex`, or `copilot`
- `gemini` currently ignores `effort` and logs a warning

In `user_preferences/preferences.md`, you can optionally set the number of papers to save:

```markdown
## Output
- Top papers: 5
```

If omitted, the app saves 3 papers by default.

## Obsidian

If you use Obsidian:

- keep your templates in the vault and point `[templates]` in `user_preferences/settings.toml` at them
- either point `[output]` directories directly into the vault or symlink `output/summaries/`, `output/daily-notes/`, and `output/weekly-notes/` into the vault
- keep `notes.link_style = "wikilink"` for Obsidian-style links, or switch to `markdown` for relative Markdown links

## Templates

The app updates specific sections in your note templates by exact heading text.

Daily note templates support:

- `{{date}}` for the ISO run date, for example `2026-03-23`
- `{{date:...}}` for Moment-like date formatting via Pendulum, for example `{{date:dddd Do MMMM YYYY}}`

Required headings:

- daily template: `##  TODAY'S TOP PAPER`
- weekly template: `## SYNTHESIS`
- weekly template: `## DAILY ADDITIONS`

The first `#` heading in the weekly template is also rewritten to include the current week range.

Content outside those managed sections is left unchanged. For full examples and common mistakes, see:

- [Custom daily and weekly templates](user_preferences/templates/README.md)

## Troubleshooting

- If `./scripts/setup.sh` fails, your provider is usually not configured for non-interactive use yet.
- If a scheduled run does not behave as expected, check `logs/last-run.log`, `logs/launchd.stdout.log`, and `logs/launchd.stderr.log`.
- If `re-ass` writes its paper section at the end of a note instead of where you wanted it, your template heading does not exactly match the required heading text.
- Machine-readable diagnostics are written to `state/runs/`.

## Validation

```bash
uv run pytest
```
