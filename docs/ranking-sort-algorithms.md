# Sorting algorithms as inspiration for paper ranking

A companion to `docs/ranking-alternatives.md`. The first document focused on
*what to rank against* (embeddings, rubrics, LLMs). This one focuses on *how
to order the candidates once the comparator exists*, taking ideas from
classical sort algorithms and asking which of them, if any, make ranking in
re-ass faster, more accurate, or more reproducible. Section 6 then asks the
same question of the two per-paper passes — glossary and tag generation —
which share the "LLM does everything, app validates after the fact" shape
that the ranker has.

> **Note on sources.** I could not reach
> `https://tools.simonwillison.net/sort-algorithms` from the sandboxed
> environment this work runs in (host not in the outbound allowlist, 403 on
> direct fetch). The inventory below is the canonical set such visualisers
> cover; if the linked page shows a variant I have missed (e.g. block sort,
> smoothsort, library sort), point me at it and I'll fold it in.

---

## 1. What "sorting" actually means in re-ass

A classical sort assumes its comparator is **cheap, deterministic, and
total**:

- *Cheap.* `a < b` is one CPU instruction.
- *Deterministic.* Same inputs → same answer, every time.
- *Total.* For every pair, exactly one of `a < b`, `a > b`, `a == b` holds,
  and the relation is transitive.

In re-ass none of these is automatically true:

| Comparator | Cheap? | Deterministic? | Total / transitive? |
| --- | --- | --- | --- |
| BM25 score | yes | yes | yes (after tie-break) |
| Embedding cosine | yes | yes | yes (after tie-break) |
| LLM pairwise verdict | **no** (network call) | **no** (sampling, position bias) | **no** (can be cyclic) |
| LLM listwise 0-100 score | **no** | **no** | yes (after sort), but scale drifts |

This changes which sort algorithms matter. The figure of merit is no longer
"comparisons" in the abstract — it is **how many *expensive* (LLM)
comparisons are needed**, given that cheap (embedding) comparisons are
effectively free. Most of the value in this exercise is identifying which
algorithmic ideas minimise LLM comparator calls while staying robust to a
noisy/non-transitive comparator.

The current `PaperRanker` does not really sort at all: it asks the LLM to
emit a fully-ordered list in one shot, then sorts by the model's reported
score. Every algorithm below is an alternative to that "one-giant-call"
shape.

---

## 2. The algorithms, and what (if anything) they bring

For each algorithm I've recorded the underlying idea, the fastest variant
or recommended form, and an honest assessment of whether the idea
transfers to abstract ranking. Algorithms with no useful translation are
included briefly for completeness so the audit is exhaustive.

### Fastest / most robust in the abstract

The two algorithms that practical language runtimes actually ship with
are the right reference points for "fast and robust":

- **Timsort** — Python's `list.sort`, Java's `Arrays.sort` for objects,
  V8, Rust's stable sort. Hybrid of natural-run detection + insertion
  sort on small runs + balanced merge. O(n log n) worst, O(n) on
  already-sorted input, stable.
- **Introsort** — C++'s `std::sort`, .NET's `Array.Sort`. Quicksort with
  a depth limit; falls back to heapsort to guarantee O(n log n) worst,
  and to insertion sort for tiny partitions. Not stable.

Everything else is either a specialist (radix, counting, bucket) or a
teaching tool (bubble, gnome, cocktail). Quicksort and heapsort underpin
introsort; merge sort underpins timsort.

### 2.1 Bubble sort

**Idea.** Repeatedly walk the list swapping adjacent out-of-order pairs.
O(n²) comparisons. Stable.

**Transfer to re-ass.** None worth keeping. Every comparison is an
adjacent LLM call; n² of them is unaffordable. The one salvageable
sub-idea is the *early termination* check ("if a full pass made no
swaps, stop"), but this is a generic property of any iterative refinement
and doesn't need bubble sort as a vehicle.

### 2.2 Insertion sort

**Idea.** Build the output by inserting each element into its correct
position among the already-sorted prefix. O(n²) worst, **O(n) on nearly
sorted input**, stable. With binary search for the insertion point:
O(n log n) comparisons, O(n²) shifts.

**Transfer.** The "nearly sorted input" property is the killer. If the
embedding stage gives a ranking that is mostly right with a handful of
local errors, **binary-insertion-sort with an LLM comparator** corrects
only the disputed positions. Each "is X better than Y?" call is a
single-token pairwise judgment, and we only make O(log n) of them per
candidate that's actually misplaced.

This is one of the more compelling ideas in the catalogue — see proposal
**R1** below.

### 2.3 Selection sort

**Idea.** Find the minimum, swap to the front, repeat. O(n²)
comparisons, but only **O(nK) to find the top K**.

**Transfer.** Useful framing for "find today's top paper" specifically.
If you only need *one* paper for the daily note, selection-style
extraction needs n-1 comparisons rather than a full sort. With an LLM
comparator that is still too many — but with a cheap embedding scorer it
is the natural shape of the daily-top-paper choice. Doesn't beat just
running argmax on a numeric score, so the algorithm itself is not what
adapts — the *partial-sort framing* does.

### 2.4 Merge sort

**Idea.** Recursively split, sort halves, merge. O(n log n), stable,
predictable, **easy to parallelise** because the two halves are
independent.

**Transfer.** This is what RankGPT and friends in the literature do:
slide a window over the candidates, have the LLM emit an ordering for
that window (an in-place k-way sort of the window), then *merge* the
windows. The merge step is itself a sequence of small LLM listwise
calls. Matches doc-1's **3b (comparator/RankGPT)** almost exactly. Merge
sort is the *control flow* underneath that proposal.

### 2.5 Quicksort

**Idea.** Pick a pivot, partition the list into ≤ pivot and ≥ pivot,
recurse. O(n log n) average, O(n²) worst, not stable. Sensitive to pivot
choice.

**Transfer.** Brittle with a non-transitive comparator (one bad LLM
verdict near the pivot can put many papers in the wrong partition).
Recovers via median-of-three / median-of-medians pivot selection, but at
that point the simpler ideas (heap, merge) are cleaner.

The genuinely useful spin-off is **quickselect**: the same partitioning
machinery applied to find the top-K in expected O(n) comparisons without
fully sorting. With a *cheap* comparator (embedding) this is exactly the
"prefilter to top-K" pattern from doc-1 **3a**. Quickselect is the right
mental model for that step.

### 2.6 Heapsort

**Idea.** Build a binary heap, repeatedly extract the max. O(n log n)
guaranteed, in-place, not stable.

**Transfer.** A bounded-size heap is the right data structure for
**streaming top-K**: keep the K best papers in a min-heap, for each
incoming candidate compare against the heap root; if worse, discard; if
better, replace and re-heapify. Per-candidate work is O(log K).

This becomes interesting when the comparator on the heap boundary is the
LLM and everything else is the embedding score — you only spend LLM
calls on candidates whose cheap score puts them near the cut line.
Maps to doc-1's **3d (boundary band)**.

### 2.7 Shell sort

**Idea.** Insertion sort with progressively shrinking gaps; coarse then
fine.

**Transfer.** The "coarse then fine" framing is real but is better
expressed by intro sort, bucket sort, or just embedding-then-LLM. Shell
sort itself adds nothing.

### 2.8 Cocktail / bidirectional bubble, comb sort, gnome sort

Variations on bubble. Same verdict: no transfer.

### 2.9 Timsort

**Idea.** Hybrid optimiser. (1) Scan for natural runs (maximal
already-sorted subsequences, ascending or descending). (2) Use insertion
sort to extend small runs up to a minimum size. (3) Merge runs with a
balanced merge strategy maintained by a stack invariant. (4) The merge
itself uses *galloping mode* — when one side is consistently winning,
binary-search ahead instead of stepping one element at a time.

O(n log n) worst, **O(n) on already-sorted or reverse-sorted input**,
stable, the production sort in Python and Java for object arrays.

**Transfer.** This is the single most relevant algorithm in the
catalogue. The embedding-ranked list is almost-certainly a
nearly-sorted approximation of the true ranking. Timsort's two key
moves — *find runs and trust them*, *merge runs with the comparator
only at the borders* — translate directly:

1. Sort all candidates by embedding similarity → noisy global order.
2. Identify "confident runs" where adjacent embedding scores differ by
   more than a threshold τ (i.e. the cheap score is unambiguous in this
   stretch). Accept their internal order without invoking the LLM.
3. The remaining "ambiguous" stretches between runs are where adjacent
   embedding scores are tied or near-tied. Resolve those with LLM
   pairwise or LLM listwise on the short stretch.
4. Galloping → when the LLM consistently prefers items from one side of
   a merge, skip ahead and trust the embedding order on that side.

This is proposal **R1** below. It is the cleanest way to spend
LLM calls *only where they are needed*.

### 2.10 Introsort

**Idea.** Start with quicksort. Track recursion depth. If depth exceeds
~2·log₂(n), bail out to heapsort to guarantee O(n log n). For tiny
sub-arrays, switch to insertion sort.

**Transfer.** The transferable idea is **algorithmic escalation**: start
with the cheapest method, monitor a quality signal, escalate to a more
expensive method only when needed. In re-ass terms:

- Start with embedding similarity.
- If the top-K papers are *well separated* (large score gaps, no
  near-ties), stop — embedding alone is enough.
- If the top-K is *contested* (tight cluster, near-ties at the cut),
  escalate the contested band only to LLM pairwise or LLM listwise.

This is the "intro sort of ranking". Maps to proposal **R6**.

### 2.11 Radix sort

**Idea.** Sort by one digit/feature at a time, least-significant first
(LSD) or most-significant first (MSD). O(n·k) where k is the number of
digits. Not comparison-based.

**Transfer.** Radix's lesson for ranking is **multi-criteria
lexicographic ordering**. Today the ranker conflates "matches science
priorities", "matches method priorities", "is in a preferred category",
and "is recent" into a single 0-100 score that the model has to balance.
Splitting these into independent passes is cleaner:

- Pass 1: hard filter on `primary_category` / `categories`.
- Pass 2: rank by max embedding similarity to science priorities.
- Pass 3: within the science-survivors, rank by max embedding similarity
  to method priorities.
- Pass 4: LLM tie-break on the top K survivors.

Each pass is independently testable. This is proposal **R4**.

### 2.12 Counting sort

**Idea.** When values are small integers in a known range, count
occurrences of each value and emit. O(n + k).

**Transfer.** Almost nothing — relevance scores are continuous. The one
edge case is *score bucketing*: if scores are rounded into a handful of
bands (e.g. quintiles), papers within a band become equivalent and ties
can be broken cheaply (alphabetical, recency, arXiv ID). Already how the
existing `always_summarize_score` / `min_selection_score` banding
implicitly works.

### 2.13 Bucket sort

**Idea.** Partition into buckets by range, sort each bucket
independently, concatenate. O(n + k) average if input is uniform.

**Transfer.** Maps to **cluster-then-rank** (doc-1 **3e**). Embed
candidates, cluster by topic, rank within each cluster, take the top of
each cluster. Useful for *diversity-preserving* selection — guarantees
the daily set isn't five papers on the same sub-topic.

### 2.14 Patience sort

**Idea.** Deal cards onto piles, each pile kept in decreasing order;
when no pile fits, start a new one. The number of piles equals the
length of the longest increasing subsequence. Reading the piles back
with a min-heap yields a sorted output.

**Transfer.** Niche. The interesting property is that **patience-style
analysis of the embedding-sorted list identifies the longest monotone
subsequence**, i.e. the largest core of papers whose embedding order is
self-consistent. Anything in that core can be trusted; anything off the
core is a candidate for LLM review. Theoretically appealing, harder to
implement than timsort run detection for the same end result.

### 2.15 Tournament sort

**Idea.** Run a single-elimination tournament. The winner is found in
n - 1 comparisons. The second-place finisher must have lost to the
winner, so only log n candidates need to be re-examined; the third-place
finisher only needs to be checked against those who lost to the first
two; etc.

**Transfer.** Very interesting in an LLM setting because **every LLM
pairwise comparison is reusable** — you've paid for that judgement, it
should contribute to every downstream decision it touches. Tournament
sort is the simplest realisation of "reuse all comparison data". A more
sophisticated version is *ELO / Bradley-Terry rating*: each LLM pairwise
verdict updates a global rating, the final ranking is by rating, and a
small fixed budget of comparisons (chosen to maximally reduce
uncertainty, e.g. pair the items whose ratings are closest) is enough.

This is proposal **R5**.

---

## 3. Five concrete proposals

These build on (and reference) the proposals **3a-3e** in
`docs/ranking-alternatives.md`. Numbering continues from there: R1, R4,
R5, R6 are new; R2 and R3 are restatements of doc-1 proposals through a
sort-algorithm lens, included so the assessment matrix at the end is
complete and self-contained.

### R1. Timsort-style run-and-merge with LLM at borders

The headline proposal. Algorithmically the closest thing to
"replace listwise scoring with the actual best-in-class sort idea".

**Shape.**

1. Compute embedding similarity per candidate → cheap noisy global
   ordering.
2. Walk the ordering top-to-bottom. Emit a *run boundary* wherever the
   gap between adjacent embedding scores exceeds τ (a configurable
   confidence threshold). Items within a run are treated as
   indistinguishable by the cheap scorer.
3. **No LLM call inside a run.** The embedding order is accepted (or
   broken deterministically: by recency, then arXiv ID — see
   Reproducibility below).
4. **At every run boundary** the LLM is asked to confirm which run wins
   — typically a 2-3 paper pairwise/setwise prompt. Boundaries near the
   selection threshold (`min_selection_score`) get priority; deep-tail
   boundaries that can't possibly affect selection can be skipped.
5. Galloping: when the LLM has confirmed three boundaries in a row in
   embedding-agreement, the rest of the tail is accepted without further
   LLM calls.

**Maps to doc-1.** Combines **3a (cascade)** and **3b (comparator)** —
the cascade is *adaptive*: K isn't fixed, it's wherever the runs say it
is.

**Pipeline removal.** `_apply_dual_match_cap` (no absolute score scale
to police), the finalist re-rank (no batch calibration drift to fix),
`_split_into_batches` (one LLM session per boundary, all small), most of
the JSON repair path (responses are tiny). `_parse_ranked_payload`
shrinks to "parse a short ordering".

### R2. Embedding cascade + LLM listwise on top-K  *(doc-1 3a)*

Restated here for the assessment matrix. Cheap embedding score, take
top-K, send the K to the existing `PaperRanker` unchanged.

### R3. Quickselect prefilter + comparator on survivors  *(doc-1 3b + 3a)*

Use embedding similarity with quickselect to reduce N → K in expected
O(n). Then RankGPT-style merge sort with an LLM listwise comparator over
the K survivors.

### R4. Radix-style multi-pass lexicographic ranking

Replace one combined score with a pipeline of single-purpose passes:

1. **Hard filter** (deterministic): category match, recency.
2. **Science similarity rank** (embedding): cosine to each science
   priority, weighted by 1/i; keep top X.
3. **Method similarity rank** (embedding): same, against method
   priorities; survivors must clear a floor on this *and* on science.
4. **LLM tie-break** on the surviving K, only if K is still bigger than
   the daily summary budget.

Each pass is independently testable; the dual-match rule from
`preferences.md` becomes explicit (one pass enforces it) instead of
being a clamp on a model-emitted score.

**Pipeline removal.** Eliminates `_apply_dual_match_cap` (its job is now
the science-and-method floor in passes 2-3), and almost all of the
score-validation logic — there's no listwise 0-100 to validate.

### R5. Tournament / Bradley-Terry pairwise budget

The LLM never produces a score and never produces a full ordering.
Instead:

1. Cheap embedding score gives an initial rating per paper.
2. A fixed budget of pairwise LLM comparisons (e.g. 2N or 3N) is spent
   on the *most uncertain* pairs — typically adjacent in the embedding
   order, or any pair whose ratings put them within ε of the selection
   threshold.
3. Each comparison updates a Bradley-Terry / ELO rating.
4. Final ranking is by rating.

Reusable signal across days: ratings can be persisted in
`state/papers/` and carried forward. Two papers compared today inform a
similar pair next week.

**Pipeline removal.** Everything inside `_rank_candidates_batch_once`,
the repair loop, the dual-match cap. Replaces them with a small
pairwise client and a rating store.

### R6. Introsort-style adaptive escalation

Algorithmic switching driven by the *spread* of the cheap score:

1. Run BM25 / embedding scoring.
2. Inspect the top of the distribution. If the gap between the K-th and
   (K+1)-th paper is greater than τ, **stop** — no LLM needed.
3. If the gap is too narrow (contested cut), escalate the contested
   slice only to a comparator from R1/R5.

In the easy case (clear winners) the LLM is never called for ranking.
In the hard case, the LLM is called for exactly the papers whose order
is genuinely uncertain.

**Pipeline removal.** On easy days, removes the entire LLM ranking
pass. The fallback path is whatever cheaper comparator R1/R5
implements.

---

## 4. Assessment matrix

Rubric: **Quality** (top-K accuracy vs current), **Speed** (wall clock
per run), **Robustness** (handles noisy / non-transitive verdicts,
provider hiccups, varying N), **Reproducibility** (same input → same
output across runs), **KISS** (simple to implement, debug, explain),
**DRY** (reuses existing pipeline components / data).

5 = strong, 3 = neutral, 1 = weak. Estimates, not measurements.

| Proposal | Quality | Speed | Robustness | Reproducibility | KISS | DRY |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Current (LLM full-pool listwise) | 4 | 2 | 2 | 2 | 3 | 5 |
| **R1.** Timsort-style run-and-merge | **5** | **5** | **4** | **4** | 3 | 4 |
| **R2.** Embedding cascade + LLM rerank top-K (doc-1 3a) | 4 | 4 | 4 | 4 | **5** | **5** |
| **R3.** Quickselect + comparator (doc-1 3a+3b) | 4 | 4 | 4 | 3 | 3 | 4 |
| **R4.** Radix-style multi-pass | 4 | **5** | 4 | **5** | 4 | 4 |
| **R5.** Tournament / Bradley-Terry | 4 | 3 | 4 | 4 | 2 | 3 |
| **R6.** Introsort-style escalation | 4 | **5** | 4 | 4 | 4 | 4 |
| Pure embedding only (doc-1 2b) | 3 | **5** | **5** | **5** | **5** | 4 |
| Pure BM25 only (doc-1 2a) | 2 | **5** | **5** | **5** | **5** | 3 |

### Notes on the scoring

- **Current** loses on speed and robustness because its complexity all
  comes from working around LLM output truncation, JSON drift, and
  scale instability — none of which would exist with a better
  algorithmic shape.
- **R1 (timsort)** is strong on quality because the LLM is spent exactly
  where its judgement helps (run borders), strong on speed because most
  pairs are decided by embedding, and strong on robustness because run
  detection is local — a single bad LLM verdict perturbs one boundary,
  not the whole order. KISS suffers slightly because there are more
  moving parts than R2.
- **R2** is the simplicity champion and the obvious starting point;
  R1 / R4 are the better steady-state shapes once R2 is bedded in.
- **R4 (radix)** scores top on reproducibility because every pass is
  deterministic except (optionally) the last LLM tie-break, and each
  pass is independently testable. The dual-match rule moves from "LLM
  obeys a scoring guide, app caps the score afterwards" to "pass 3
  enforces it directly".
- **R5 (Bradley-Terry)** has the best long-run learning story (signal
  accumulates across days) but is the most complex single change and
  has the most novel failure modes. KISS is the obvious cost.
- **R6 (introsort escalation)** wins on speed in the easy case (no LLM
  at all) and degrades gracefully to R1 in the hard case. Worth
  combining with R1, not competing.
- **Pure embedding** would be a strong baseline and a useful **floor**
  even if not the final architecture — every proposal here treats
  embedding similarity as the cheap recall stage.

---

## 5. Recommendations

1. **R2 first, behind a flag.** Same recommendation as doc-1. It's the
   smallest reversible step, gives an embedding scorer that all other
   proposals need, and starts producing the data you'll want to tune
   R1/R4 against (in `state/runs/*.json`).

2. **R1 (timsort run-and-merge) next, as the steady-state target.** Of
   all the sort-algorithm-inspired ideas, this is the one that actually
   addresses every pain point in `ranking.py`: it removes the listwise
   output (so no JSON-repair stack), removes the 0-100 scale (so no
   dual-match cap), removes finalist re-ranking (no batch drift to fix),
   and spends LLM calls precisely where embedding similarity is
   genuinely ambiguous. It also corresponds to what the most-deployed
   real sort algorithm in the world actually does, so the engineering
   precedent is excellent.

3. **R6 (introsort escalation) on top of R1.** Free additional latency
   and cost win on "easy" days where the embedding score clearly
   separates the top papers. Implementation is small once R1 exists
   (it's the early-exit before invoking R1's LLM merge step).

4. **R4 (radix multi-pass) is worth considering as an alternative to
   R1 rather than alongside it.** It's the cleanest expression of the
   dual-match rule (one pass per group, no clamping after the fact) and
   the most reproducible architecture in the matrix. The trade-off is
   that the LLM only sees the survivors of two cheap filters — if those
   filters get the dual-match logic wrong, no later step recovers. R1
   is more forgiving because the LLM sees the whole ambiguous region.

5. **R5 (Bradley-Terry) only after R1/R2/R4 are in place.** It needs an
   accumulated history of pairwise verdicts to be worth the engineering,
   and it complicates KISS more than any other option. Park it as a
   "phase 3" idea if cross-run learning is ever a goal.

6. **Don't ship pure embedding or pure BM25 as the only ranker.**
   They're the right *base layer* and the right *backtest baseline*,
   but the LLM's rationale is part of the daily-note value, and
   abandoning the LLM entirely loses that. Keep the LLM in the
   architecture; just put it where it earns its tokens.

### What the order of work could look like

```
phase 0 (already done)
└── docs/ranking-alternatives.md             — articulate the design space
└── docs/ranking-sort-algorithms.md          — this file

phase 1 (low risk, reversible)
├── add EmbeddingScorer (priorities embedded once, cached by hash)
├── add `ranking.prefilter = "embedding" | "off"` config flag
├── R2: embedding prefilter → existing PaperRanker over top-K
├── backtest harness over state/runs/*.json — confirm parity
└── T1: reuse the embedder for science-tag selection (see §6)

phase 2 (the real win)
├── R1: run-and-merge with LLM comparator at borders
├── retire _apply_dual_match_cap, _split_into_batches, finalist re-rank
└── shrink _parse_ranked_payload to ordering-only

phase 3 (optional)
├── R6: skip the LLM entirely when the cheap-score top-K is well separated
└── R4 alternative path if multi-pass turns out cleaner than run-and-merge

phase 4 (research-grade)
└── R5: persistent Bradley-Terry rating across days
```

The first two phases would give re-ass a ranker that is faster, cheaper,
more reproducible, and meaningfully more robust than today's, with the
LLM doing only the work it's actually good at — judging close calls,
not assigning calibrated 0-100 scores to a hundred papers in one shot.

---

## 6. Beyond ranking: tags and glossary

The per-paper pipeline runs two more LLM passes after the summary is
generated: a **glossary** pass (`generate_glossary`,
`build_glossary_prompt`, `validate_glossary_section`, and the
preserve-last-candidate retry loop in `call_glossary_llm_with_retry`)
and a **tag** pass (`generate_tags`, `build_tags_prompt`,
`validate_tags_section`, `normalise_tags_section`, with
`build_fallback_tags` as a regex-substring safety net). Both share the
same architectural shape as the ranker today: one LLM call expected to
satisfy strict format rules, with after-the-fact validation and a
fallback when that fails. The question is whether any of the ideas in
sections 2-5 apply to them, or whether the difference is in the noise.

The honest answer: **half of one of the two passes is a genuine win;
the rest is in the noise.**

### 6.1 The tag pass is two tasks in one prompt

`build_tags_prompt` asks the model for two hashtag lines:

1. **Proper-noun line.** Up to 5 hashtags for telescopes, surveys,
   datasets, missions, instruments, models, software, or named
   catalogues mentioned in the summary. There is no authority list —
   this is free-form named-entity extraction.
2. **Science-keyword line.** Up to 5 hashtags chosen *only* from a
   supplied keyword allowlist (parsed by `iter_keyword_tags`), with
   `reject_unknown_science_tags=True` enforcing this in
   `normalise_tags_section`. This is **multi-label classification
   against a fixed taxonomy**.

These two tasks have very different best tools:

| Task | Best tool | Why |
| --- | --- | --- |
| Proper-noun extraction | LLM | Open-vocabulary, contextual, no taxonomy to score against |
| Science-keyword selection | Embedding similarity | Closed taxonomy, every candidate is known in advance, paraphrase-tolerant |

`build_fallback_tags` already implements a poor-man's version of the
embedding approach: it strips non-alphanumerics from each allowlist tag
and checks whether the words appear in the normalised summary. This
misses every paraphrase ("supermassive black hole" vs `#SMBH`,
"intracluster light" vs `#ICL`) and every synonym. Replacing the
substring check with embedding cosine similarity is a strict upgrade
on the same task.

### T1. Embedding-based science-tag selection

The natural application of doc-1's section 2b (and a small piece of R6)
to the science half of the tag pass.

**Shape.**

1. At setup (or when the keyword file's hash changes), embed each
   allowlist science tag once, treating the tag word(s) plus any
   surrounding context heading as the text. Cache the embeddings.
2. At paper time, embed the summary (or just the abstract +
   subheadings) once.
3. Score each allowlist tag by cosine similarity to the summary
   embedding. Keep the top-5 above a floor.
4. **Escalation (R6 applied locally):** if the top-5 are tightly
   clustered (small gap to the 6th, 7th, …), pass that ambiguous slice
   to the LLM as a *short* listwise prompt — "given this summary, pick
   the best 5 of these 8 hashtags". Most papers won't trigger this.
5. The proper-noun line is left exactly as it is today: small
   dedicated LLM call, simple validation, no allowlist.

**Why it's a real win, not noise.**

- Local models are at their worst on "pick exactly from this list"
  problems. The current
  `reject_unknown_science_tags=True` + retry + repair + fallback stack
  exists because they don't reliably obey allowlist constraints.
  Embedding similarity *cannot* go off-allowlist by construction.
- Paraphrase coverage strictly improves over `build_fallback_tags`.
- The science-tag step becomes deterministic and reproducible across
  runs.
- The proper-noun half no longer shares its fate with the science
  half — a flake in one doesn't lose the other.
- Cost: zero recurring LLM tokens for the science half on easy papers;
  a small contested-slice LLM call on hard ones.

**What gets removed.**

- `build_fallback_tags` and `_normalise_search_text` /
  `_split_tag_words` (the regex-substring matcher it relies on).
- `reject_unknown_science_tags=True` and the associated
  "dropped science tags" path in `normalise_tags_section` — the
  classifier can't produce off-list tags.
- Most of `validate_tags_section` — structural validation of the
  hashtag block remains useful, but allowlist enforcement is gone.
- A retry attempt: the proper-noun call is short and rarely fails;
  the science half doesn't go through the LLM at all on easy papers.

**Cost / risk.** The embedding similarity tags depend on a
representation that wasn't trained on the user's specific allowlist.
Worth backtesting against the last few weeks of generated tags before
flipping the default. Low-similarity-floor tuning is the main knob.

### 6.2 The glossary pass — mostly noise

The glossary task is fundamentally generative: identify which terms in
*this specific summary* are specialised, then write a one-sentence
definition. There is no external taxonomy to match against, no list
to filter, no ordering to produce. Embedding similarity has nothing to
compare *to*. Run-and-merge, quickselect, multi-pass radix — none of
them have an obvious lever here. The task is one paper, ~5-12 short
rows, single LLM call; `call_glossary_llm_with_retry`'s
preserve-last-candidate pattern is already about as KISS as it gets.

The one *adjacent* idea that would actually help is **persistent
caching across papers**. Common acronyms (`SMBH`, `AGN`, `JWST`,
`IllustrisTNG`, `SHARK`, `SHAM`, `HOD`) get re-defined for every
paper. A cross-run glossary cache keyed by term — stored under
`state/` next to the existing per-paper records — would let each new
paper's LLM call write definitions only for genuinely new terms, with
known terms looked up.

This is a state-store optimisation, not a sort-algorithm idea, and
borrows nothing from sections 2-5 except the spirit of "only spend
tokens where signal is genuinely new". Worth it only if the glossary
call ever becomes a noticeable slice of total summarisation time. For
now: skip.

### 6.3 Assessment

The rubric used in section 4, applied to the per-paper passes:

| Proposal | Quality | Speed | Robustness | Reproducibility | KISS | DRY |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Current tags (LLM + validation + regex fallback) | 3 | 3 | 2 | 2 | 3 | 3 |
| **T1.** Embedding science tags + LLM proper nouns | **5** | **5** | **5** | **5** | 4 | **5** |
| Current glossary (LLM + retry, preserve last candidate) | 4 | 4 | 4 | 4 | **5** | 4 |
| Cross-run glossary cache | 4 | **5** | 4 | 4 | 3 | 3 |

T1 scores high on DRY because it reuses exactly the embedder built for
R2. The cross-run glossary cache scores lower on KISS / DRY because it
introduces a new persistent artefact for marginal benefit — listed for
completeness, not as a recommendation.

### 6.4 Recommendation

- **Adopt T1 in phase 1**, immediately after the R2 embedder lands. It
  reuses the same scorer and removes more code than it adds.
- **Leave glossary generation alone.** The methods in this document
  don't apply to a fundamentally generative task. Revisit only if the
  glossary call becomes a measurable cost; if it does, the right
  intervention is a state-store cache, not a sort-algorithm idea.
