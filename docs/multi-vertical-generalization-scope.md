# Multi-Vertical Generalization — scope

Branch: `feature/multi-vertical-generalization`. Started 2026-07-31, split
out of the "General" brainstorming session into its own dedicated chat per
Zahir's usual one-session-per-purpose pattern. Full backlog context:
`docs/job-search-automation-prd.md` §13 — "Generalize Panga for
multi-vertical sale" row.

## Goal

Panga was built end-to-end for one user's own career: IT/CIO-track roles,
life-sciences/pharma industry focus, his own personal disqualifiers (e.g.
CISO-titled roles). Selling this to other users means a physician, a nurse
practitioner, a chemical engineer targeting nuclear plants, and a chemical
engineer targeting plastics/injection-molding plants all need the app to
work for *their* career — not Zahir's. This branch generalizes the parts of
Panga that currently assume "the user is Zahir."

**Why this matters before the other three branches finish:** packaging,
update-delivery, and licensing are all shipping infrastructure *around* the
product. If the product itself only really works for one person's career,
none of that infrastructure produces something sellable. Worth sequencing
this branch's core pieces early relative to the others, not as an
afterthought once they're done.

## Progress (updated 2026-08-01)

Design points 1, 2, 3, and 5 below are done — vertical/seniority intake,
resume-driven title-ladder + target-role generation, and user-editable
disqualifiers are all built, tested (70 existing tests pass, no
regressions), and manually verified in the browser including the
fresh-install/new-user path. Point 4 (Prospector) is intentionally still
untouched per its own "incremental, not speculative" design. What's left
on this branch: nothing identified yet beyond point 4's eventual
demand-driven builds, which aren't this branch's job to start early.

## What's currently hardcoded (the problem)

- **CISO/security-officer-title disqualification** — Zahir's personal
  "not qualified for this despite broader experience" rule, baked directly
  into the scoring system prompt logic.
- **Prospector's entire signal-sourcing stack** — `regulatory_filings.py`
  (openFDA), `clinical_trials.py` (ClinicalTrials.gov), `company_filters.py`
  (mega-pharma denylist, non-company/acquired-company keyword lists),
  `commercial_hiring.py` (pharma commercial-title keywords) — all
  life-sciences/pharma-specific, all built assuming that's the target
  industry.
- **`target_roles`/industry weights** — one shared `config/settings.yaml`,
  not per-user; assumes IT/CIO-track titles and weights.

## Design (converged 2026-07-31)

### 1. Vertical/industry intake, after resume + support-doc ingestion
**Status: done (2026-08-01).** New intake step: ask the user's desired
industries/verticals directly — **not** a hardcoded dropdown. Trades vary
too widely to enumerate up front, and even within one trade the target
companies differ hugely by sub-vertical (a chemical engineer targeting
nuclear plants needs different target-account signals than one targeting
plastics/injection-molding plants). Free-text or tag-style input, not a
fixed picklist.

Implementation: the Settings tab's previously-decorative "Industries" box
(free text, one per line) is now the real intake field, plus a new
self-reported seniority field alongside it. The whole "Target roles and
industries" section was reordered to sit directly after the Documents
section (before LinkedIn profile/connections, which aren't part of the
intake) so it actually reads as "the next step after resume ingestion"
rather than a settings box several unrelated sections down the page.
Saving a resume now nudges the user toward it via the save toast.

### 2. Title-ladder generation, resume-driven + live-reasoning
**Status: done (2026-08-01).** Job titles prefilled from the resume, then
cross-checked against a live Claude reasoning pass: "what's the standard
title ladder for this trade/vertical" — a physician's ladder looks nothing
like a nurse practitioner's, which looks nothing like a chemical
engineer's. This extends the **existing** `src/skills/` role/skill lookup
mechanism (build step 2 in the original PRD) to be vertical-aware, rather
than building a new system from scratch — that mechanism is already
live-reasoning-based, not a static table, which is the right foundation
for this.

Implementation: `tailoring.drafting.generate_target_roles()` (one reasoning
call) writes the generated ladder into `src/skills/role_skills.json` via a
new `skills.lookup.save_role_skills()`, giving that "grows over time" file
its first real caller beyond the original hand-built Lifesciences/Pharma
entry.

### 3. Per-user `target_roles`/weights, reasoning-generated
**Status: done (2026-08-01).** Generated via a reasoning pass over: resume
content + self-reported seniority + years of real-world experience + the
chosen verticals from step 1 — proposing adjacent/equivalent roles the
user might not have listed themselves (the same spirit as Zahir's own
`config/settings.yaml` starter weights, but generated per-user instead of
hand-typed). Loads into the **existing** Settings `st.data_editor`
target-roles table so the user reviews/edits a proposed starter set rather
than typing weights in blind.

Implementation: same `generate_target_roles()` call as point 2 returns both
the ladder and the proposed `target_roles`/weights list; the Settings
tab's "Generate my target roles from my resume" button prefills the
existing editor (folding a generation counter into the widget key so a
fresh generation actually replaces stale values, not Streamlit's usual
key-persistence trap).

### 4. Prospector stays life-sciences-specific for now — build incrementally
**Do not** try to pre-build signal sources for every trade speculatively.
Life-sciences/pharma (the existing, proven implementation) remains the
reference; new verticals' signal sources get built only once there's a
real paying customer in that vertical to validate against. Prospector
should degrade gracefully for a vertical with no signal source yet (say so
explicitly in the UI — "not available for this industry yet" — not show
empty/wrong data). Matches the standing architecture principle already in
the PRD: "should not be over-engineered for scale it doesn't need yet."

### 5. Disqualifiers become user-editable
**Status: done (2026-08-01).** The CISO-rule pattern (a real, non-obvious
personal disqualifier despite otherwise-matching experience) generalizes
to: gather this kind of disqualifier during the **existing** gap-probing
interview (`src/profile/interview.py`), store it per-user, and apply it in
scoring instructions — not hardcoded in Python for one person.

Implementation note: the live "gap-probing interview" turned out to be the
resume-drafting flow's `clarifying_questions` (Results tab, per-job) —
`src/profile/interview.py`'s own `detect_gaps()` function was already
dead code (no UI ever called it) before this change, so that's the
mechanism this extends, not that one. Confirmed with Zahir 2026-08-01
(chose the reactive option: disqualifiers surface organically when Claude
notices a borderline posting during drafting, not a static upfront
question, since a new user usually can't name their own disqualifiers in
the abstract before seeing a real borderline case). `save_answer()` gained
an `is_disqualifier` flag on `gap_interview_answers` entries;
`SCORE_SYSTEM_PROMPT` reads it generically instead of hardcoding the CISO
text. `detect_gaps()`/`role_skills.json` remain wired up (now with real
data via point 2 above) but still have no caller — still available for a
future upfront-gap-check feature if one gets built.

## Explicitly out of scope for this branch

- Packaging, update delivery, licensing/billing — the other three
  branches' jobs; this branch is product logic only.
- Building out non-life-sciences Prospector signal sources speculatively —
  per point 4 above, incremental and demand-driven, not upfront.
- Marketing/pricing/positioning decisions about which verticals to target
  first — business calls for Zahir, not a design output of this branch.

## A note on shared files

`src/ui/app.py`, `config/settings.yaml`, `src/profile/interview.py`, and
similar shared files have a history of being touched by multiple
concurrent sessions at once (see the Panga project memory, "Concurrent
sessions" entry). Check `git diff` before committing here — don't sweep up
another branch's in-progress changes. This branch in particular touches
core files (`interview.py`, `skills/`) that other sessions may also be
working in — check for conflicts early, not just at merge time.
