# Ranking Implementation Notes

Date: 2026-03-22

## What Changed

- `src/re_ass/ranking.py` now uses explicit stages:
  - direct small-pool passthrough for `<= 50` candidates
  - lexical retrieval with anchor-token requirements
  - alias-aware semantic retrieval
  - reciprocal-rank fusion
  - local feature reranking
  - final Claude selection over a bounded final pool
- The final selector can now return fewer than `max_papers` when the Claude score is below the configured threshold.
- Run summaries now persist retrieval-pool diagnostics, final-pool diagnostics, final selection rationales, and whether the direct passthrough path was used.

## Operational Defaults

- `preferences.md` now searches:
  - `astro-ph.CO`
  - `astro-ph.GA`
  - `astro-ph.HE`
- `re_ass.toml` now defaults to:
  - `retrieval_pool_size = 96`
  - `final_pool_size = 24`
  - `min_selection_score = 80`
  - `passthrough_candidate_count = 50`

## Small Daily Pulls

When the fetched candidate set is `<= 50`, the ranker skips retrieval truncation and sends the full metadata set to Claude for the final choice. This keeps daily runs from losing quality to an unnecessary pre-filter.

## Testing

The ranking tests now cover:

- generic lexical false positives
- alias-based hybrid recall
- rerank reordering
- final-selection confidence gating
- small-pool passthrough
