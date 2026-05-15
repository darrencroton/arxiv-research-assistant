# Paper ranking — current approach and alternatives

A working document capturing the current ranking architecture, why it looks the
way it does, and a menu of alternative or hybrid approaches that could replace
parts of it. The goal is to make the trade-offs explicit before committing to
a redesign.

---

## 1. Current process

`src/re_ass/ranking.py` — `PaperRanker.rank_papers`.

Per arXiv announcement day:

1. **Build the candidate payload.** Every fetched paper contributes
   `{candidate_id, title, abstract, primary_category}` to a JSON array.
   Candidate IDs are short local labels (`C001`, `C002`, …) generated per run
   to keep the prompt compact.
2. **Build the prompt.** A system prompt plus a user prompt that contains:
   the rubric (rules + 0-100 scoring guide), the user's ordered priorities
   (flat list, or split into `<science_priorities>` and `<method_priorities>`),
   and the candidates JSON.
3. **One LLM call covers the entire pool.** The model is asked to score
   *every* candidate from 0 to 100, attach a one-sentence rationale, and
   (when both priority sections are present) emit boolean
   `science_match` / `method_match` flags. Output must be a single JSON
   object sorted from highest to lowest score.
4. **Optional batching** (`[llm].ranking_batch_size`, default `0` = off).
   When enabled, candidates are split into evenly-sized batches; each batch
   is ranked independently; then a **finalist re-rank** pass takes all
   candidates above `min_selection_score - 10` and re-orders them in one
   final LLM call to recalibrate across batches.
5. **Validation and recovery.**
   - `_parse_ranked_payload` enforces shape, score range, completeness,
     and no-duplicates.
   - On failure, alternating "retry" and "repair" calls run up to
     `_MIN_RANKING_VALIDATION_ATTEMPTS = 4` times (or `2 ×
     config.retry_attempts`, whichever is larger). The repair prompt feeds
     the invalid response and the validator's error message back to the
     model and asks for a fix.
   - If a batch is still invalid after retries and is large enough,
     `_split_for_recovery` halves it and recurses.
6. **Deterministic post-processing.**
   - `_apply_dual_match_cap`: any paper with `science_match=False` or
     `method_match=False` whose score is above 69 is clamped to 69. Capped
     paper keys are tracked so they can still surface in the weekly
     "interest" list.
   - Final sort by `(-score, title)`.
   - **Selection bands:**
     - `always_summarize_score` (default 85) — auto-selected for full
       summary.
     - `min_selection_score` (default 70) — clears the bar; eligible for
       overflow into weekly interest.
     - If nothing clears `always_summarize_score`, `_ABOVE_MIN_FILL_COUNT
       = 1` top paper from the above-min pool is promoted so the daily
       note is never silently blank.
7. Returns a `RankingSelection` containing the full ranked list, the
   selected set, and the weekly-interest overflow.

### Rationale for the current shape

- **One LLM pass keeps calibration in one head.** Asking the model to
  produce a single sorted list over all candidates avoids cross-batch
  drift in the simple case.
- **Dual-match scoring guide** encodes the user's actual taste: "strong
  fit" requires both a science hook *and* a method hook when both sections
  are configured. The post-hoc cap exists because the model does not
  reliably obey the scoring guide on its own.
- **Repair / split-recovery loops** exist because long structured outputs
  are the failure mode — the model truncates JSON, drops candidates, or
  invents IDs when the list is large.
- **Per-batch + finalist re-rank** lets local models with small context
  or output windows still produce a coherent global ordering.

### What it costs

- **Output tokens are the bottleneck.** N candidates × (rationale + score
  + flags) is what blows up. Every workaround in the file
  (`_MIN_RANKING_VALIDATION_ATTEMPTS`, JSON repair, `_split_for_recovery`,
  finalist re-rank) traces back to that root cause.
- **Unstable 0-100 calibration.** `_apply_dual_match_cap` and the
  finalist re-rank are both symptoms of the model not respecting an
  absolute scale.
- **Position bias.** Prompt order influences scores; nothing currently
  mitigates this.
- **Free signal ignored.** arXiv categories, cross-listings, and the
  user's own past selections (`state/papers/`) are not used.

---

## 2. Pure non-LLM baselines

These are useful as reference points and as the cheap stage of a hybrid.
None of them is being proposed as a wholesale replacement on its own; the
LLM's value as a *rationale generator* is real even if its value as a
*ranker* is contested.

### 2a. BM25 / TF-IDF over title + abstract

Build a query from the priority text (optionally weighted by 1/i for
"earlier matters more"); score each paper with BM25.

**How it fits in.** Stand-alone scorer that produces a numeric relevance
score per candidate. Plugs in upstream of the existing threshold/cap
selection.

**What it gives us.** Deterministic, ~milliseconds per run, zero
recurring cost. In astronomy the priority vocabulary is precise (`LRDs`,
`SHAM`, `HOD`, `ICL`, `IllustrisTNG`) so token overlap is unusually
informative.

**What it doesn't give us.** Paraphrase ("compact red sources at z > 6"
vs "LRDs"), dual-section logic (science AND method) is awkward to
express, and BM25 has no natural rationale string.

**Pipeline removal if adopted alone.** Removes the entire LLM call,
JSON-repair loop, split-recovery, finalist re-rank, and dual-match cap.
But loses rationales and is likely to under-perform on novel phrasing.

### 2b. Dense embedding similarity

Embed each priority sentence once (cache by hash of the priority text);
embed each paper's title + abstract per run; score = weighted sum or max
of cosine similarities. Cheap embedding model (local or hosted).

**How it fits in.** Same plug point as BM25, but expresses the rubric
better:
- Earliest-priority weighting → coefficient on each priority embedding.
- Dual-match → score = `(max_sim_science ≥ τ_s) AND (max_sim_method ≥
  τ_m)`, or treat science / method as two scores combined multiplicatively.
- Selection bands → percentile or absolute thresholds on the combined
  score.

**What it gives us.** Handles paraphrase naturally, expresses the
dual-section rubric cleanly, cents-per-month operating cost (or zero with
a local embedder), no JSON to repair.

**What it doesn't give us.** Rationales, fine-grained ranking inside a
tight cluster, anything beyond what's in the abstract.

**Pipeline removal if adopted alone.** Same as BM25: the entire LLM
ranking stack goes. Probably gets 80% of current quality on astro-ph at
~zero recurring cost — worth a backtest before committing.

### 2c. Learned ranker over user history

`state/papers/` already records what the user accepted vs skipped. Over
time that becomes a labelled dataset. Train a small logistic regression
or gradient-boosted tree on TF-IDF or embedding features.

**How it fits in.** Replaces or augments the score from 2a / 2b once
there's enough history.

**What it gives us.** Adapts to the *actual* user, not the *stated*
preferences. Captures preferences that are hard to articulate.

**What it doesn't give us.** Anything useful in the cold-start period.
Needs the embedding / BM25 path to exist first to bootstrap features.

**Pipeline removal if adopted.** None on its own — this is an addition,
not a replacement.

---

## 3. Hybrid approaches

The pattern production search and RAG systems converge on: **cheap recall
→ expensive rerank**. Each variant below preserves the LLM's role
*somewhere* in the pipeline, just smaller and better-aimed.

### 3a. Embedding retrieval + LLM rerank on top-K (cascade)

**Shape.** Score all N candidates by embedding similarity (2b). Take the
top K (e.g. K = 3× the number of papers that could ever be summarised in
a day, plus a "rescue band" of anything with absolute similarity above a
generous threshold). Send only those K to the existing `PaperRanker`
loop, unchanged.

**How it fits in.** New `EmbeddingPrefilter` upstream of `PaperRanker`,
gated by a setting (`ranking.prefilter = "embedding" | "off"`). The rest
of the pipeline is untouched. Cleanly reversible behind a config flag.

**What gets removed / simplified.**
- `_split_into_batches` / `ranking_batch_size` / finalist re-rank
  effectively become dead code at K ≈ 20-30 (a single LLM call always
  fits).
- `_split_for_recovery` becomes much rarer — a small candidate list
  rarely overruns the output budget.
- The repair loop stays (cheap insurance) but fires less often.
- `_apply_dual_match_cap` stays — calibration is still listwise here.

**Cost / risk.** False-negatives at the cheap stage are the main risk;
the rescue band mitigates. Highest impact for least architectural change.

### 3b. Pairwise / setwise LLM comparator (RankGPT-style)

**Shape.** Replace the listwise 0-100 score with a comparator-style rank
over the K survivors of 3a. Either:
- **Sliding window listwise.** Present windows of ~10 candidates, ask
  the model to emit an ordering, slide and merge.
- **Tournament / merge-sort.** O(K log K) pairwise calls, each prompt is
  a few hundred tokens, output is a single token ("A" or "B").

The model never produces a number; ranking is by ordinal position. A
separate small call (or a deterministic embedding check) flags
`science_match` / `method_match` on the survivors.

**How it fits in.** Replaces `_rank_candidates_batch_once` for the
top-K. Best paired with 3a so K is small (K ≤ ~30).

**What gets removed / simplified.**
- The entire 0-100 scoring guide and the dual-match cap go — there is no
  absolute scale to police. Selection becomes "top-N by rank, filtered by
  match flags".
- JSON shape becomes trivial (a list of IDs in order) so the repair loop
  and most of `_parse_ranked_payload` shrink dramatically.
- `always_summarize_score` / `min_selection_score` become rank
  thresholds, or move to thresholds on the embedding similarity score.

**Cost / risk.** More LLM calls but each one is tiny. Calibration
problem disappears — this is what the literature has shown most
consistently. Requires more orchestration code than 3a.

### 3c. LLM-as-rubric-compiler

**Shape.** A single LLM call, made only when `preferences.md` changes
(or on a manual refresh), expands the priorities into a structured
rubric:
- weighted keywords / phrases per priority,
- a handful of "anchor sentences" per priority for embedding similarity,
- explicit dual-match logic.

Per-day ranking is then fully deterministic against this compiled rubric.

**How it fits in.** New artefact stored alongside `preferences.md` (e.g.
`preferences.rubric.json`), with a hash of the source preferences so it
auto-regenerates when the user edits priorities. `PaperRanker` is
replaced by a deterministic scorer that consumes the rubric.

**What gets removed.** The entire daily LLM ranking stack:
`_rank_candidates_batch`, batch logic, finalist re-rank, repair,
split-recovery, dual-match cap. Steady-state per-run LLM cost for
ranking → 0.

**Cost / risk.** Brittle to changes in priority phrasing tone; the
rubric quality depends on a single expansion call. Needs a sanity check
(e.g. "score last week's selections under the new rubric and warn on
disagreement"). Highest engineering cost, lowest steady-state cost.

### 3d. Cascade with a "boundary band"

**Shape.** Embedding score splits candidates into three bands:
- **clearly-in:** top X by similarity (auto-selected — full summary).
- **clearly-out:** bottom Y (auto-rejected; not even in weekly interest).
- **borderline:** the middle band. Only these go to the LLM.

The LLM then scores or ranks the borderline papers using the existing
rubric.

**How it fits in.** Maps onto the existing two-threshold architecture
almost 1-for-1: `always_summarize_score` becomes "above the cheap-score
upper band", `min_selection_score` becomes the lower band of the
borderline zone. Borderline → LLM for adjudication.

**What gets removed / simplified.**
- Number of papers sent to the LLM drops from N to the size of the
  borderline band (typically a handful).
- The full-pool listwise call disappears entirely; what remains is
  effectively the same as 3a but with explicit "skip the LLM for the easy
  ones" semantics.
- Batching / finalist re-rank become dead code.

**Cost / risk.** Mis-calibrating the band boundaries can either send too
much to the LLM (no saving) or auto-reject real matches. Backtesting
against historical runs needed to set the boundaries.

### 3e. Embed → cluster → rank within clusters

**Shape.** Embed all candidates, cluster by topic (HDBSCAN or k-means
over the embeddings). Rank within each cluster (with either the existing
listwise LLM call on smaller, comparable groups, or a comparator from
3b). Surface the top of each cluster.

**How it fits in.** Replaces the single global LLM call with several
smaller in-cluster ones. Naturally enforces topical diversity in the
selected set.

**What gets removed / simplified.**
- Position bias is reduced (within-cluster ordering is over comparable
  papers).
- The finalist re-rank is replaced by a cluster-aware merge step.

**Cost / risk.** More moving parts (clusterer, cluster-size heuristics)
for unclear benefit at the volumes this system actually sees (tens to
low-hundreds of candidates/day). Probably overkill — listed for
completeness.

---

## 4. Cross-cutting observations

- **The LLM's most defensible job here is producing rationales, not
  ranks.** Rationales surface in the daily/weekly notes and have
  user-facing value; ranks are a means to an end. Most of the hybrid
  options preserve the rationale step while replacing the scoring step.
- **Even better: the per-paper summariser already reads the full PDF.**
  If rationales are emitted there instead of at ranking time, the ranker
  doesn't need an LLM at all — it just needs to pick the right K papers.
- **`state/papers/` is an under-used asset.** Once any cheap score
  exists, the historical accept/skip record is ready-made supervision for
  threshold tuning and (eventually) a learned ranker (2c).
- **Anything we do should be evaluable against the existing
  `state/runs/*.json` history.** That's a built-in backtest harness; the
  user has weeks of "what got selected, what got summarised" already on
  disk.

---

## 5. Recommended order of operations

1. **3a (embedding cascade) behind a feature flag** — smallest blast
   radius, biggest immediate win on cost and reliability. Lets the rest
   of the pipeline stay exactly as it is while we measure.
2. **Backtest 3a against the last few weeks of `state/runs/*.json`** to
   choose K and the rescue threshold, and to confirm parity with the
   current ranker on selected papers.
3. **3b (comparator) for the top-K step** once 3a is bedded in — this is
   what finally lets `_apply_dual_match_cap` and the finalist re-rank go
   away entirely.
4. **3c (rubric compiler)** is the long-term endgame if steady-state
   cost ever becomes a concern, but is the riskiest single change and
   should not come first.
