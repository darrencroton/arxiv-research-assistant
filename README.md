# ArXiv Research Assistant (`re-ass`)

`research-assistant` fetches recent arXiv papers, ranks them against your interests, and writes:

- paper summaries to `output/summaries/`
- daily notes to `output/daily-notes/`
- a rolling weekly note to `output/weekly-notes/`
- downloaded PDFs to `output/pdfs/`

## Requirements

- `uv` on `PATH`
- one configured LLM provider

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

## First Run

Edit these files first:

- `user_preferences/settings.toml`: provider, output paths, note settings
- `user_preferences/preferences.md`: arXiv categories, topics to prioritise, number of top papers

Optional template overrides:

- `user_preferences/templates/daily-note-template.md`: default daily note template
- `user_preferences/templates/weekly-note-template.md`: default weekly note template
- or point `[templates]` in `user_preferences/settings.toml` at your own template files

Then run:

```bash
uv run re-ass
```

Backfill a specific day:

```bash
uv run re-ass --date 2026-03-21
```

An explicit `--date` backfill updates that day's outputs without rotating the current weekly note.

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

In `user_preferences/preferences.md`, you can optionally set the number of papers to save:

```markdown
## Output
- Top papers: 5
```

If omitted, the app saves 3 papers by default.

## Obsidian

If you use Obsidian:

- symlink `output/summaries/`, `output/daily-notes/`, or `output/weekly-notes/` into your vault
- point `[templates]` in `user_preferences/settings.toml` at template files in your vault
- keep `notes.link_style = "wikilink"` for Obsidian-style links, or switch to `markdown` for relative Markdown links

## Templates

The app only edits content inside managed markers.

- daily marker: `re-ass:daily-top-paper`
- weekly markers: `re-ass:weekly-synthesis`, `re-ass:weekly-daily-additions`

Content outside those markers is left unchanged.

## Validation

```bash
uv run pytest
```
