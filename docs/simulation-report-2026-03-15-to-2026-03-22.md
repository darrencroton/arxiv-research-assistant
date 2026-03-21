# Simulation Report: 2026-03-15 to 2026-03-22

## Scope

- Simulated daily runs for `2026-03-15` through `2026-03-21`
- Ran one additional rollover day on `2026-03-22` to verify weekly archival
- Used `astro-ph.CO` with these priorities:
  1. Little red dots
  2. black holes and AGN
  3. semi-analytic galaxy formation models
- Enabled Claude CLI in the simulation config under `archive/simulation-2026-03-15-to-2026-03-21/re_ass.simulation.toml`

## Output Location

- Simulation vault: `archive/simulation-2026-03-15-to-2026-03-21/vault`
- Timing summary: `archive/simulation-2026-03-15-to-2026-03-21/simulation-summary.json`

## What Worked

- Historical backfill now respects the requested run date instead of always using "now".
- The weekly note was populated correctly across Sunday-to-Saturday.
- Sunday rollover created `Weekly_Archive/2026-03-22-arxiv.md` once the following Sunday was simulated.
- Daily notes, weekly synthesis, and daily weekly-addition entries were all written successfully.
- Claude generated good micro-summaries and good weekly synthesis text.

## What Failed

- The `/summarise-paper` step did not generate any actual paper notes through Claude.
- All 18 paper notes in the simulation vault were fallback notes generated locally by `re-ass`.
- A direct probe of the exact prompt returned:
  `I need permission to fetch web content. Could you approve the WebFetch tool call, or alternatively grant access to external URLs so I can retrieve the paper?`

## Timing

Per-day durations from `simulation-summary.json`:

- 2026-03-15: 104.24s
- 2026-03-16: 79.15s
- 2026-03-17: 107.89s
- 2026-03-18: 123.81s
- 2026-03-19: 129.91s
- 2026-03-20: 168.75s
- 2026-03-21: 108.44s

Observations:

- The mean runtime for the 7 simulated days was about 117s/day.
- This is far below the expected `4-8 minutes per paper`, which confirms the paper-note skill did not run to completion.
- The dominant time in the current simulation was repeated arXiv fetching plus short Claude calls for summaries/synthesis.

## Result Stats

- Daily notes created: 7
- Unique paper notes created: 18
- Weekly entries in the archived week: 21
- Duplicate weekly entries: 3 repeated papers
- Duplicated papers:
  - `Halo assembly bias in the early Universe: a clustering probe of the origin of the Little Red Dots`
  - `Field-Level Inference from Galaxies: BAO Reconstruction`
  - `Novel insights on the Coma Cluster kinematics with DESI. I. Linking mass profile, orbital anisotropy and galaxy populations`

## Bottlenecks

1. Claude skill permission gate
   The main blocker is external to the Python code: Claude's `/summarise-paper` flow needs WebFetch permission and exits without writing the requested note file.

2. Silent fallback masked the real problem
   Before this simulation, the app quietly fell back when no note file appeared. This is now improved with explicit logging of the Claude response.

3. Repeated arXiv full-feed fetches
   Simulating a week required re-fetching and re-ranking the arXiv feed for each day. This is acceptable now, but it is the main runtime cost on the Python side.

4. Duplicate paper carry-forward from the widened lookback
   The 7-day fallback lookback makes weekend or low-volume runs useful, but it also re-surfaces already-seen papers when no fresh matches exist.

5. Sequential paper processing
   Once the real summariser works, three papers per day at `4-8 minutes each` will turn this into a long-running job. The current sequential design will then become the primary runtime bottleneck.

## Areas For Improvement

1. Solve the Claude permission issue
   The next real blocker is not ranking or vault-writing anymore; it is getting `/summarise-paper` to run with web access in the non-interactive CLI environment.

2. Add duplicate suppression
   This is now clearly needed for widened lookback windows and weekend runs.

3. Cache arXiv query results during simulations/backfills
   A simulation runner should fetch once, then reuse local results across dates where possible.

4. Consider bounded concurrency for paper processing
   When the real summariser is working, processing 2-3 papers in parallel may be worth it if Claude and system resources can handle it.

5. Improve note filename sanitization
   Titles with LaTeX/math content currently produce awkward filenames, even though the links still work.

## Preference / Product Questions To Discuss

1. Weekend behavior
   When there are no genuinely new matching papers, should the assistant:
   - repeat the best recent papers,
   - write "no new matching papers today",
   - or skip the daily update entirely?

2. Duplicate policy
   If a paper already appeared earlier in the week, should it be:
   - omitted entirely,
   - linked once with a note like "still relevant",
   - or allowed to recur when it remains top-ranked?

3. Weekly synthesis scope
   Should the synthesis summarize every paper that appeared during the week, or only unique papers?

4. Daily note verbosity
   The current micro-summaries are useful but fairly dense. Do you want them tighter for the Daily note?

5. Paper note naming
   Should note filenames stay title-based, or move to a safer format such as `{arxiv_id} - {title}`?
