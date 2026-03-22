# Restructuring and Workflow Plan for re-ass

**Mode**: Plan  
**Date**: 2026-03-22  
**Status**: Revised after independent review

---

## Objective

Restructure `re-ass` into its intended final form:

- generic note generation and management, not hard-wired to Obsidian
- clean separation between the `re-ass` application layer and the vendored upstream paper summariser
- stable, explicit, transparent processing state
- professionally organised runtime directories and module names
- retained processed PDFs, reliable note updates, and clear logging
- no dead or legacy logic left in the final system

This plan prioritises the quality of the final end state over minimising implementation effort. The target architecture should be the best version of the product, not the smallest possible refactor.

---

## Executive Summary

The current code already performs the core workflow successfully:

- load preferences
- fetch recent arXiv papers
- rank papers
- suppress duplicates
- generate or fall back to paper summaries
- update daily and weekly summaries
- support historical backfill with `--date`

The problem is not missing core functionality. The problem is that the current implementation is structurally uneven:

- it is still Obsidian-shaped in naming and link rendering
- it uses title-based filenames as both display names and de facto identity
- it has no explicit notion of completed processing state
- it updates notes by assuming rigid template structure
- it discards downloaded PDFs after summarisation
- it contains upstream metadata-enforcement work that is now partially wasted

The revised plan fixes those issues by building a stronger architecture around the existing working pipeline, while keeping the vendored upstream summariser boundary intact.

Key changes from the previous plan:

1. **Stable paper identity is now a first-class design requirement.**
   The system will use a stable `paper_key` derived from the arXiv id, not the human-readable filename, as the authoritative identity for duplicate suppression and processing state.

2. **Template handling is now explicit and safe.**
   Daily and weekly templates are user-owned files with managed markers/placeholders, so updates do not depend on brittle heading-only parsing.

3. **Processing state is now explicit.**
   The system will store machine-readable paper records under `state/`, rather than inferring success from the presence of a PDF or note file.

4. **The vendored upstream summariser remains a vendored boundary.**
   We will not dissolve `src/re_ass/paper_summariser/` into top-level modules. Preserving that boundary is the better long-term design because it keeps upstream sync straightforward and limits local coupling.

5. **Processed PDFs are retained correctly.**
   Downloads will be staged safely and moved into `processed/` only as part of a successful per-paper transaction. `processed/` will not be treated as the sole signal that a paper is fully complete.

6. **Micro-summary generation moves earlier.**
   It will be generated immediately after paper selection, because it only depends on title and abstract. It remains part of the per-paper record, but note files are only updated after the paper run reaches a successful state.

---

## Decisions Resolved

The following open questions are now resolved and should be treated as fixed requirements:

1. **Daily and weekly note templates**
   - Ship simple defaults.
   - Treat templates as user-owned files.
   - README must recommend pointing config directly at Obsidian templates or symlinking to them.
   - Most users, including the primary use case, are expected to use Obsidian-backed templates.

2. **Default link style**
   - Default to `wikilink`.
   - Support both `wikilink` and standard Markdown links.

3. **Weekly archive naming**
   - Default to `"{date}-weekly-arxiv.md"`.
   - Make the pattern easy to change in config.

4. **Micro-summary timing**
   - Generate earlier, before PDF download and full-paper summarisation.
   - The implementation should do whatever best supports the final workflow and data model.

---

## End-State Principles

These principles govern the final design:

1. **End-state first**
   We are not optimising for a small diff. We are optimising for the best final architecture.

2. **Generic output, specific source**
   The application is still arXiv-focused, but its note-generation and note-management layer must be generic Markdown, not Obsidian-specific.

3. **Upstream-first where appropriate**
   The vendored `paper_summariser` subtree should stay self-contained and close to upstream. App-specific adaptation belongs outside that boundary.

4. **Stable identity before pretty filenames**
   Human-readable filenames are useful. Stable machine identity is mandatory.

5. **Managed markers over fragile parsing**
   If the app edits user-facing notes, it must do so through explicit managed regions, not by assuming fixed headings alone.

6. **Explicit state over inferred state**
   Processing status must be visible and machine-readable.

7. **Transactional paper processing**
   Each paper should move through a predictable sequence of stages with clear success and failure handling.

8. **No dead paths**
   Final code should contain no compatibility shims, no unused config branches, no wasted validation work, and no dormant directories/features.

---

## Scope

### Included

- runtime directory restructuring
- config schema redesign
- generic note/output generalisation
- stable paper identity design and helper implementation
- explicit processing state design
- retained PDF lifecycle redesign
- daily/weekly template contract redesign
- link rendering redesign
- pipeline orchestration redesign
- dead code and legacy path removal
- logging and diagnostics improvements
- documentation rewrite
- final independent code review and cleanup

### Explicitly Not Included

- changing the arXiv ranking model beyond what is needed for identity/state integration
- rewriting the provider architecture
- replacing the upstream summariser prompt structure
- adding an `input/` watch loop that is not used by the current arXiv workflow

`input/` is intentionally excluded from the target runtime because it would add an unused feature and a dead directory. The suggested structure in the original instructions was a guide, not a requirement to create unused infrastructure.

---

## Current-State Assessment

### What already works

- config loading via `re_ass.toml`
- preference parsing
- arXiv fetch + ranking + fallback lookback
- title-based duplicate suppression
- note generation through the vendored summariser
- deterministic local fallbacks when LLM generation is unavailable
- daily summary updates
- weekly summary rotation and synthesis updates
- backfill with `--date`

### What is structurally wrong today

1. **Obsidian coupling is still embedded in naming and rendering.**
   - `[vault]` config section
   - `obsidian_vault/` runtime root
   - `VaultManager`
   - `ProcessedPaper.wikilink`
   - `render_obsidian_note()`

2. **Identity is unstable.**
   Duplicate suppression and note naming are based on sanitized titles. That is insufficient as a durable system identity.

3. **Processing state is ambiguous.**
   The app currently infers state from the existence of note files. After restructure, relying on note existence or PDF existence alone would still be brittle.

4. **Template editing is brittle.**
   Daily/weekly updates currently depend on fixed headings and regex structure, which is unsafe once templates become user-owned.

5. **PDF lifecycle is incomplete.**
   PDFs are downloaded into a temporary directory and discarded after summarisation.

6. **Some summariser-side metadata work is now wasted.**
   Source metadata is enforced and validated in the LLM output, but the final note wrapper rebuilds the metadata header from `ArxivPaper` and discards the LLM top matter.

7. **There is legacy or misleading code.**
   - `_infer_legacy_provider()`
   - the retained `command_prefix` fallback path
   - `del is_pdf`
   - misleading module names such as `config_manager.py` and `vault_manager.py`

8. **Logging and diagnostics are too shallow.**
   The current app mainly logs to stdout/stderr. For a scheduled automation workflow, a local `logs/` directory is justified and useful.

---

## Feasibility, Complexity, and Value

### Feasibility

High. The codebase is small enough that a disciplined restructure is practical, and the major behaviours are already covered by tests and recent end-to-end validation.

### Complexity

Moderate-to-high as a refactor, but bounded. The complexity is architectural rather than algorithmic.

### Value

High. The restructure solves real stability, maintainability, and transparency problems. It also prevents a future slow drift into a half-generic, half-Obsidian-specific codebase with unreliable state handling.

---

## Target Architecture

### Target runtime layout

```text
re-ass/
├── output/
│   ├── papers/                     # Generated paper summaries
│   ├── daily/                      # Daily summaries
│   └── weekly/                     # Current weekly summary + archives
├── processed/                      # Retained PDFs for successfully processed papers
├── state/
│   ├── papers/                     # Per-paper machine-readable records
│   └── runs/                       # Optional per-run summaries
├── logs/                           # Processing history and last-run diagnostics
├── templates/
│   ├── daily-note-template.md
│   └── weekly-note-template.md
├── preferences.md                  # User categories and priorities
├── scripts/
│   └── launchd/                    # launchd and other automation assets
├── src/re_ass/
│   ├── __init__.py
│   ├── __main__.py
│   ├── main.py                     # CLI entry point
│   ├── pipeline.py                 # End-to-end workflow orchestration
│   ├── settings.py                 # Config loading and validation
│   ├── preferences.py              # Preferences parsing
│   ├── models.py                   # Core dataclasses shared across app modules
│   ├── paper_identity.py           # Stable identity + filename + metadata helpers
│   ├── state_store.py              # Explicit paper/run state persistence
│   ├── arxiv_fetcher.py            # Fetching and ranking
│   ├── generation_service.py       # App-side generation orchestration
│   ├── note_manager.py             # Daily/weekly note management
│   └── paper_summariser/           # Vendored upstream-derived summariser boundary
│       ├── __init__.py
│       ├── service.py
│       ├── providers/
│       └── project_knowledge/
├── docs/
├── tests/
├── archive/
├── pyproject.toml
├── re_ass.toml
└── README.md
```

### Why this is the right final layout

1. **`output/` is generic and symlink-friendly.**
   Users can expose `output/papers/`, `output/daily/`, and `output/weekly/` inside Obsidian or elsewhere.

2. **`processed/` retains real artifacts.**
   PDFs are preserved and traceable.

3. **`state/` keeps machine state separate from user-facing notes.**
   This is critical for stable duplicate suppression, transparent recovery, and clean automation.

4. **`logs/` provides local diagnostics.**
   This matches the stated goal of transparency and supports unattended runs.

5. **`templates/` contains only user-facing note templates.**
   It is intentionally separate from the vendored summariser prompt assets.

6. **The vendored upstream boundary stays intact.**
   `paper_summariser/` remains self-contained, which is the better long-term architecture.

7. **No unused `input/` directory is created.**
   The current product is arXiv-driven, not file-drop-driven. Creating unused infrastructure would violate the no-dead-code goal.

---

## Module Responsibilities

### `main.py`

- CLI parsing
- logging bootstrap
- config load
- handoff to `pipeline.run()`

### `pipeline.py`

- end-to-end workflow
- run-level orchestration
- per-paper transactional handling
- run summary and exit status

### `preferences.py`

- parse `preferences.md`
- extract categories and ranked priorities
- validate required sections/content

### `paper_identity.py`

- derive stable `paper_key`
- derive canonical filename stem
- produce display metadata
- centralise link label/path helpers

### `state_store.py`

- read/write per-paper state records
- determine completed vs failed vs partial status
- support duplicate suppression based on stable identity

### `generation_service.py`

- app-side content generation orchestration
- micro-summary generation
- paper-note creation through vendored summariser
- deterministic fallback behaviour

### `note_manager.py`

- bootstrap output/templates
- manage daily summary updates
- manage weekly summary creation, rotation, archive naming, and synthesis block updates
- render links according to config
- update only within managed markers

### `paper_summariser/`

- vendored upstream summarisation path
- explicit provider architecture
- PDF direct-upload or marker-pdf extraction path
- paper summary production

---

## Configuration Design

### Proposed `re_ass.toml` schema

```toml
[output]
root = "output"
papers_dir = "papers"
daily_dir = "daily"
weekly_dir = "weekly"

[processed]
root = "processed"

[state]
root = "state"
papers_dir = "papers"
runs_dir = "runs"

[logs]
root = "logs"
history_file = "history.log"
last_run_file = "last-run.log"

[templates]
daily_template = "templates/daily-note-template.md"
weekly_template = "templates/weekly-note-template.md"

[preferences]
file = "preferences.md"

[notes]
link_style = "wikilink"
weekly_note_file = "this-weeks-arxiv-papers.md"
rotation_day = "monday"
archive_name_pattern = "{date}-weekly-arxiv.md"

[arxiv]
max_papers = 3
fetch_window_hours = 24
fallback_window_hours = 168
max_results = 200
default_categories = ["astro-ph.CO"]

[llm]
enabled = false
mode = "cli"
provider = "claude"
model = ""
timeout_seconds = 900
max_output_tokens = 12288
temperature = 0.2
retry_attempts = 3
allow_local_paper_note_fallback = true
prompt_debug_file = "tmp/paper_summariser/prompt.txt"
download_timeout_seconds = 120
max_pdf_size_mb = 100
marker_timeout_seconds = 300
ollama_base_url = "http://localhost:11434"
```

### Config principles

1. Paths are explicit and user-controllable.
2. Templates are direct file paths, so symlinks and Obsidian template files work naturally.
3. `link_style` is a proper app-level setting.
4. Weekly archive naming is configurable through a simple pattern.
5. No legacy `command_prefix` fallback remains.

---

## Stable Paper Identity, Filenames, and Metadata

### Problem to solve

The current code uses `sanitize_note_name(title)` for filenames and duplicate suppression. That is not stable enough.

### Final design

Introduce a dedicated paper identity helper and record model.

#### `paper_key`

The authoritative paper identity.

- For arXiv papers: `arxiv:<versionless_id>`
- Example: `arxiv:2603.15732`

This key is used for:

- duplicate suppression
- state records
- processed-PDF tracking
- recovery logic
- run logs

#### Canonical filename stem

The human-readable, canonical filename stem used for both notes and processed PDFs:

`<FirstAuthor et al> - <Year> - <Title> [arXiv <id>]`

Examples:

- `Bayer et al - 2026 - Field-Level Inference from Galaxies BAO Reconstruction [arXiv 2603.15732].md`
- `Bayer et al - 2026 - Field-Level Inference from Galaxies BAO Reconstruction [arXiv 2603.15732].pdf`

This is the recommended final format because it gives:

- human readability
- traceability
- uniqueness
- easy manual inspection

It is superior to using author-year-title alone.

#### Helper outputs

The helper should return a structured object, not just a string:

```python
@dataclass(frozen=True, slots=True)
class PaperIdentity:
    paper_key: str
    source_id: str
    filename_stem: str
    note_filename: str
    pdf_filename: str
    authors_short: str
    authors_full: tuple[str, ...]
    year: int
```

#### Micro-summary placement

Micro-summary generation does **not** belong inside the identity helper because it is generated content, not stable source metadata.

Instead:

- identity helper produces stable metadata and paths
- generation service produces `micro_summary`
- state record stores both

### Uses of the helper

- `arxiv_fetcher.py` duplicate suppression by `paper_key`
- `generation_service.py` note and PDF paths
- `note_manager.py` link labels and display metadata
- `state_store.py` record persistence
- tests for identity stability, sanitisation, and collisions

---

## Template Contract

### Requirement

Templates must be user-owned and flexible, but the app still needs a reliable way to update them.

### Final design

Use explicit managed markers.

#### Daily template default

```md
# {{date}}

<!-- re-ass:daily-top-paper:start -->
## Today's Top Paper
<!-- re-ass:daily-top-paper:end -->
```

#### Weekly template default

```md
# This Week's ArXiv Overview

## Synthesis
<!-- re-ass:weekly-synthesis:start -->
*(A synthesis of this week's papers will be automatically generated here. Max 100 words.)*
<!-- re-ass:weekly-synthesis:end -->

---
## Daily Additions
<!-- re-ass:weekly-daily-additions:start -->
<!-- re-ass:weekly-daily-additions:end -->
```

### Rules

1. The app only edits content inside managed markers.
2. User content outside those markers is preserved untouched.
3. The app may seed missing templates with simple defaults.
4. The README must explicitly recommend:
   - using direct template paths in Obsidian, or
   - symlinking local template files to Obsidian template files

This is the right final design because it supports user-owned templates without making note updates brittle.

---

## Link Rendering Contract

### Supported styles

1. `wikilink` (default)
2. `markdown`

### `wikilink`

- Default because Obsidian is still the primary use case.
- Render `[[Note Name]]` or `[[Note Name|Paper Title]]`.

### `markdown`

- Render relative links from the current summary to the paper summary.
- Example from daily summary to paper summary:
  `[Paper Title](../papers/<filename>.md)`

### Rule

All link rendering must be centralised in one helper. Daily and weekly summary logic must not hardcode link syntax.

---

## State Model

### Why explicit state is required

The presence of a PDF is not proof of a completed paper run. The presence of a note file is also not enough for robust recovery.

### Final design

Store one machine-readable paper record per `paper_key` under `state/papers/`.

Suggested shape:

```json
{
  "paper_key": "arxiv:2603.15732",
  "source_id": "2603.15732",
  "title": "Field-Level Inference from Galaxies: BAO Reconstruction",
  "published": "2026-03-16T00:00:00+00:00",
  "filename_stem": "Bayer et al - 2026 - Field-Level Inference from Galaxies BAO Reconstruction [arXiv 2603.15732]",
  "note_path": "output/papers/...",
  "pdf_path": "processed/...",
  "micro_summary": "...",
  "status": "completed",
  "first_completed_at": "...",
  "last_attempt_at": "...",
  "last_error": null
}
```

### Status values

- `selected`
- `micro_summary_generated`
- `pdf_downloaded`
- `note_written`
- `completed`
- `failed`

### Duplicate suppression rule

Primary duplicate suppression should be:

- skip if `paper_key` already has a `completed` state record

Secondary defensive checks may still look at note files during migration/cleanup, but file presence is no longer the authoritative system record.

---

## Logging and Diagnostics

### Final design

Add a local `logs/` directory with:

- `history.log` for append-only processing history
- `last-run.log` for the most recent run
- optional run summary JSON under `state/runs/`

### Why this belongs in the final product

- supports unattended scheduled runs
- improves transparency
- keeps debugging local to `re-ass`
- aligns with the original desired structure

Console logging should remain, but local logs are still valuable and justified.

---

## PDF Lifecycle Design

### Current issue

PDFs are downloaded into a temporary directory and discarded.

### Final design

1. Derive `paper_key` and `filename_stem` first.
2. Download PDF to a temporary staging path.
3. Use the staged file for direct-PDF upload or text extraction.
4. Only after successful per-paper processing, move/copy the PDF into:
   `processed/<canonical_filename>.pdf`
5. Update the paper state record accordingly.

### Important rule

`processed/` means “retained artifact”, not “authoritative completed state”.

That distinction is necessary to avoid unrecoverable partial failures.

---

## Workflow Comparison: Current vs Target

| Area | Current | Target |
| --- | --- | --- |
| Output model | Obsidian-shaped | Generic Markdown output |
| Runtime root | `obsidian_vault/` | `output/`, `processed/`, `state/`, `logs/` |
| Identity | title-derived filename | stable `paper_key` + canonical filename |
| Duplicate suppression | existing note stems | completed paper state by `paper_key` |
| PDF retention | temp only | retained in `processed/` |
| Templates | seeded, rigid structure | user-owned templates with managed markers |
| Link rendering | hardcoded wikilinks | centralised `wikilink` or `markdown` |
| Weekly archive naming | hardcoded Sunday archive name | configurable pattern, Monday default rotation |
| Processing state | implicit | explicit per-paper record |
| Logging | stdout/stderr | stdout/stderr + local logs |
| Upstream boundary | present but not cleanly respected in plan | explicitly preserved |

---

## Target Workflow

This is the intended final workflow for `uv run re-ass` and `uv run re-ass --date YYYY-MM-DD`.

1. **Load configuration** from `re_ass.toml`.
2. **Bootstrap runtime paths**:
   - `output/papers/`
   - `output/daily/`
   - `output/weekly/`
   - `processed/`
   - `state/papers/`
   - `state/runs/`
   - `logs/`
3. **Ensure templates and preferences exist** if configured local defaults are missing.
4. **Rotate weekly summary if required** for a non-backfill run:
   - default rotation day is Monday
   - archive filename uses `archive_name_pattern`
   - rotation preserves user template structure and managed markers
   - explicit historical backfills leave the current weekly summary unchanged
5. **Load user preferences** from `preferences.md`.
6. **Fetch recent papers from arXiv** for configured categories and time window.
7. **Rank papers** against user priorities.
8. **Build candidate bundles** for the top-ranked papers:
   - derive `paper_key`
   - derive canonical filenames
   - derive display metadata
   - skip papers already marked `completed`
9. **Generate micro-summaries early** from title + abstract:
   - use provider if enabled
   - otherwise deterministic fallback
   - store in paper bundle/state
10. **Process papers in rank order**:
   - stage PDF download
   - generate paper summary through vendored summariser or deterministic fallback
   - commit retained PDF to `processed/`
   - write note to `output/papers/`
   - persist/update `state/papers/<paper_key>.json`
11. **Handle failures per paper transactionally**:
   - failed paper does not update daily/weekly summaries
   - error is recorded in state/logs
   - remaining papers continue unless an explicit fatal condition occurs
12. **Update daily summary** with the highest-ranked successfully completed paper using template markers.
13. **Update weekly summary** with all successfully completed papers from the run using template markers, except explicit historical backfills which leave the current weekly summary unchanged.
14. **Generate weekly synthesis** from existing synthesis plus new micro-summaries:
   - use provider if enabled
   - otherwise deterministic fallback
15. **Persist run summary** under `state/runs/` and `logs/`.
16. **Exit successfully** if the run completed cleanly, even if zero new papers were completed.

### No-new-paper behaviour

If no unseen papers remain after duplicate suppression, or if none complete successfully:

- do not mutate daily or weekly summaries
- write a clear log entry
- exit `0`

---

## Implementation Phases

### Phase 1: Freeze the target contracts

Purpose: define the final interfaces before moving files around.

Steps:

1. Finalise the runtime directory contract.
2. Finalise the config schema.
3. Finalise the template marker contract.
4. Finalise the stable identity model.
5. Finalise the paper state record schema.
6. Finalise the link rendering contract.
7. Finalise the failure-handling rules for per-paper transactions.

Deliverable:

- documented contracts with no remaining open design questions

### Phase 2: Archive the pre-restructure runtime

Purpose: start the new system cleanly rather than carrying forward mixed legacy artifacts.

Steps:

1. Archive the current `obsidian_vault/` runtime outputs under `archive/`.
2. Preserve the current preferences file and template file contents for reuse.
3. Archive stale planning and handoff artifacts that are superseded.
4. Keep source history in git; do not attempt an in-place runtime migration of generated notes.

Rationale:

- backward compatibility is not required
- clean runtime state is preferable to complicated migration logic

### Phase 3: Restructure modules around a preserved vendored boundary

Purpose: improve names and app structure without flattening the vendored summariser.

Steps:

1. Keep `src/re_ass/paper_summariser/` intact.
2. Create new app-side modules:
   - `pipeline.py`
   - `preferences.py`
   - `paper_identity.py`
   - `state_store.py`
   - `generation_service.py`
   - `note_manager.py`
3. Rename or replace misleading app-side modules.
4. Keep `main.py` as the CLI entry point.
5. Update imports and packaging.

### Phase 4: Implement stable identity and explicit state

Purpose: make identity and completion semantics correct.

Steps:

1. Implement `paper_key` derivation from arXiv id.
2. Implement canonical filename generation with arXiv id suffix.
3. Implement metadata display helpers.
4. Implement `state_store.py`.
5. Replace duplicate suppression based on note stems with `paper_key`-based state lookup.
6. Add tests for:
   - stable key derivation
   - filename sanitisation
   - author formatting
   - collision prevention
   - completed/failed state handling

### Phase 5: Generalise output and note management

Purpose: remove Obsidian coupling from the note/output layer.

Steps:

1. Replace `[vault]` with the new config sections.
2. Replace `VaultManager` with `NoteManager`.
3. Replace hardcoded `obsidian_vault/` paths with generic output roots.
4. Replace `ProcessedPaper.wikilink` with centralised link rendering.
5. Preserve support for Obsidian via `wikilink` default, not through hardcoded assumptions.

### Phase 6: Implement template markers and safe note editing

Purpose: make template usage robust and user-owned.

Steps:

1. Create simple default daily and weekly templates.
2. Implement marker-based update logic.
3. Replace heading-regex-only parsing for managed content.
4. Ensure user content outside markers is preserved untouched.
5. Add tests covering:
   - existing custom content around markers
   - missing markers
   - template bootstrap
   - repeated reruns on the same day

### Phase 7: Redesign the PDF lifecycle

Purpose: retain PDFs safely and correctly.

Steps:

1. Add processed path config.
2. Introduce staged downloads.
3. Use staged files for the summariser path.
4. Move PDFs to final retained location only after successful paper processing.
5. Record retained PDF path in paper state.
6. Add tests for:
   - successful retain
   - failed summarisation leaves no false completed state
   - reruns after partial failure recover cleanly

### Phase 8: Redesign the pipeline around per-paper transactions

Purpose: make the workflow operationally robust.

Steps:

1. Split CLI concerns from orchestration logic.
2. Generate micro-summaries immediately after ranking.
3. Replace list-comprehension processing with explicit per-paper transactional flow.
4. Continue past non-fatal per-paper failures.
5. Update daily summary from the highest-ranked successful paper.
6. Update weekly summary from all successful papers, except explicit historical backfills which leave the current weekly summary unchanged.
7. Write run summaries and diagnostics.
8. Add integration tests for:
   - zero new papers
   - partial success
   - all success
   - LLM-disabled deterministic path
   - LLM-enabled mocked path

### Phase 9: Remove dead and legacy code

Purpose: finish with a clean codebase.

Steps:

1. Remove `_infer_legacy_provider()` and legacy config inference.
2. Remove `command_prefix` support.
3. Remove or simplify `del is_pdf` and any similarly vestigial arguments.
4. Re-evaluate summariser metadata enforcement:
   - keep only what still has real value
   - remove any metadata repair/validation that is provably discarded by the app wrapper
5. Remove stale docs and stale runtime assumptions.
6. Remove outdated names and comments that no longer describe reality.

### Phase 10: Logging, docs, and user guidance

Purpose: make the final system transparent and easy to use.

Steps:

1. Rewrite `README.md`.
2. Document:
   - runtime directory structure
   - symlink strategy for outputs
   - direct-template-path strategy for Obsidian templates
   - link-style options
   - weekly archive naming
   - backfill behaviour
   - state and log locations
3. Update `AGENTS.md`.
4. Update or archive stale reports and assumptions docs.

### Phase 11: Independent final code review

Purpose: explicitly satisfy the original instruction to review the implemented result against the quality goals.

The final task after implementation is a full independent code review against:

1. does exactly what it says
2. professionally organised
3. clean, clear, transparent
4. runs efficiently
5. well documented
6. follows KISS and DRY
7. stable
8. no dead, outdated, legacy code or logic

Special emphasis:

- identity and duplicate suppression correctness
- template marker safety
- transactional processing semantics
- unnecessary leftover compatibility code
- dead config options and stale runtime paths

---

## Test Strategy

The current tests are a good base but not enough for the restructured product.

### Required additions

1. `tests/test_paper_identity.py`
2. `tests/test_state_store.py`
3. `tests/test_note_manager.py`
4. `tests/test_pipeline.py`
5. expanded `tests/test_paper_summariser_service.py`
6. updated `tests/test_settings.py`
7. updated fetcher tests for `paper_key`-based suppression

### Required validation

- `uv run python -m compileall src tests`
- `uv run pytest`
- targeted backfill replay with mocked providers
- targeted live validation when provider credentials are available

---

## Recommended Final End State

At the end of this work, `re-ass` should be:

- an arXiv-driven assistant with generic Markdown outputs
- locally managed under `output/`, `processed/`, `state/`, and `logs/`
- easy to expose in Obsidian via symlinks and direct template paths
- explicit about what has and has not been processed
- stable in duplicate suppression and reruns
- cleanly layered around a preserved vendored upstream summariser
- free of dead compatibility code and misleading naming

That is the end state this plan targets.
