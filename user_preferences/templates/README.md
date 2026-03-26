# Custom Daily and Weekly Templates

`re-ass` can use your own Markdown note templates, including templates stored in an Obsidian vault or any other notes directory.

The important point is that `re-ass` does not guess where to insert content. It updates specific sections identified by the heading text configured in `user_preferences/settings.toml`. If those headings are missing or renamed, `re-ass` appends its managed section at the end of the note instead of updating the place you intended.

## How template selection works

The default template paths are configured in:

- `user_preferences/settings.toml`

You can point these settings at any Markdown files you want:

```toml
[templates]
daily_template = "/path/to/your/daily-template.md"
weekly_template = "/path/to/your/weekly-template.md"
```

Those template files must already exist. Running the setup script will create default templates to get you going, but you'll probably want to customise things.

On first use, `re-ass` reads those template files and writes output notes into your configured output directories.

## Daily template requirements

Your daily template must contain the heading configured as `notes.daily_top_paper_heading`.

The current default is:

```markdown
## TODAY'S TOP PAPER
```

That section is where `re-ass` writes the selected top paper for the day.

Important details:

- Keep the heading text exact, including capitalisation.
- Anything under that specific heading is managed by `re-ass` and may be replaced.
- Content outside that heading is left alone.
- If the heading is missing, `re-ass` appends a fresh managed section at the end of the daily note.
- Automatic catch-up places missed batches onto earlier weekday notes, not onto every consecutive calendar day.

### Supported date placeholders

Daily templates can use:

- `{{date}}` for an ISO date such as `2026-03-23`
- `{{date:...}}` for a Pendulum format string such as `{{date:dddd Do MMMM YYYY}}`

### Example daily template

```markdown
# {{date:dddd Do MMMM YYYY}}

## Tasks

- 

## Notes

## TODAY'S TOP PAPER

## Journal
```

## Weekly template requirements

Your weekly template must contain the headings configured as `notes.weekly_synthesis_heading` and `notes.weekly_additions_heading`.

The current defaults are:

```markdown
## SYNTHESIS
## DAILY ADDITIONS
```

Important details:

- `re-ass` rewrites the first `#` title in the file to include the current week range.
- The synthesis heading section is managed by `re-ass` and replaced with the current weekly synthesis.
- The daily additions heading section is managed by `re-ass` and updated with per-day paper entries such as `### Monday 23rd`.
- Content outside those sections is left alone.
- If one of those headings is missing, `re-ass` appends that managed section at the end of the weekly note.

### Example weekly template

```markdown
# ARXIV PAPERS FOR THE WEEK

## Goals

- 

## SYNTHESIS

---

## DAILY ADDITIONS

## Follow-up
```

The `---` separator is optional, but the built-in template uses it and it reads well.

## What `re-ass` will insert

### Daily note section

`re-ass` writes:

- a link to the top paper note
- the micro-summary for that paper
- a link back to the matching weekly note for that day, which may be the live weekly note or a weekly archive during catch-up

### Weekly note synthesis

`re-ass` rewrites a rolling synthesis for the current week from the full weekly additions gathered so far into the heading configured by `notes.weekly_synthesis_heading`.
The default word budget starts at 100 words and expands through the week to 200 words by the end of the note window.

### Weekly daily additions

`re-ass` appends or updates day blocks under the heading configured by `notes.weekly_additions_heading`, for example:

```markdown
### Monday 23rd

**Title:** [[paper-note]]

**Summary:** Short summary here.
```

## Recommended workflow for Obsidian or another notes app

1. Keep your custom template files in your vault or notes directory.
2. Point `[templates]` in `user_preferences/settings.toml` at those files.
3. Set `[output].daily_notes_dir` and `[output].weekly_notes_dir` to folders you want to read from your notes app, or symlink the generated output directories into your vault.
4. Choose `notes.link_style = "wikilink"` for Obsidian-style links, or `notes.link_style = "markdown"` for standard Markdown links.

## Common mistakes

- Renaming the headings configured in `notes.daily_top_paper_heading`, `notes.weekly_synthesis_heading`, or `notes.weekly_additions_heading`
- Removing the first top-level `#` heading from the weekly template
- Putting important manual notes inside sections that `re-ass` manages
- Assuming the app uses invisible markers; it uses exact heading names instead

If you want a safe starting point, copy the built-in templates in this directory and modify everything except the managed headings, or update the heading settings at the same time.
