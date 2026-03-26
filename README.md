# ArXiv Research Assistant (`re-ass`)

`re-ass` is a local arXiv discovery tool for researchers. It automatically fetches recent papers from selected arXiv categories, ranks them against your research priorities, and writes high quality Markdown summaries for the top ranked, direct to your Obsidian daily/weekly notes (or wherever you keep your research).

It uses the same core engine as [Science Paper Summariser](https://github.com/darrencroton/science-paper-summariser) to produce structured paper summaries, each with a glossary, tags, and full citations to the original paper (to ensure quality).

The default project knowledge is tuned for `astro-ph`, but can easily be adapted for other research fields.

## Quickstart

### Requirements

- Python `3.13+`
- `uv` on `PATH`
- one supported LLM provider

Supported providers:

- CLI mode: `claude`, `codex`, `copilot`, `gemini`
- API mode: `claude`, `openai`, `gemini`, `perplexity`, `ollama`

### 1. Run setup

```bash
./scripts/setup.sh
```

This installs dependencies, creates the working directories, and bootstraps your local config in `user_preferences/`.

The bootstrapped default provider is `claude`. If that is not the provider you plan to use, edit `user_preferences/settings.toml` after setup and then rerun `./scripts/setup.sh` to validate your chosen provider.

### 2. Edit `user_preferences/settings.toml`

This file controls:

- `[output]`: where summaries, notes, and PDFs are written
- `[templates]`: which daily and weekly templates to use
- `[preferences]`: which preferences file to read
- `[notes]`: link style, weekly filename, rotation day, archive naming, and managed headings
- `[arxiv]`: fetch limits, ranking threshold, and maximum selected papers
- `[llm]`: provider mode, provider name, model, and optional reasoning effort

Common provider setups:

- CLI `claude`: `mode = "cli"`, `provider = "claude"`, then run `claude auth login`
- CLI `codex`: `mode = "cli"`, `provider = "codex"`, then run `codex login`, or `printenv OPENAI_API_KEY | codex login --with-api-key`
- CLI `copilot`: `mode = "cli"`, `provider = "copilot"`, then run `copilot login`, or set `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`
- CLI `gemini`: `mode = "cli"`, `provider = "gemini"`, then set `GEMINI_API_KEY`, or use Vertex AI credentials
- API `claude`: `mode = "api"`, `provider = "claude"`, then set `ANTHROPIC_API_KEY`
- API `openai`: `mode = "api"`, `provider = "openai"`, then set `OPENAI_API_KEY`
- API `gemini`: `mode = "api"`, `provider = "gemini"`, then set `GOOGLE_API_KEY`
- API `perplexity`: `mode = "api"`, `provider = "perplexity"`, then set `PERPLEXITY_API_KEY`
- API `ollama`: `mode = "api"`, `provider = "ollama"`, and use `ollama_base_url` if you need a non-default local endpoint

Example:

```toml
[llm]
mode = "cli"
provider = "copilot"
model = "claude-sonnet-4.6"
effort = "high"
```

If you do not already have a Claude, Codex, or Gemini subscription, researchers and educators with an `.edu` email address may be eligible for [GitHub Education](https://github.com/education), which opens up higher-quality Copilot models through the GitHub Copilot CLI.

For scheduled automation, your chosen provider must already be authenticated for non-interactive use.

### 3. Edit `user_preferences/preferences.md`

This file defines your literature filter.

- `## Categories` is required and must contain arXiv categories as bullet items.
- Priorities must be written as numbered items.
- You can use either one flat `## Priorities` list or split priorities into `## Priorities - Science` and `## Priorities - Methods`.

Minimal complete example:

```markdown
# Arxiv Priorities

## Categories
- astro-ph.CO
- astro-ph.GA

## Priorities - Science
1. Little red dots, LRDs, and compact dusty red JWST sources at high redshift
2. Black holes and AGN in galaxies: SMBH growth, AGN triggering, AGN feedback, JWST AGN, merger-driven AGN; not GW-only MBH binary papers

## Priorities - Methods
1. Semi-analytic galaxy formation models: semi-analytic models, SAMs, L-Galaxies, SHARK, SAGE, the Somerville model, and model predictions
2. Large observational surveys: SDSS, DESI, HSC, LSST, Euclid, Roman, JWST legacy fields, wide-field multiwavelength surveys, survey catalogues, and statistically powerful survey samples
```

Priority-writing guidance:

- Keep each priority to one concrete line.
- Use the terms, aliases, instruments, surveys, and contexts you actually care about.
- Add a short exclusion when a topic has obvious near-misses.
- With the science/method split, one direct match in each section can be enough; multiple matches are a bonus.
- With a single flat `## Priorities` list, a strong direct match to one priority can be enough; multiple matches are a bonus.

### 4. Decide where your notes should live

You can:

- keep the defaults and let `re-ass` write into `output/`
- point `[output]` at folders inside your notes directory or vault
- symlink `output/` subdirectories into your notes directory or vault

If you want custom daily/weekly note templates, configure these before your first run (see below).

### 5. Run once manually

```bash
uv run re-ass
```

This should:

- discover unprocessed arXiv announcement days from the recent category listings
- fetch that day's candidates across all configured categories, then rank them as one combined pool
- rank and summarise the selected papers
- write or update the daily note ending at today, with catch-up days filling earlier note dates if needed
- write or update the rolling weekly note
- write one diagnostic JSON per processed announcement day under `state/runs/`

Backfill a specific day:

```bash
uv run re-ass --date 2026-03-21
```

An explicit `--date` backfill processes that announcement day directly without rotating the current weekly note.

## Directory Layout

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
  settings.toml               your local configuration
  preferences.md             your local categories and priorities
  defaults/                  tracked default settings and preferences
  templates/                 built-in daily and weekly templates
```

`state/papers/` is the authoritative completion record. Existing notes or PDFs alone do not mark a paper as completed.

Automatic runs are driven by arXiv announcement days, not by a rolling timestamp cursor:

- each configured category is discovered separately from its recent arXiv listing
- cross-listed papers are deduplicated by arXiv ID
- all candidates for an announcement day are ranked together in one pass
- if `re-ass` was down, the next run can catch up on the visible missed announcement days in order
- automatic catch-up fills weekday daily notes backwards from the run date, skipping weekends
- archived weekly notes are keyed by the week they describe, so catch-up days link back to the correct week file

## Ranking and Selection

`re-ass` scores every fetched candidate against the priorities in `user_preferences/preferences.md`, keeps papers at or above `[arxiv].min_selection_score`, and then caps the final selection at `[arxiv].max_papers`.

The categories section controls which arXiv feeds are fetched in the first place. The priorities section then tells the ranker what counts as a strong match within that pool.

## Templates and Obsidian

If you use Obsidian or another notes app:

- keep your templates in the vault and point `[templates]` at them
- point `[output]` directories directly into the vault, or symlink the generated directories into it
- keep `notes.link_style = "wikilink"` for Obsidian-style links, or switch to `markdown` for relative Markdown links

Template rules:

- the daily template must contain whatever `notes.daily_top_paper_heading` is set to
- the weekly template must contain whatever `notes.weekly_synthesis_heading` and `notes.weekly_additions_heading` are set to
- daily templates support `{{date}}` and `{{date:...}}`
- the first `#` heading in the weekly template is rewritten to include the current week range
- content outside managed sections is left unchanged

For full examples and common mistakes, see [Custom daily and weekly templates](user_preferences/templates/README.md).

## Automation

The repo includes a macOS `launchd` template and renderer in `scripts/launchd/`, but automation is optional and is not installed automatically.

Only install automation after a manual run succeeds. For setup, schedule customisation, and troubleshooting, see [Automation with `launchd`](scripts/launchd/README.md).

## Customisation

- For different research fields, update `user_preferences/preferences.md` so "Categories" fetches the right arXiv feeds, and "Priorities" best match the papers you are most interested in. You should probably also update the generated science-area tags to use a different vocabulary; see `src/re_ass/paper_summariser/project_knowledge/keywords.txt`.
- To change the structure of each paper summary, edit `src/re_ass/paper_summariser/project_knowledge/paper-summary-template.md`. To change the summariser instructions themselves, edit `src/re_ass/paper_summariser/project_knowledge/system-prompt.md` and `src/re_ass/paper_summariser/project_knowledge/user-prompt.md`, which may be needed if you alter the paper summary template structure.

## Troubleshooting

- If setup reports that provider validation was skipped for the freshly bootstrapped default config, edit `[llm]` in `user_preferences/settings.toml` and rerun `./scripts/setup.sh`.
- If setup or a manual run fails after you have chosen a provider, that provider is usually not installed or not authenticated for non-interactive use yet.
- If a scheduled run does not behave as expected, check `logs/last-run.log`, `logs/launchd.stdout.log`, and `logs/launchd.stderr.log`.
- If `re-ass` writes its managed section at the end of a note instead of where you expected it, your template heading does not exactly match the configured heading text.
- Machine-readable diagnostics are written to `state/runs/`.

## Validation

```bash
uv run pytest
```
