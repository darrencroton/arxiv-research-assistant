# A/B-testing re-ass — user stories, rubric, and AI-analysis playbook

This is the canonical lens for evaluating re-ass output from `scripts/ab-test.py`
runs. It exists so:

1. Any AI reviewer asked to "compare model X vs model Y" grades against the
   same user stories and rubric, instead of inventing a fresh framing each time.
2. The `compare` report's sections, the `--json` payload, and the rubric below
   are aligned 1-for-1.

Read this end-to-end before writing an A/B assessment. Then go look at the
report.

---

## 1. What re-ass is for

`re-ass` is a daily arXiv triage pipeline for a working astronomer. Each
weekday morning it picks a paper to read, summarises it well enough that the
researcher could cite from memory next week, and assembles a weekly editorial.
What it actually delivers, in order of value:

1. **A 60-second promote-to-read decision** — the right top paper, with a
   rationale a researcher can sanity-check before committing reading time.
2. **A citation-grade summary** — paper-quote footnoted, Methods/Results/
   Weaknesses sections, glossary so the researcher can hand it to a student.
3. **A weekly editorial** — a short paragraph that turns the week's reading
   into something paste-able to a group chat.
4. **Auditable provenance** — every paper not selected is still surfaced in
   the "of interest" tail, so nothing important is silently dropped.
5. **Set-and-forget operation** — runs nightly via launchd; failures are
   noisy (logged, fatal JSON) not silent.

If a candidate model breaks any of those, the cost/privacy story doesn't
save it.

---

## 2. User stories

These are the durable ones. Refine, don't replace.

| # | User story | Acceptance signal (and where to look in the compare report) |
|---|---|---|
| **U1 — Morning triage** | "Each weekday I open the daily note. I want a top paper, a summary I trust, and a paragraph rationale within 60 seconds. If the choice surprises me I want the 'of interest' band immediately." | `### Daily note` (top-paper match, managed-section present); `### Selection overlap`. |
| **U2 — Defensible selection** | "I need to explain to a collaborator why the system chose paper A over paper B. The ranker's reasoning must map onto my actual priorities." | `### Rationale side-by-side` (does the rationale name which preference?); `### Top-10 ranking overlap` (was the runner-up at least in the top-5?). |
| **U3 — Citation-grade summary** | "I want enough specifics — numbers, code names, sample sizes, weaknesses — that I can paraphrase to a student without reopening the PDF." | `### Paper-summary structure` (word count, sections present, glossary terms, footnote count, Weaknesses present). |
| **U4 — Weekly perspective** | "On Friday I want a short editorial of the week I can paste into Slack: one thesis, prose not bullets, 100–200 words." | `### Weekly synthesis` (word band, orphan H2 count, excerpt). |
| **U5 — Set-and-forget reliability** | "The job runs every day. If something fails I want it loud and diagnosable, not a silently corrupt note." | `### Reliability` (fatal/warnings/errors); `### Scoreboard` first row. |
| **U6 — Privacy / cost control** | "If a model can run locally with acceptable quality, I'd rather not send every paper to a paid API." | Not in the report; it's the deciding factor for tradeoffs once U1–U5 are scored. |

---

## 3. Rubric

Each dimension scored 1–5. **1 = unusable**; **3 = workable with caveats**;
**5 = matches a careful human's output**. The point isn't precision — it's a
shared, consistent way to size the gap between two providers.

| # | Dimension | What "5" looks like | Where it's visible |
|---|---|---|---|
| **R1** | Selection on-priority & defensible | Top pick is a clear match to a named preference (dual `science_match` + `method_match`); rationale names *which* priority. | `### Rationale side-by-side`, `### Selection overlap`, `### Per-paper score deltas`. |
| **R2** | Top-N ranking agreement | If the two providers disagree on top-1, they should at least agree on the top-3 pool. Jaccard ≥ 0.6 at top-5 and Kendall τ ≥ +0.5 on shared keys. | `### Top-10 ranking overlap`. |
| **R3** | Threshold discipline | Score distribution separates strong-match papers from the rest. Typical daily selections are 1–3: papers clearing `always_summarize_score` plus at most one top-up when fewer than two papers cleared the threshold. Consistently 3+ selections per day indicate score inflation on borderline papers — the question is whether each score is genuinely justified. | `### Threshold discipline`. |
| **R4** | Summary depth & accuracy | Key Ideas hit the paper's headline numbers; Results section quotes specific figures of merit; every claim has a paper-quote footnote with page anchor; Weaknesses concrete. | `### Paper-summary structure`. |
| **R5** | Template integrity | All required sections present; glossary populated (not silently dropped); tags from controlled vocabulary; no orphan H2/H1 inside managed sections. | `### Paper-summary structure` (missing_sections, glossary_terms), `### Weekly synthesis` (orphan_h2_count). |
| **R6** | Weekly synthesis quality | One coherent thesis with at least one tension/counterpoint; prose not bullets; lands inside the configured word band. | `### Weekly synthesis` (body_words, target band, excerpt). |
| **R7** | Operational reliability | Zero fatal runs in the window; recoverable retries rare; errors have actionable messages. | `### Reliability`. |
| **R8** | Author/title fidelity | First-author surname correctly extracted; diacritics preserved; title unmangled. | `### Daily note` (top-paper match by key — verify titles match what the daily note attributes them to). |
| **R9** | Cost / privacy posture | Inference happens where the user wants it; a local model with quality regressions on R1–R7 fails this dimension regardless of price. | Not in the report; assess after grading R1–R7. |

### How to combine the scores

Don't average them. **R5 (template integrity), R6 (synthesis), and R7
(reliability) gate everything else** — a model that scores 5 on R4 but 2 on
R5 is not yet a production candidate, because the user is reading broken
files. R1/R2/R3 grade the "is it picking the right paper" question; R4 grades
"is the summary good enough to cite"; R8 is a stable papercut to log but not
usually decisive.

In a tradeoff between two providers, write the verdict as: "X wins on
[dimensions]; Y wins on [dimensions]; the deciding factor is [user story
that's most at risk for *this* researcher]."

---

## 4. AI-analysis playbook

You've been asked to write an A/B assessment from a compare run. Do this:

### Before you start

1. Open *this file*. Use these user stories and this rubric verbatim. Don't
   invent your own framing.
2. Get the compare output. Either:
   - `python scripts/ab-test.py compare --week --markdown` (rich Markdown)
   - `python scripts/ab-test.py compare --week --json > /tmp/findings.json`
     (machine-readable; same numbers).

### Reading the report

Walk the report top-down, per day:

1. **`### Scoreboard`** — read it first. If it shows a fatal or a
   "structurally clean: ✗", that dominates everything below. Mention it in
   the headline.
2. **`### Provider stamp`** — confirm which model actually ran (run-summary
   stamp ≠ settings TOML in some cases).
3. **`### Reliability`** — if one side has fatal runs or repeated patterns
   like "Glossary generation failed", flag this as an R5/R7 hit.
4. **`### Candidate alignment`** — Jaccard < 0.95 means the two providers
   weren't even reading the same paper pool (rate-limit, timing skew). Stop
   grading R1–R3 until you've noted this caveat.
5. **`### Threshold discipline`** — there is no cap on the always_summarize
   band; every paper clearing `always_summarize_score` is correctly included.
   R3=2/3 when a variant consistently lands 2+ papers in that band and the
   extras are not clearly on-priority (score inflation, not selection logic).
   The ⚠ warning in the report flags 2+ papers in the always-summarize band
   as a prompt to check score quality, not a misconfiguration.
6. **`### Top-10 ranking overlap`** — this, not Selection overlap, is the
   honest R2 signal. Two providers can have selection Jaccard = 0 and Top-5
   Jaccard = 0.8 — they agree on the pool, disagree only at the very top.
7. **`### Selection overlap` + `### Per-paper score deltas` + `### Rationale
   side-by-side`** — the R1 picture. The rationales tell you whether each
   side reasoned about the user's actual priorities (named) or applied
   generic interestingness.
8. **`### Daily note`** — top-paper match, managed-heading present, `managed_body_words`. Word count is scoped to the body inside the configured top-paper heading; the daily-note file may also carry user-owned content (tasks, meetings, freeform notes) that re-ass doesn't measure.
9. **`### Weekly synthesis`** — orphan H2 count, word band, excerpt. R5+R6.
10. **`### Paper-summary structure`** — for shared selections, word count and
    section/glossary/footnote counts. R4+R5.

### Writing the assessment

Structure the final report as:

1. One-paragraph headline: who wins, and on which user story it matters most.
2. Per-day findings (one short paragraph per day) — *don't* dump tables; the
   AI/human reading your output already has the report.
3. A rubric scoreboard (table with R1–R9 scores for each provider).
4. Recommendation, in two parts:
   - "Use X for production today because …" (against U1–U5).
   - "Use Y as a sparring/local partner because …" (against U6, plus any
     dimensions where Y already matches X).
5. Targeted follow-ups (no more than three): the highest-leverage prompt or
   config changes that would close the gap.

**Do not** restate the report verbatim. **Do not** invent rubric dimensions.
**Do not** grade without the candidate-alignment caveat if the Jaccard is
< 0.95.

---

## 5. Lifecycle reference (mirrors the `scripts/ab-test.py` module docstring)

| Step | Command | What it does |
|---|---|---|
| Create variant | `python scripts/ab-test.py setup --name <var>` | Copies `settings.toml` → `settings-<var>.toml` with output/state/logs paths suffixed; user then edits the `[llm]` block. |
| Schedule | `python scripts/ab-test.py schedule --name <var>` | Installs `com.user.re-ass.<var>` launchd job offset 30 min from the benchmark. |
| Compare | `python scripts/ab-test.py compare [--week\|--last N\|--date D\|--all] [--markdown] [--json]` | Produces the A/B report; either side that lacks a `state/runs/announcement-*.json` won't appear (run the variant at least once first). |
| Cleanup | `python scripts/ab-test.py cleanup --name <var>` | Archives the variant settings and uninstalls launchd. Output/state/log dirs are *not* deleted; the print-out tells you how to archive them. |

---

## 6. What's not (yet) in the compare report

If you find yourself reaching for any of these during an assessment, file an
enhancement against `ab-test.py`:

- A semantic diff of two paper summaries beyond word/section/glossary counts
  (e.g. do they agree on the headline numerical claims?).
- An archived-weekly view (the compare only reads the current rolling note).
- A run-time/cost metric (latency, tokens, dollars). The pipeline doesn't
  persist these today.
- A trend over time (the report is per-day; week-over-week R-score drift
  would need a separate aggregator).
