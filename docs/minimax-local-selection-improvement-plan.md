# Minimax local selection improvement plan

Date written: 2026-05-16

This note documents the changes made after the first local Minimax A/B test so a future analysis can compare the next `settings-local.toml` run against both:

1. the Sonnet benchmark output, and
2. the previous Minimax result now archived under `~/Documents/AI Tools/private/re-ass-tests/archive/minmax-4`.

The goal is not to overfit one week of scores. The goal is to make the local model's ranking task easier, preserve a stable top threshold, and add one bounded near-threshold rescue for high-quality papers that land just below the top band because of ordinary model variance.

## Baseline evidence

The prior assessment is copied into:

- `~/Documents/AI Tools/private/re-ass-tests/archive/minmax-4/ab-test-minimax-vs-sonnet-baseline-2026-05-11_to_2026-05-14.md`

The generated compare report from that run is:

- `~/Documents/AI Tools/private/re-ass-tests/archive/minmax-4/ab-test-local-2026-05-11_to_2026-05-14.md`

Baseline summary:

- Candidate parity was perfect: both providers saw identical candidate pools on all four assessed days.
- Reliability was clean: both providers had fatal/warnings/errors = `0/0/0`.
- Minimax summary quality was generally useful, specific, footnoted, and numerically detailed.
- The main weakness was selection recall and threshold behaviour, not prose length.
- Minimax at `always_summarize_score = 90` selected counts of `3, 1, 1, 1` across the week, compared with Sonnet's `1, 2, 2, 2`.
- Important secondary papers that Minimax ranked highly but did not summarize included Matteri et al. on high-z clustering constraints and Hafezianzadeh et al. on ASTRID/LSST luminosity functions.
- Lin et al. on DESI/EAGLE satellite metallicity was also a strong benchmark selection, but Minimax scored it `82`, below the near-top band.

The prior judgement was that Minimax was near production quality, roughly `7.6/10`, but just below the "variance does not matter" threshold because it missed several strong secondary papers.

## What changed

### Preferences

Both the active local preferences file and the tracked defaults were rewritten to make the priorities easier for weaker/local models to apply consistently.

Files:

- `user_preferences/preferences.md` locally, ignored by git
- `user_preferences/defaults/preferences.md` tracked

The previous preferences were scientifically correct but dense. The new wording keeps the same broad intent while making each priority more operational:

- It names what a strong fit looks like.
- It says what should not be strongly promoted.
- It distinguishes galaxy-evolution science from adjacent topics such as generic Milky Way structure, isolated ISM chemistry, generic dust, AGN accretion/BLR physics, general cosmology, and method-only survey/catalogue papers.
- It emphasizes that method matches should support the listed science priorities, not merely be simulations, surveys, statistics, or machine learning in isolation.

Expected effect:

- Fewer adjacent-but-not-central papers should receive `85+`.
- Environment/survey/simulation papers that directly constrain galaxy evolution should be easier to score correctly.
- Papers like Lin et al. should be less likely to sit below the near-top rescue band if their central result is strongly on-priority.

### Ranking prompt calibration

The ranking prompt now explicitly says:

- score `85+` only when the central result directly advances a listed science priority and the method is relevant to that same science question;
- reserve `90+` for papers that clearly deserve one of the day's top `1-3` summary slots.

This is intentionally light-touch. It should reduce score inflation without asking the model to follow a complex decision tree.

### Near-top rescue selection

The top threshold remains configured by `always_summarize_score`; for the local run this remains `90`.

The selector now adds one deterministic rescue path:

- If at least one top-band paper exists, keep all papers scoring `>= always_summarize_score` as before.
- Also rescue at most one additional paper from the band `[always_summarize_score - 5, always_summarize_score)`.
- The rescue paper must be in the top three ranked candidates.
- The rescue paper must be a dual science and method match.
- If no paper clears the top band, the existing quiet-day behaviour remains unchanged: select the best eligible paper above `min_selection_score` so the daily note still has something useful.

This means the default local behaviour with threshold `90` is:

- `>=90`: summarize
- `85-89`: summarize at most one, only if top-three and dual-match
- `70-84`: weekly interest unless no top-band paper exists, in which case the best eligible paper is used as the daily fill

The intended steady state is usually `1-3` summarized papers per day. A uniquely strong day can still produce `4` if several papers genuinely clear the top band plus one near-top rescue, but that should be uncommon.

## Counterfactual on the previous Minimax scores

Applying the new rescue rule to the archived Minimax scores, without changing the model output, would have selected:

| Announcement date | Counterfactual selected papers |
|---|---|
| 2026-05-11 | Merida `95`, Lu `94`, Markowitz `92` |
| 2026-05-12 | Varnava `92`, Gaia quasar-pair photometric-redshift paper `88` |
| 2026-05-13 | Huang `95`, Matteri `88` |
| 2026-05-14 | Leonova `92`, Hafezianzadeh/ASTRID `88` |

This would have recovered Matteri and ASTRID without lowering the global threshold to `85`. It would not have recovered Lin, because that requires the model to score the paper more appropriately under the rewritten preferences.

That distinction is important for the next analysis:

- If the new run recovers Matteri/ASTRID-like second papers through the rescue path, the selector change is working.
- If the new preferences lift Lin-like environment/survey/simulation papers into the `85-89` or `90+` band while suppressing adjacent noise, the preference rewrite is working.
- If the model simply promotes more marginal papers into `85+`, then the prompt/preference changes increased score inflation rather than improving relevance.

## How to assess the next run

When the new `settings-local.toml` run is complete, run the normal comparison:

```bash
uv run python scripts/ab-test.py compare --week --markdown
uv run python scripts/ab-test.py compare --week --json
```

Use `docs/ab-test.md` as the scoring rubric, then compare three things:

1. New Minimax vs Sonnet benchmark for the new week.
2. New Minimax vs previous Minimax failure modes from the baseline report.
3. New Minimax selected set vs its own ranked list to see whether the near-top rescue selected the right paper or promoted noise.

Specific questions to answer:

- Did candidate parity remain high? If not, selection comparison is less meaningful.
- Did reliability remain clean?
- Did the selected-paper count usually stay around `1-3`?
- Did the rescue slot fire only on genuinely strong dual-match papers?
- Did fewer adjacent-but-not-central papers receive `85+`?
- Did environment/survey/simulation papers with direct galaxy-evolution content score more appropriately?
- Did the daily top paper remain defensible even when it differed from Sonnet?
- Did the weekly synthesis improve because it had better secondary ingredients?

Do not judge improvement by word count. Judge whether the summaries and synthesis contain more scientifically useful content for a working astronomer.

## Success criteria

Treat the change as a genuine improvement if most of the following are true:

- Minimax still has clean reliability and candidate parity.
- Aggregate selected-paper overlap with Sonnet improves without a large increase in total selected papers.
- The local selected count is closer to Sonnet's count, but not by summarizing many marginal papers.
- The local model recovers strong secondary papers that it previously left only in weekly interest.
- The rewritten preferences reduce obvious over-promotion of adjacent AGN, ML, survey, or generic simulation papers.
- The weekly synthesis becomes more complete because the summarized set better represents the week.

Treat the change as inconclusive if the new week is scientifically very different, candidate pools diverge, or Sonnet itself shows high variance.

Treat the change as a regression if the rescue slot commonly selects weak papers, if `85-89` becomes noisy, or if daily summary volume becomes overwhelming.

## Follow-up after archived `minmax-5`

The next completed local run was archived under:

- `~/Documents/AI Tools/private/re-ass-tests/archive/minmax-5`

Post-change report written outside the repo:

- `~/Documents/AI Tools/private/re-ass-tests/archive/minmax-5/re-ass-minimax-local-selection-postchange-2026-05-11_to_2026-05-14.md`
- `~/Documents/AI Tools/private/re-ass-tests/archive/minmax-5/re-ass-minimax-scoring-cause-review-2026-05-16.md`

The `minmax-5` run improved the visible top-paper match but did not solve
selection recall:

- Candidate parity remained perfect and reliability stayed clean.
- Top-paper agreement with Sonnet was `4/4`.
- Local selected counts were `1, 1, 1, 1`, compared with Sonnet's `1, 2, 2, 2`.
- The rescue slot did not meaningfully fire because most missed secondary papers
  scored below `85`.
- Several obvious dual matches were marked as dual matches but scored in the
  prompt's `70-84` band, for example Lu `83`, Lin `79`, Huang `78`, and FLARES
  `82`.
- Some misses were boolean-gate failures rather than pure scoring failures:
  Varnava ended `science_match=true, method_match=false`, and ASTRID/LSST ended
  `science_match=false, method_match=true`.

Interpretation:

- The main issue was not summary quality and probably not inability to compare
  the top candidates.
- The biggest influence was prompt/selector mismatch: the prompt described
  `70-84` as a valid dual-fit band, while the selector treats `70-84` as weekly
  interest except for one quiet-day fallback.
- The hard one-sided score cap to `69` made boolean mistakes look like low model
  confidence and made near-miss diagnostics harder.
- The preference rewrite reduced noisy over-selection, but likely over-suppressed
  some simulation/survey products, especially ASTRID/LSST-style mock-catalogue or
  luminosity-function papers.

## Second calibration update

The next iteration keeps the approach simple and avoids adding a large decision
tree.

### Prompt scoring bands

The ranking prompt now aligns score bands with selection behavior:

- `90-100`: strongest dual fits and obvious daily-summary candidates.
- `85-89`: clear dual fits that are plausible daily-summary candidates.
- `70-84`: relevant but not daily-summary fits, including lower-confidence dual
  matches or important one-sided matches for weekly interest.
- `40-69`: partial, adjacent, or weakly connected fits.
- `0-39`: weak fits.

The prompt no longer says to "prefer conservative scoring". It instead says to
use the full score range and not push clear daily-summary candidates below `85`
just because they are not the single best paper.

### Score preservation for one-sided papers

The selector still requires dual science and method matches for daily selection
when both priority sections are present.

However, it no longer mutates one-sided high scores down to `69`. Raw scores are
preserved for ranking diagnostics and weekly-interest visibility. This keeps the
selection guard while making false boolean decisions easier to identify.

### Simulation and survey preference clarification

The active preferences and tracked defaults now clarify that simulation or
survey products are strong method matches when they deliver calibrated galaxy
luminosity functions, mock catalogues, clustering measurements, or model
benchmarks used to test galaxy evolution.

Expected effect:

- Strong dual-match papers should move from `70-84` into `85+` more often.
- False one-sided classifications should remain visible as high-scoring
  diagnostic misses instead of being hidden at `69`.
- ASTRID/LSST-like papers should be less likely to be treated as method-only
  infrastructure.

Watch-outs for the next run:

- If `85+` becomes noisy again, the prompt calibration went too far.
- If selected counts remain `1, 1, 1, 1`, the selector may need one narrow
  top-two dual-match fallback.
- If selected counts jump to many papers per day, keep the prompt but tighten the
  selector rather than adding more preference text.
