# re-ass A/B assessment: Minimax local vs Copilot Sonnet

Assessment date: 2026-05-16  
Window assessed: arXiv announcement dates 2026-05-11 to 2026-05-14, mapping to daily notes 2026-05-12 to 2026-05-15  
Benchmark config: `user_preferences/settings.toml`, Copilot `claude-sonnet-4.6`, CLI, unbatched ranking, `always_summarize_score = 85`  
Local config: `user_preferences/settings-local.toml`, OpenAI-compatible `minimax/minimax-m2.7-q8`, API at `127.0.0.1:8080`, ranking batch size 30, `always_summarize_score = 90`  
Primary evidence: `docs/ab-test-local-2026-05-11_to_2026-05-14.md`, selected daily/weekly notes, and selected paper summaries.

## Headline

Minimax is a credible local replacement for the core "what should I read this morning?" workflow, but it is not yet an indistinguishable production replacement for the full weekly triage system. Its generated summaries are usually scientifically specific and useful, and the local run had perfect candidate parity and clean reliability. The main gap is selection discipline: it agrees with Sonnet on the high-quality candidate pool more often than it agrees on the exact selected set, but it missed several strong second summaries and over-promoted two AGN papers on 2026-05-11. I score Minimax at **7.6/10 relative production quality** against a **good-enough threshold of 8.0/10**. Above 8.0, the remaining differences would be within normal model variance and would not matter much; this run is close, but the missed secondary selections still matter for a scientist relying on the weekly note.

This is not a "Sonnet is right by default" conclusion. Sonnet also has variance and minor defects: it exceeded the weekly synthesis word band by one word, some older summaries show glossary-count artefacts, and it sometimes summarizes more than is strictly necessary. The judgement here is based on the paper choices and summary content, not model reputation or word count.

## Caveats

- Candidate-set parity was perfect on all four assessed dates: Jaccard = 1.00 every day. The two systems were reading the same pool, so selection comparisons are meaningful.
- The current weekly notes are not perfectly symmetric: the benchmark weekly note also contains a Monday 2026-05-11 daily addition from a prior announcement day, while the local weekly note starts at Tuesday 2026-05-12. Weekly synthesis quality is still assessable, but it is not a clean same-input synthesis comparison.
- Glossary length differences were not penalised because glossary generation was recently changed to cap at 12 entries.
- Minimax's higher `always_summarize_score = 90` was treated as an intentional calibration change, not a flaw. I only penalised the observed outcomes when they affected quality.

## Relative Score

| Measure | Score | Interpretation |
|---|---:|---|
| Minimax relative production quality | 7.6/10 | Close to usable as the default, but not comfortably over the line. |
| Good-enough threshold | 8.0/10 | At or above this, differences are small enough relative to model variance that the privacy/cost benefit should dominate. |
| Sonnet absolute quality this week | 8.3/10 | Strong selection coverage and synthesis, with minor structural and verbosity defects. |
| Minimax absolute quality this week | 7.6/10 | Strong summaries and reliability, weaker recall of strong second papers. |

Practical reading: Minimax is good enough for local preview, sparring, and probably day-to-day top-paper discovery. I would not yet switch production weekly summaries over without one more round focused on secondary-selection recall and minor citation/title hygiene.

## Per-day Findings

### 2026-05-11 announcement, Tuesday note

Both models identified Lu et al. on COLIBRE UV luminosity functions as a strong fit. Minimax instead made Merida et al. on the BH* model the top paper and also selected Markowitz et al. on a changing-look Seyfert. These are not bad papers: Merida is a strong LRD/high-z AGN match, and Markowitz is relevant to AGN physics. The issue is threshold discipline. Minimax selected three papers above 90 on this day, while Sonnet selected one. Markowitz looks over-promoted relative to the stated preferences because it is more BLR/accretion-state physics than galaxy-evolution/AGN-demographics work. Minimax's Lu summary was good and specific, with the correct 1 to 2.5 mag UVLF deficit and the top-heavy IMF point. Sonnet's Lu summary was a little sharper on observational-bias limitations, but the local summary was citation-grade.

Verdict: Minimax's top choice was defensible, but it spent summary budget too freely on a less central AGN paper.

### 2026-05-12 announcement, Wednesday note

Both models selected Varnava et al. on W2246-0526, and that is a strong AGN/high-z selection. Sonnet also selected Lin et al. on DESI/EAGLE satellite metallicity enhancement. Minimax ranked Lin in the top five but did not summarize it. That is a meaningful miss: Lin directly hits galaxy environments, DESI survey benchmarking, EAGLE hydrodynamical simulation, and environmental chemical evolution. Minimax's Varnava summary was high quality, with correct polar-dust evidence, AGN luminosity, black-hole mass, SFR, and model caveats. It did have a malformed citation marker in the Method section (`[[^10]`), which is a minor template-integrity defect.

Verdict: Minimax produced a useful top summary, but missed a second paper that was strongly on-priority.

### 2026-05-13 announcement, Thursday note

The top two papers were the same pool in different order: Sonnet chose Matteri et al. first and Huang et al. second; Minimax chose Huang first and Matteri second but only summarized Huang. Both are excellent fits. Huang is squarely in galaxy-halo connection and clustering-method priorities, and Minimax's summary captures the core point: Poisson errors understate correlation-function uncertainties by about a factor of three and halo-mass uncertainties by about 1.5 to 3. Matteri, however, was also highly relevant: it uses clustering to discriminate explanations for the JWST high-z galaxy abundance problem. Missing that summary weakens the weekly synthesis and the "of interest" follow-through. Minimax's Huang summary had useful specifics, but also a few small fidelity issues, including a likely typo in the tag `#HBTHERONS` and a questionable glossary description of FLAMINGO-10k as a "suite of 10,000 ... snapshots".

Verdict: Minimax's top pick was fully defensible, but thresholding suppressed a second strong paper that should probably have been summarized.

### 2026-05-14 announcement, Friday note

Both models agreed on Leonova et al. on Horizon-AGN galaxy pairs and black-hole mergers as the top paper. The shared summaries were both useful. Sonnet's version gave more detail on the delayed-merger/numerical-merger distinction and rare high-mass limitations; Minimax's version was more compact but still captured the MCC optimisation, mass/redshift trends, cosmic-noon BH-merger peak, and LISA/PTA relevance. Sonnet also summarized Hafezianzadeh et al. on ASTRID LSST luminosity functions. Minimax ranked it second but did not summarize it. This is another nontrivial recall miss: ASTRID plus LSST mock catalogues directly matches simulation and survey-benchmark priorities, though it is less urgent than the top Leonova paper.

Verdict: Minimax matched the top decision well, but again under-summarized the strong second paper.

## Rubric Scoreboard

Scores use the 1-5 rubric from `docs/ab-test.md`. R5, R6, and R7 are gates.

| Rubric | Sonnet | Minimax | Evidence |
|---|---:|---:|---|
| R1 Selection on-priority and defensible | 4.2 | 3.7 | Minimax's selected papers were mostly defensible, but it over-selected on 2026-05-11 and missed strong second summaries on 2026-05-12, 2026-05-13, and 2026-05-14. |
| R2 Top-N ranking agreement | 3.6 | 3.6 | Pair metric: top-5 Jaccard was 0.67, 1.00, 1.00, 0.67; top-10 Jaccard was 0.54, 0.43, 0.82, 0.67; Kendall tau was +0.52, +0.33, +0.67, +0.57. Good pool agreement, imperfect ordering. |
| R3 Threshold discipline | 4.0 | 3.3 | Sonnet selected 1, 2, 2, 2 papers. Minimax selected 3, 1, 1, 1. The day-one inflation and later conservative recall both affected product value. |
| R4 Summary depth and accuracy | 4.4 | 4.0 | Minimax summaries included headline numbers, methods, results, and weaknesses. Sonnet was slightly stronger on nuance and paper-specific caveats. Minimax had a few small content/fidelity slips. |
| R5 Template integrity | 4.2 | 4.0 | Both had clean required sections and no fatal structural issues. Minimax had one malformed citation marker in Varnava. Glossary-count differences ignored. |
| R6 Weekly synthesis quality | 4.1 | 4.0 | Both syntheses were coherent and useful. Sonnet's was sharper but one word over the configured band and partly based on a broader weekly note. Minimax's was within band and clear, but weakened by missing secondary papers. |
| R7 Operational reliability | 5.0 | 5.0 | Both had fatal/warnings/errors = 0/0/0 on all assessed days. |
| R8 Author/title fidelity | 4.0 | 3.6 | Minimax preserved most titles, but file/link text for the BH* paper became `BH$^ $`, and some tags/terms were rough. Sonnet also had minor author-name inconsistencies in daily notes. |
| R9 Cost/privacy posture | 2.0 | 5.0 | Minimax runs locally through the configured OpenAI-compatible endpoint; Sonnet uses Copilot CLI. |

## Outcome by User Story

| User story | Outcome |
|---|---|
| U1 Morning triage | Minimax is good enough for top-paper discovery. The top paper differed on 3 of 4 days, but the alternatives were generally credible and on-priority. |
| U2 Defensible selection | Minimax is workable but not fully mature. Rationales usually named the right science priority, but selection thresholds caused missed high-quality secondary papers. |
| U3 Citation-grade summary | Minimax is close. The summaries are specific, numeric, footnoted, and contain concrete weaknesses. The occasional citation/term/title blemish needs cleanup. |
| U4 Weekly perspective | Minimax is usable but somewhat less complete because it had fewer summarized ingredients. The prose itself is fine. |
| U5 Set-and-forget reliability | Both passed cleanly. |
| U6 Privacy/cost control | Minimax wins decisively if quality is accepted. |

## Recommendation

Use **Sonnet for production today** if the weekly note is meant to be the authoritative record, because its selection recall was better this week. It captured the strong secondary papers that Minimax left in the tail: Lin et al. on DESI/EAGLE satellite metallicity, Matteri et al. on high-z clustering discriminants, and Hafezianzadeh et al. on ASTRID/LSST luminosity functions.

Use **Minimax as the local partner now**, and consider it a near-production candidate. Its summaries are already scientifically useful, and the local/privacy benefit is substantial. The gap is not summary prose length or model prestige; it is the reliability of selecting all genuinely strong papers without either day-one inflation or later under-selection.

## Targeted Follow-ups

1. Tune local selection recall around the `85-90` band rather than only raising `always_summarize_score`. The failure mode was not mostly "bad papers selected"; it was "strong second papers ranked high but not summarized".
2. Add a selection rule or prompt reminder for dual science+method matches: if a paper is top-3 and clearly hits both a science priority and a method priority, it should usually survive unless the rationale explicitly says why not.
3. Add a lightweight output QA check for malformed footnote markers and title/math degradation in generated note links, especially patterns like `[[^10]` and `BH$^ $`.

## Bottom Line

Minimax is **almost** good enough. I would call it production-ready when it can repeat this level of summary quality while recovering the strong second papers on at least 3 of 4 days. On this run, the local model is strong enough to trust for daily discovery, but still just below the "variance does not matter" threshold for fully replacing Sonnet in the weekly record.
