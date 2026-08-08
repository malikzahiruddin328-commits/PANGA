# Score-first resume flow — spec

Status: approved for build (points 1-5). Point 6 (what "Generate" does once a
resume already exists) is deliberately excluded from this build — still under
discussion with Zahir, do not build ahead of it.

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

## Explicitly out of scope for this build

**Point 6 - what happens when "Generate" is clicked again after a resume
already exists for this job.** Still being discussed with Zahir. Current
leaning (not yet approved): the Step 1 panel doesn't disappear after first
generation, it stays as "things that could still raise this further," and
clicking Generate at any point (first time or later) just means "redraft
using everything confirmed since the last draft" - but this is NOT approved
yet, do not build against this assumption. Whoever picks this build up:
ping the hub before touching what happens post-first-generation.

## Ownership

Spans both ats_score.py/drafting.py/job_store.py (baseline selection,
either/or groups, question-value computation) and app.py (the new Step 1/
Step 2 screen). Coordinate between ATS Engine (backend) and UI refinement
(frontend) - backend pieces (1, 2, 4) are a prerequisite for the frontend
screen (3, 5 are partly UI, partly backend-question-generation).
