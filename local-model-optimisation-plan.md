# Local Model Optimisation Plan

## Motivation

The pipeline is moving from frontier models (e.g. Claude Sonnet) to capable local models
(e.g. Minmax M2.7 on Mac Studio M3 Ultra). These models produce summaries at roughly 85%
of Sonnet quality. The goal is to close that gap by reducing cognitive load on the model,
sharpening focus at each stage, and improving template adherence — without sacrificing the
depth, coherence, or anti-hallucination properties the current design depends on.

The changes fall into two categories:

- **Pipeline restructuring**: split one large, complex call into a focused main call plus
  cheap post-processing steps
- **Prompt improvements**: reduce noise, provide a worked example, filter inputs

---

## Agreed Changes

### 1. Filter keywords by paper category

**Current state**  
All ~440 science-area keywords are injected into the system prompt for every paper,
regardless of the paper's subject area.

**Change**  
Before building the system prompt, filter the keyword list using the paper's
`primary_category` and `categories` fields from `ArxivPaper`. Map arXiv category codes
to keyword categories and inject only the relevant subset (~40–80 keywords depending on
topic breadth).

**Implementation notes**  
- Add a mapping from arXiv category codes (e.g. `astro-ph.GA`, `astro-ph.CO`) to keyword
  category headings in `keywords.txt`
- Add a filter function that takes `ArxivPaper.categories` and returns the filtered keyword
  list
- Pass the filtered list into `create_system_prompt()` in place of the full list
- The mapping should be maintainable (a dict or small config file), not buried in logic

**Expected outcome**  
Shorter, more relevant system prompt. The model attends to a manageable keyword set,
improving both selection accuracy and overall prompt comprehension.

---

### 2. Add a worked example to the user prompt

**Current state**  
Template adherence is enforced via 12 explicit rules in the system prompt and a skeletal
template with placeholder text ("Blah blah blah[^1]"). Local models respond poorly to
long rule lists.

**Change**  
Replace or substantially trim the rule list with a single carefully crafted example section
in the user prompt. The example should demonstrate one complete section (e.g. *Results*)
showing: correct bullet structure, an exact quote in the correct footnote format with
section/page reference, bold on first technical mention, and UK English. Any rules that
the example makes self-evident should be removed.

**Implementation notes**  
- Write the example using a plausible but fictional astronomy paper (avoid any real paper
  that might be in the model's training data and cause confusion)
- Store it as a project knowledge file alongside the existing prompt templates
- Inject it into the user prompt template at an appropriate position (after the format
  requirements, before the paper input)
- Review the 12 rules and remove any that the example now covers implicitly; keep only
  rules that cover edge cases not shown in the example

**Expected outcome**  
Significantly improved template adherence, especially footnote formatting and bullet
structure. Shorter, cleaner system prompt as rules are retired.

---

### 3. Separate glossary generation into a post-processing step

**Current state**  
The glossary is generated as part of the main summary call. The model must simultaneously
write narrative sections, track technical terms, and format a two-column markdown table —
all in one pass.

**Change**  
Remove the glossary from the main summary call. After the main summary is complete and
validated, make a second focused call: pass the completed summary text and ask the model
to identify technical terms and produce the glossary table. Splice the result back into
the summary at the `## Glossary` location.

**Implementation notes**  
- Remove the `## Glossary` section from `paper-summary-template.md`
- Remove glossary instructions from `user-prompt.md` and `system-prompt.md`
- Add a `generate_glossary(summary_text, provider, config)` function in `service.py`
- Add a `insert_section(summary, section_markdown, after_heading)` utility for splicing
- Call glossary generation after `validate_summary()` in `summarise_source()`
- Glossary call does not need the paper content — the summary is sufficient input
- The glossary call needs its own validation (check that a table was returned)

**Expected outcome**  
Main call is simpler and shorter. Glossary quality improves because the model reviews the
completed summary rather than tracking terms mid-generation. The glossary correctly
reflects what actually appears in the summary rather than terms from the raw paper that
may not have made it through.

---

### 4. Separate tag generation into a post-processing step

**Current state**  
Tags are generated as part of the main summary call. The system prompt contains the full
keyword list (~440 entries) to support this, adding significant noise to every call.

**Change**  
Remove tag generation from the main summary call. After the main summary (and glossary)
are complete, make a third focused call: pass the completed summary text and ask the model
to generate the two-line tag block. Move the keyword list out of the main system prompt
and into this call only.

**Implementation notes**  
- Remove the `## Tags` section from `paper-summary-template.md`
- Remove the `<tags>` block from `user-prompt.md`
- Remove the `<knowledgeBase>` / keywords block from `system-prompt.md`
- Add a `generate_tags(summary_text, keywords, provider, config)` function in `service.py`
- The tag prompt should specify the two-line format explicitly (proper nouns line, then
  science-area keywords line) and pass the filtered keyword list (see Change 1)
- Splice the result back into the summary at the `## Tags` location using the same
  `insert_section()` utility introduced in Change 3
- The tag call needs its own validation (check two hashtag lines were returned)

**Expected outcome**  
Main system prompt is substantially shorter and cleaner (no keyword list). The model
focuses entirely on writing a coherent narrative summary. Tag quality is maintained or
improved because the model selects tags from the synthesised summary rather than the raw
paper, and attends to a filtered keyword list rather than all 440.

Note: Change 1 (keyword filtering) should be implemented before or alongside this change,
as the filtered list is what gets passed to the tag call.

---

## What is not changing

- **Footnotes remain in the main call.** The constraint of finding an exact supporting
  quote for every bullet is intentional: it forces the model to self-censor during
  generation and is the primary guard against hallucination. Separating footnotes into a
  post-processing step would lose this constraint at the moment it matters most and would
  risk the model retrofitting quotes to claims rather than genuinely verifying them.

- **Section-by-section generation is not being pursued.** The summary must tell the story
  of the paper as a coherent whole. Generating sections independently would break
  cross-section narrative coherence (e.g. Key Ideas anticipating Results, Discussion
  reflecting back on Method).

---

## Expected Outcomes

| Change | Primary benefit |
|---|---|
| Keyword filtering | Shorter, more relevant system prompt; better keyword selection |
| Worked example | Improved template adherence; trimmed rule list |
| Separate glossary | Simpler main call; glossary reflects actual summary content |
| Separate tags | Removes keyword list from main call; focused tag selection |

Collectively these changes reduce the cognitive load on the main summary call — the
hardest and most important step — while delegating well-defined, bounded tasks to
cheaper, focused follow-up calls. The additional calls are small (summary text in, one
section out) and add modest latency. The main call becomes shorter in both input and
required output, which is where local models gain the most.
