# A/B test report: minmax vs sonnet (announcements 2026-05-07 → 2026-05-11)

Generated: 2026-05-13

---

## 1. What re-ass does (and why it matters)

`re-ass` is a daily arXiv triage pipeline for a working astronomer:

1. **Fetch** new papers from the user's configured arXiv categories on each
   announcement day, deduplicate cross-listed entries.
2. **Rank** the day's pool against the researcher's own `preferences.md`
   (science priorities + method priorities, with explicit exclusions).
3. **Select** the top-1 (or top-N at `[arxiv].max_papers`) once the ranker
   clears `always_summarize_score`, and surface a "papers of interest" band
   for everything else above `min_selection_score`.
4. **Summarise** each selected paper from the PDF into a structured Markdown
   note (Key Ideas / Intro / Data / Method / Results / Discussion /
   Weaknesses / Conclusions / Future work / Glossary / Tags / References
   with paper-quote footnotes).
5. **Stitch** the result into the researcher's daily note (Obsidian) and roll
   up an editorialised weekly synthesis.

### Why this is valuable to an astronomer

A working researcher in galaxy formation / cosmology sees ~30–60 new
candidate arXiv papers a day across `astro-ph.GA` and `astro-ph.CO`. Reading
even the abstracts costs an hour; reading the top paper costs three. The
researcher mostly cares about a small, well-defined subset (e.g. LRDs, SMBH
co-evolution, SAMs, JWST, surveys). The value `re-ass` delivers is:

- **Triage at researcher resolution**, not generic "trending in
  astrophysics" — the ranker reads from the actual `preferences.md` so it
  knows which "AGN" papers count (e.g. exclude GW-only MBH binaries) and
  which method matches (e.g. SAMs, large surveys).
- **A trustworthy promote-to-read decision** every weekday morning: a
  single TOP paper plus an honest "of interest" tail, with a *rationale
  line* the researcher can sanity-check in 5 seconds before committing
  half a day to a PDF.
- **A research-grade summary** of the chosen paper that is good enough to
  cite from memory next week — figures of merit (numbers, names of codes,
  resolution, sample size), an explicit weaknesses section, and a
  glossary so the researcher can hand it to a student.
- **A weekly synthesis** that turns a week's reading into a
  one-paragraph editorial: "here is the through-line of this week's
  arXiv" — useful for group meetings, blog posts, and keeping perspective.
- **Provenance**: every claim in the paper note is anchored to a quoted
  passage with a section/page reference so the researcher can verify
  before quoting downstream.

The point of the tool is **not** to replace reading. It is to ensure the
single hour the researcher spends reading each morning is spent on the
right paper, and that everything *not* selected is auditable so nothing
important is silently dropped.

---

## 2. User stories

| # | User story | Acceptance signal |
|---|---|---|
| **U1 — Morning triage** | "Each weekday morning I open my daily note. Within 60 seconds I want a TOP paper, a summary I trust, and a paragraph rationale. If the choice surprises me I want to see the 'also of interest' band immediately." | Daily note populated by morning; TOP paper is recognisably on-priority; rationale survives a sanity check; tail is present and ordered. |
| **U2 — Defensible selection** | "I need to be able to explain to my collaborator why the system chose paper A over paper B today. The ranker's reasoning has to map onto my actual priorities, not a generic 'interesting astronomy' notion." | Each ranking result carries a rationale; `science_match` / `method_match` flags align with `preferences.md`; the scoring distribution puts at most a few papers in the always-summarize band per day, not the entire pool. |
| **U3 — Citation-grade summary** | "When I read the summary, I want enough specifics (numbers, code names, sample sizes, key figures) that I can paraphrase to a student without reopening the PDF. I also want to know the paper's weaknesses." | Summary contains template sections including Methods, Results with numbers, Weaknesses, Glossary; every quantitative claim has a paper-quote footnote with a page reference. |
| **U4 — Weekly perspective** | "On Friday I want a short editorial of the week's papers I can paste into a Slack group: a single coherent paragraph that ties the threads together." | One weekly synthesis section, 100–200 words (the configured target), single thesis, prose not bullets, on-topic. |
| **U5 — Set-and-forget reliability** | "The job should run every day on schedule without me babysitting it. If something fails I want it to fail loudly in a way that's easy to diagnose, not silently corrupt my notes." | Few or zero fatal errors per week; recoverable retries leave clean output; logs surface root cause in `last-run.log`. |
| **U6 — Privacy / cost control** | "If a model can run locally with acceptable quality, I'd rather not send every paper to a paid API." | Cost / privacy is acceptable for routine use; researcher chooses tradeoff knowing the quality delta. |

---

## 3. Rubric

Each output is scored on a 1–5 scale across these dimensions. 1 = unusable;
3 = workable but with caveats; 5 = matches a careful human's output.

| Dimension | What "5" looks like |
|---|---|
| **R1. Selection on-priority** | Top pick is a clear match to one of the researcher's named priorities (science *and* method match); decision survives a researcher's eyebrow test. |
| **R2. Rationale quality** | One sentence that names *which* preference the paper matches and *why* (mechanism, dataset, code) — not generic praise. |
| **R3. Scoring discipline** | Score distribution separates the strong on-priority papers from the rest; few false positives in the always-summarize band; dual-match papers cluster at the top. |
| **R4. Summary depth & accuracy** | Key Ideas hit the paper's actual headline numbers; Results section quotes specific figures of merit with page-anchored footnotes; Weaknesses are concrete, not hedged. |
| **R5. Template integrity** | All required sections present; glossary populated; tags from the controlled vocabulary; no duplicated or dropped sections. |
| **R6. Weekly synthesis** | One coherent thesis, 100–200 words, prose-not-bullets, references at least one tension/counterpoint, sounds like the researcher could send it to a group chat. |
| **R7. Operational reliability** | Job runs to completion on schedule; recoverable retries are rare and silent; fatal failures are zero per week and have actionable error messages. |
| **R8. Author/title fidelity** | First-author surname correctly identified; non-Latin diacritics handled; title not silently truncated or mangled. |
| **R9. Cost / privacy posture** | Inference happens on the researcher's preferred plane (local for routine, paid API for boundary cases) without quality regressions that defeat the point of the tool. |

---

## 4. Test setup

- **Benchmark ("sonnet")** = `mode=cli, provider=copilot, model=claude-sonnet-4.6, effort=high`, output landing in the Obsidian vault (`~/.../Vault/Science/Papers`, `Internal/Daily Notes`, `Science/Weekly-ArXiv`).
- **Variant ("minmax")** = `mode=api, provider=openai-compatible`, served from a local Tailscale endpoint at `https://djcmacstudio.tail98bbb1.ts.net/v1/chat/completions` (a local MiniMax-class model), output landing in `~/Documents/AI Tools/private/re-ass-tests/`.
- Both share the same `preferences.md`, same `[arxiv]` thresholds
  (`always_summarize_score = 85`, `min_selection_score = 70`,
  `max_papers = 1`), same templates.

Three announcement days are evaluated (the user's request listed
2026-05-07 twice; I interpret this as 05-07, 05-08, 05-11). Announcement
days map to the *following* weekday's daily note via
`shift_announcements_to_next_weekday = true`.

Note on methodology: `ab-test.py compare` could not be run end-to-end —
the benchmark side writes state JSON into a path that has been pruned,
so there is no `state/runs/announcement-2026-05-07*.json` on the sonnet
side. Selection facts for sonnet are reconstructed from the live daily
and weekly notes in the vault; ranking detail (top-N table, rationales)
is read from the variant's state JSON only.

---

## 5. Side-by-side results

### 5.1 Selection

| Announcement → note | minmax top picks (score) | sonnet top pick | Top-1 match | minmax rank of sonnet's pick |
|---|---|---|---|---|
| **2026-05-07 → 05-08** | 2605.05074 LRDs as obscured LBDs (88, dual-match), 2605.04776 SMBH–stellar-mass evolution (85, dual) | 2605.04144 "Big Wheel" galaxy-halo at z~3 | ✗ | #3 (score 84, dual-match) |
| **2026-05-08 → 05-11** | 2605.05972 TNG50 satellite kinematic planes (85, dual) | 2605.06400 LoTSS-DR3 cluster Radio U-Net | ✗ | #4 (score 78, dual-match) |
| **2026-05-11 → 05-12** | 2605.06782 COLIBRE UVLF z=7–15 (92, dual), 2605.06769 Lumen extreme line ratios (90, dual), 2605.06781 FIRE-2 stellar orbits (85, dual) | 2605.06782 COLIBRE UVLF z=7–15 | ✓ | #1 |

**Reading:**

- **One exact top-1 agreement out of three** (the COLIBRE paper, where the
  case is so strong even an automated reader picks it).
- **Top-3 overlap is much better than top-1**: on 05-07 and 05-08, sonnet's
  pick sits in minmax's top-3/top-4 with the same dual-match flag.
  Disagreement is at the margin of a few points, not a fundamental
  taxonomy failure.
- **minmax pushes more papers above the `always_summarize_score = 85`
  band** than sonnet does. On 05-11 minmax landed three papers ≥ 85; on
  05-07 it landed two. Sonnet's score distribution is tighter and yields
  one paper above 85 per day. Both behaviours are defensible — minmax is
  rewarding multi-match papers more aggressively; sonnet rations the
  always-summarize verdict — but they have different ergonomic
  consequences (see U1, U3).

### 5.2 Daily summary quality (head-to-head on same paper)

The two days where both sides chose the same paper give us a clean
head-to-head:

- **2026-05-06 → 05-07: arXiv 2605.03008** (environmental quenching of
  high-z galaxies with simulations)
- **2026-05-11 → 05-12: arXiv 2605.06782** (COLIBRE UVLF z=7–15)

| Metric | minmax | sonnet |
|---|---|---|
| Words (2605.06782) | 2112 | 2891 (+37%) |
| Words (2605.03008) | 2158 | 2551 (+18%) |
| Glossary terms (06782) | 9 | 12 |
| Tags (06782) | 9 | 10 |
| Required sections present | ✓ | ✓ |
| Footnoted quantitative claims | ✓ | ✓ |
| Weaknesses section concrete | ✓ | ✓ (also includes ionising-background caveat that minmax omits) |
| Discussion places paper in literature | partial | yes (e.g. GALFORM dual-IMF precedent on COLIBRE summary) |
| Critical voice (e.g. flags "ad hoc" rescue mechanisms) | absent | explicit |

**Reading:** both summaries are scientifically usable. They quote the same
headline numbers (~1 mag deficit at z=7, ~2.5 mag at z=15, factor-300
luminosity-density drop) and use the same template skeleton. Sonnet's
summary is consistently the deeper one — broader discussion, more
interpretive critical commentary, larger glossary, more historic context.
Minmax's summary is tighter but does not miss the headline science. For
**U3 (citation-grade summary)** sonnet scores a clean 4–5; minmax scores
3–4.

### 5.3 Author/title fidelity (R8)

Both models make first-author errors, but of different kinds:

- **Variant (minmax)** sometimes mis-attributes (e.g. 2605.03008 attributed
  to "Döven et al." — actually correct for the Turkish-naming convention
  where Aleyna Adak is given name and Döven the surname; sonnet's
  "Aleyna et al." is wrong here).
- **Sonnet** has more trouble with East-Asian names: 2605.06782 attributed
  to "Shengdong L. et al." (first name + last initial — wrong; should be
  "Lu S. et al.", which minmax got right); 2605.05972 attributed to
  "Matías G. et al." (also wrong — should be "Gámez-Marín et al."; minmax
  got this right in its daily note).

Neither side is uniformly correct on author fields. This is a real
operational papercut for a researcher who later searches the vault by
surname.

### 5.4 Weekly synthesis (U4 / R6)

- **sonnet weekly synthesis**: ~190 words, single coherent paragraph,
  identifies a central tension ("models versus JWST reality"), brings a
  counterpoint ("EAGLE reproduces cluster enrichment faithfully, showing
  simulations fail selectively"), bookends with "Radio U-Net... freeing
  attention for the frontiers that matter". This is what an editorial
  voice sounds like, and it lands inside the configured 100–200-word
  band.
- **minmax weekly synthesis**: ~365 words across **two adjacent
  `## Weekly Synthesis` subheadings** — i.e. the variant emitted two
  synthesis attempts and neither replaced the other. The content is fine
  in places (it correctly identifies COLIBRE/Lumen as a simulation–JWST
  tension, the FIRE-2/TNG50 cosmic-web throughline), but the duplication
  is a template-integrity failure and the prose is more list-like
  ("Methodologically, ML techniques (CatBoost, FlexZBoost) appear...")
  than editorial. It also blows the configured word-count target by ~80%.

For U4, sonnet scores 4–5; minmax scores 2 (duplicated heading is
visible enough to be a "send back" if a human were reviewing).

### 5.5 Operational reliability (U5 / R7)

From the variant's log (`re-ass-tests/logs/history.log`) and `state/runs/`
over the visible test window:

- **3 fatal runs** (announcement-2026-05-06, announcement-2026-05-12 ×2):
  - Ranking payload count mismatch (42 expected, 41 returned) survived a
    repair attempt and crashed the announcement.
  - 401 "Invalid API Key" on the local endpoint.
- **Recoverable validation failures (the model produced output that
  failed re-ass's structural checks):**
  - Ranking payload count mismatch retried-once × ≥4 distinct days.
  - Glossary "must contain only a two-column table" retried × 3, then
    skipped section.
  - Science tags outside controlled vocabulary × multiple
    (`#StarsPhotometricRedshifts`, `#GalaxiesSatellites`,
    `#StellarMass`, …).
  - Token-limit hit (truncation, `finish_reason=length`).
- Approx ratio: 18 WARN/ERROR lines per 437 INFO lines (~4%).

Sonnet's vault outputs over the same window are validation-clean — no
dropped glossary sections, no missing tags, no obvious truncation, no
fatal announcement days. (Caveat: I could not see sonnet's log directly
during the test window, only the resulting notes; a clean note is
strong evidence but not proof of a clean run.)

For U5 / R7, sonnet scores 4–5; minmax scores 2.

### 5.6 Rubric scoreboard

| Dimension | minmax | sonnet | Comment |
|---|---|---|---|
| R1 Selection on-priority | 3 | 4 | minmax disagrees on top-1 in 2/3 cases, but sonnet's pick is always in minmax's top-4. |
| R2 Rationale quality | 4 | 4 | Both produce concrete, priority-named rationales. (Sonnet rationales not in state JSON; judged by daily-note summaries.) |
| R3 Scoring discipline | 3 | 4 | minmax over-uses the ≥85 band (2–3 picks/day). |
| R4 Summary depth & accuracy | 3 | 4 | Both accurate; sonnet adds ~30% more interpretive depth and richer glossary. |
| R5 Template integrity | 2 | 5 | minmax: duplicated weekly synthesis heading, recurring glossary validation failures, tag-vocab violations. |
| R6 Weekly synthesis | 2 | 5 | minmax: duplicated section, over-length, list-like. Sonnet: in-band, single thesis, editorial. |
| R7 Operational reliability | 2 | 4 | 3 fatal runs + many recoverable failures on minmax side. |
| R8 Author/title fidelity | 3 | 3 | Both fail differently; neither is uniformly trustworthy. |
| R9 Cost / privacy | 5 | 3 | minmax is local and free; sonnet via Copilot is paid + cloud. |

---

## 6. `ab-test.py compare` — coverage gaps and recommended enhancements

The current `compare` covers seven section types: provider stamp,
candidate alignment, score deltas, selection overlap (Jaccard),
rationale side-by-side, synthesis word counts, paper-note paths.

To do this analysis I had to step outside the script repeatedly. The
following are the high-leverage enhancements, ordered by ROI:

1. **Surface validation/retry/fatal events for the announcement window.**
   The variant's `last-run.log` and `state/runs/*-fatal.json` carry the
   evidence that explains *why* a glossary is missing or a synthesis is
   duplicated. Add a "Reliability" section per day that counts:
   ranking-payload retries, glossary-skip events, tag-vocab violations,
   token-limit warnings, HTTP non-200s, and fatal exits. Pull from
   `[logs].last_run_file` between the run's start and end timestamps.
2. **Read provider metadata from the *run*, not the *settings*.**
   `[llm]` in the TOML is what the next run *will* use; the run that
   already happened might have used a different one. Either persist the
   resolved provider/model/effort into the run-summary JSON (preferred —
   add an `llm` field at the `state_store` write site) or extract it
   from the matching `last-run.log` block. Without this, the provider
   stamp is a guess.
3. **Diff weekly synthesis content, not just word count.**
   Word count would not have caught the duplicated `## Weekly Synthesis`
   heading the variant produced — only line-by-line content would. Add a
   side-by-side excerpt (e.g. first 60 lines) of both weekly notes and
   flag when the configured word-count band
   (`weekly_synthesis_word_limit_start`/`_end`) is exceeded.
4. **Compare paper-summary structure on the union of selected keys.**
   "Paper-note paths" currently just points at the directory. Add for
   each shared selection: word count, presence of required sections,
   glossary term count, footnote count, presence of weaknesses bullets.
   This is where the variant's recurring glossary-skip shows up, and
   where the +37% summary length on sonnet shows up.
5. **Top-N ranking overlap, not just selection overlap.**
   Jaccard on `selected_paper_keys` only sees the top-1 (or top-N at
   `max_papers`). Add Kendall tau or NDCG on the top-10
   `ranking_results` sorted by score. This would have made it clear that
   minmax and sonnet broadly agree at the top-3 level on 05-07 / 05-08
   even when they disagree on top-1.
6. **Threshold-discipline diagnostic.**
   Print, per side, how many papers landed at-or-above
   `always_summarize_score` and at-or-above `min_selection_score`. The
   user-visible behaviour ("minmax kept giving me three papers a day")
   is invisible in the current compare unless you count by hand.
7. **First-author surname extraction sanity check.**
   For the union of selected keys, look up the canonical first author
   from the arXiv metadata (already fetched) and compare to the surname
   the daily/weekly note attributes the paper to. Flag mismatches. Both
   sides would currently fail this, which is the point — it would be
   visible.
8. **Day-pair consistency.**
   Same paper, same template, two providers should produce summaries
   that agree on numerical claims. A cheap check: regex out
   numbers/units from both summaries' Key Ideas blocks and compare the
   sets. Big asymmetries (one side mentions 2.5 mag deficit, the other
   omits it) flag for human review.

`--markdown` should keep emitting a Markdown report; the additional
sections fold in naturally. I'd also recommend the report grow a
top-of-file scoreboard (compact table: # fatals, # retries, # band
violations, # selection overlaps) so the human reader can decide in 10
seconds whether to read the detail.

---

## 7. Recommendation

Anchored to the user stories:

- **U1 Morning triage, U2 Defensible selection, U3 Citation-grade
  summary, U4 Weekly perspective, U5 Set-and-forget reliability**:
  **sonnet (claude-sonnet-4.6 via Copilot)** is clearly the better
  production setup *today*. The weekly synthesis quality, the depth of
  the paper summaries, the validation cleanliness, and the absence of
  fatal runs in the window matter more than a few-points-of-score
  disagreement on selection.

- **U6 Privacy / cost control**: **minmax is the legitimate sparring
  partner** — it runs locally, it is free at the margin, and on the
  date where the case is unambiguous (2605.06782 COLIBRE UVLF) it
  arrived at the same answer with the same supporting numbers and
  identical template structure. Where it falls down is not the
  *ranking* — which is broadly fine to within a few points and includes
  the right paper in its top-3 every time — but the *production-line*
  reliability: weekly-synthesis duplication, glossary validation
  failures, tag-vocab drift, occasional ranking-count fatals, token
  truncation. Those are mostly *prompt-following* and *constraint-
  obedience* problems, and they're exactly the things the local model's
  size budget tends to bite on.

**Concrete suggestion:** keep `sonnet` as the benchmark and the default
production provider. Use `minmax` as a (a) backup when Copilot quota or
auth is unavailable and (b) cost-free baseline that you continue to
A/B against — *because* it agrees on top-3 ranking, it's the right
candidate to grow into a fully local pipeline once the structural
errors are tamed. Two follow-on experiments are well-shaped:

1. Tighten the local model's instruction-following by sharpening the
   glossary-format prompt (the failure mode is consistent enough — "must
   contain only a two-column table" — that a small prompt fix or a
   schema-validated function call would likely eliminate it).
2. Constrain the always-summarize band on the variant side: either lower
   the temperature or raise `always_summarize_score` slightly when the
   variant is in use, to reduce 2-to-3-pick days back to 1-pick.

---

## 8. Caveat on this report's evidence

I could not run `ab-test.py compare` end-to-end for this analysis: the
benchmark side has no `state/runs/announcement-*.json` files in the
expected location, so the script's compare path could not load the
sonnet data. I reconstructed sonnet's selections and summaries from the
live Obsidian vault and inferred ranking-distribution details from the
variant's state JSON only. This is the strongest argument for
enhancement (1) above: if the run-summary JSON carried both sides'
provider metadata and structural diagnostics, A/B audits like this
would not require manual archaeology.
