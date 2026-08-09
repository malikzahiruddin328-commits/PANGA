# Score-first resume flow — spec

Status: approved for build (points 1-7). Layout: **Option B** (split
summary + list - score/baseline/plateau explanation in a sticky left rail,
questions as their own cards on the right) is the confirmed choice out of
the 3 mockup options - build against Option B, not A or C.

## Why

Live-tested 2026-08-08: the current flow drafts a full resume on every single
clarifying-question answer, "to see if it helps." This is expensive (a full
resume generation per answer) and, worse, a real regression risk — since each
regenerate rewrites the resume from scratch, a keyword that matched in one
draft has no guarantee of surviving the next rewrite, even when the new
content is objectively better (confirmed live: "clinical development" was
present, then vanished after a regenerate that reworded it to "clinical-stage"
- see the ATS Engine investigation this same day for the reproduction).

Zahir's proposed redesign, refined through discussion: separate "figure out
what would help" (cheap, iterative, no document written) from "write the
actual resume" (expensive, happens once, at the end). This spec is that
redesign.

## The two-step flow

**Step 1 — Analyze fit (no document created).** Score the posting's
required/preferred keywords against a baseline text (see Baseline selection
below) plus whatever's already confirmed in the user's profile
(`gap_interview_answers`). Surface open questions, each labeled with the
real point value answering it would add (computed directly from the
deterministic scorer's own keyword-count math — same arithmetic
`ats_score.py` already does, just run before drafting instead of after).
Every answer saves immediately into the profile (`save_gap_answers()`,
already exists) regardless of whether the user ever proceeds to Step 2 — an
abandoned session still keeps the real facts learned.

**Step 2 — Generate, once satisfied.** One real drafting call, using every
confirmed answer at once. This is the only point a resume document actually
gets written.

## Build items (1-5, approved)

### 1. Baseline selection for the "projected" score

Before any resume exists for a job, Step 1 needs something to score against.
Don't default straight to the generic base resume (`data/profile/raw`) - first
look for the most similar *previously drafted* resume among the user's other
applications, using keyword-set overlap (Jaccard similarity or equivalent)
between this job's `ats_required_keywords`/`ats_preferred_keywords` and each
past job's cached keyword lists - no new AI call needed, this is arithmetic
over data already cached per job. Use that past resume's text as the baseline
if a good match exists (define "good match" - e.g. a minimum overlap
threshold - during implementation, live-verify the threshold isn't so loose
it picks an unrelated resume). Fall back to the generic base resume only when
nothing sufficiently similar exists yet.

### 2. Either/or qualification requirements, and honest score-plateau explanations

Real gap in the current keyword extractor: JDs routinely phrase a requirement
as a substitutable *either/or* - e.g. "Master's degree, OR Bachelor's degree
plus 8+ years of experience." Today's extractor (`ATS_KEYWORDS_SYSTEM_PROMPT`
in `drafting.py`) has no concept of this - it can extract "Master's degree"
as its own flat required keyword, and a candidate who satisfies the
Bachelor's+experience side gets dinged for a "gap" that isn't real.

Fix: extend extraction to recognize either/or qualification patterns and
represent them as a single satisfiable group (schema change needed -
`required_keywords` currently a flat list of strings, needs to support an
alternative-group shape) rather than independent flat keywords. The
deterministic scorer (`score_resume_against_keywords`) needs matching logic
that treats a group as satisfied if *any* member matches, not all.

Second part of this item: **the system, not the user, should notice when the
score has genuinely plateaued** despite honest iterative answering, and
explain why in plain terms rather than leaving Zahir to wonder ("why did
this go up/down/nowhere"). Live-verify against a real either/or case (a real
JD phrasing this pattern) that the explanation correctly identifies the
Master's-style "gap" as not real, citing the actual satisfying alternative.

### 3. Standing-preference questions get no point value, and look different

Some questions aren't about this job's score at all - they're standing
preferences that apply to every future match (the existing
`disqualifier_check` question type already covers this, e.g. "should VP/CIO
roles below a certain level be excluded going forward," or "have you ever
architected a pure Salesforce/SFDC implementation, not just Veeva CRM for
pharma"). These should never get a "+N pts" badge (there is no per-job point
value - it's a real yes/no fact, not a probe with a suggested answer), and
should render visually distinct from the point-scored skill_gap questions so
it's clear at a glance they're a different kind of thing. This mechanism
already exists in code (`is_disqualifier` handling in
`render_gap_questions_section`) - this item is about carrying that same
distinction into the new Step 1 screen's question list, not building it from
scratch.

### 4. Answers persist even if the user never reaches Step 2

Already true today via `save_gap_answers()` - explicitly confirm this
behavior is preserved (not accidentally gated behind clicking "Generate") in
the new flow, since it's easy to accidentally couple "answer saved" to
"document generated" when restructuring this.

### 5. "Answer more" needs a real stopping point

Clicking "Answer more" triggers a new round of question generation given
what's already confirmed. Cap it sensibly (same 3-10 range logic
`clarifying_questions` already uses) and, when genuinely nothing more
worth asking exists, say so explicitly ("no more real gaps found based on
your current profile") rather than either erroring or inventing a
low-value question to fill space.

### 6. Regenerating a job that already has a resume - conditional confirmation

The Step 1 panel doesn't disappear after first generation - it stays
permanently as "things that could still raise this further." Clicking
"Generate" at any point (first time or later) means "redraft using
everything confirmed since the last draft." What happens on a *repeat*
Generate click depends on whether there's genuinely new information:

- **New confirmed answers exist since the last draft** - no blocking gate,
  just an informational heads-up before proceeding: "N new confirmed facts
  aren't in your resume yet - regenerating should raise your score from X
  toward ~Y, estimated cost $Z. Go ahead?"
- **Nothing new since the last draft** - a real yes/no confirmation gate.
  Regenerating here is pure downside risk (every regenerate is a full
  rewrite - see the "Why" section above - so with nothing new to add,
  there's no expected upside, only the real chance of an accidental
  rewording dropping a previously-matched keyword, exactly what happened in
  the live case that motivated this whole redesign). Show the real cost of
  the last generation call and ask for explicit confirmation before
  proceeding.

### 7. Real per-call cost logging (prerequisite for item 6)

Item 6 needs a real dollar figure, not an estimate. `api_cost.py` already
has `estimate_response_cost()` - computes real cost from a response's
actual token usage, explicitly built (2026-07-31, Zahir's own request at the
time) to be reused everywhere, but today it's only wired into
`call_with_web_search()`, not into `call_structured()` - the function that
actually powers job scoring, resume/cover-letter drafting, and everything
else (the vast majority of real spend). Scope for this item:
1. Wire `estimate_response_cost()` into `call_structured()` too.
2. Persist each call's real cost to a simple running log (one row per call:
   timestamp, what it was for, model, tokens, $ cost) rather than computing
   and discarding it.
3. Update the existing `panga-cost-report` skill to read real logged
   numbers instead of estimating from guessed call frequency.
Scope explicitly does NOT include a new cost-dashboard UI screen - Zahir
wants that deferred ("for the other screens we can expose it later"). This
item only needs to produce a real number item 6's popup can read.

## Ownership

Spans ats_score.py/drafting.py/job_store.py/llm_client.py/api_cost.py
(baseline selection, either/or groups, question-value computation, cost
logging) and app.py (the new Step 1/Step 2 screen, Option B layout, the
regenerate-confirmation popup). Coordinate between ATS Engine (backend,
items 1/2/4/7) and UI refinement (frontend, items 3/5/6's popup) - backend
pieces are a prerequisite for the frontend screen.
