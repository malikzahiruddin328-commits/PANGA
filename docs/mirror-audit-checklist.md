# Mirror's recurring audit checklist

This is Mirror's durable, standing checklist for the recurring
`panga-mirror-audit` pass — not a one-time report, a living list Mirror
re-runs against every time. Read-only investigation only; Mirror never
edits code itself, it flags/reports findings to the hub.

When a new category of miss is found (by Zahir live-testing, by another
session, or by Mirror itself), add it here as its own numbered section
with a real example — don't let it live only in a chat message that
scrolls out of context. Keep each category's method concrete enough that
it doesn't depend on Mirror's in-the-moment judgment to remember what to
look for.

## 1. Doc-vs-code drift

Comments/docstrings claiming behavior the code doesn't actually have -
stale descriptions left behind after a refactor, a "TODO" that was
actually finished, a docstring describing an old approach the code moved
away from.

**Method:** for any comment/docstring making a specific behavioral claim
("this always...", "never...", "returns X when Y"), read the actual code
beneath it and confirm the claim still holds. Prioritize files with recent
history of rapid same-day rewrites (most likely to drift).

## 2. Internal engineering references leaking into user-facing text

Spec-doc shorthand (PRD §-numbers), raw module/file names, internal
build-step references, session/branch names, etc. ending up in text
actually rendered to Zahir - not comments, real `st.markdown()`/label/
button text he sees on screen.

**Method:** grep the UI layer (`src/ui/app.py` and friends) for patterns
like `§`, `PRD`, `.py` file references, branch-name-shaped strings, etc.
inside string literals passed to Streamlit render calls. Eyeball each hit
for whether it's genuinely user-facing (vs. a docstring, which is fine).

**Real example found 2026-08-09:** 3 spots in the Prospector tab had
"(PRD §16/§17)" sitting directly in text Zahir actually saw. Fixed same
day (`0c0a52c`).

## 3. UI control labels vs. what the code actually does

A button/control's own label implies an action ("Answer more questions,"
"Compute X," "Refresh Y," "Analyze Z") that its click handler doesn't
actually perform - a live functional lie in the interface, not just
confusing text.

**Method:** for every `st.button`/interactive control whose label implies
an action beyond a bare rerun, trace the click handler to what it actually
calls. Flag any handler that's just `st.rerun()` alone, or that doesn't
call whatever the label's verb implies.

**Real example found 2026-08-09 (by Zahir, not Mirror - this category
didn't exist as a check until this exact miss):** the Analyze Fit panel's
"Answer more questions" button (`src/ui/app.py` ~1113) did nothing but
`st.rerun()` - never actually requested a new round of questions. Routed
to ATS Engine as a live blocker same day.

**Status history.** 2026-08-09 re-audit: STILL BROKEN. `7a99c89` ("Fix
Analyze Fit panel: real score projection + point values for free-form
questions") was the same-day follow-up commit, but it only touched the
point-value badge and caption wording - it never touched the button, which
was still byte-for-byte `if st.button("Answer more questions", ...):
st.rerun()`.

**Status as of Mirror's 2026-08-10 re-audit: RESOLVED, independently
verified in current master code (not from the commit title).** `6dc6d2e`
("Make 'Answer more questions' actually request a new round of AI
questions") is the commit that genuinely fixed it. `app.py:1388-1423` now
calls `_request_additional_gap_questions()` for real, persists the merged
questions plus the scan fingerprint, and shows an honest "No more real gaps
found" toast when the AI legitimately returns nothing. The two-audit gap
between the commit that *claimed* the fix and the commit that *made* it is
the reason this file's independent-verification standard exists - keep
re-checking resolved items rather than deleting them.

## 4. CLAUDE.md's own documented known-failure patterns

Panga/CLAUDE.md already has a "Known failure patterns (2026-08-08
retrospective)" section — four specific, previously-real bugs written down
precisely so new/touched code could be checked against them without
rediscovering each one tab by tab. This audit never actually cross-checked
against that list until 2026-08-09, despite it sitting right there the
whole time — a real gap in how this checklist was built (reactive to live
misses only, not to already-documented risk), not something excusable as
"we didn't know."

**Method — check any new/touched code against all four directly:**
1. **Streamlit expander state** — an `st.expander(..., key=...)` meant to
   stay open across an unrelated interaction needs `on_change="rerun"` on
   the same expander, or Python's `expanded=` silently wins every rerun.
2. **Regenerate-from-scratch regression risk** — any flow that re-drafts
   AI content instead of editing in place can lose a fact/keyword/detail
   the previous draft had, even when the new draft is objectively better.
   Real, recurring example: resume regenerate dropping a matched ATS
   keyword while fixing another one (recurred again 2026-08-09).
3. **AI output feeding a literal/deterministic downstream check** needs a
   real code-level backstop, not just prompt wording — prompt instructions
   alone have proven unreliable multiple times on this project.
4. **A feature can be fully built and merged but never actually turned
   on** — check that any feature gated behind user config or a scheduled
   task actually has that config populated and that task actually created,
   not just that the code path is reachable.

## 5. Cross-document factual consistency

Cover letter/exec bio/leadership summary each drafted via a separate,
independent AI call sharing only the raw job+profile context - never the
resume text drafted moments earlier in the same batch. Each document can
independently phrase, round, or emphasize the same underlying fact
differently, so a reviewer who spots the same number/date/achievement
stated two different ways across two of Zahir's own documents reads that
as a real inconsistency, even though every individual document is
accurate against the profile on its own.

**Method:** for any feature drafting multiple related documents from the
same source facts, check whether later documents receive the
already-drafted earlier ones as context (or at minimum an instruction to
stay consistent with them), not just the raw underlying data a second
time.

**Real example found 2026-08-09:** `generate_documents()`
(`src/tailoring/drafting.py`) drafted cover_letter/exec_bio/
leadership_summary with zero visibility into the resume text drafted
earlier in the same batch. Fixed same day (`9ba0ebe`) - non-resume
documents now receive the resume text with an explicit
stay-consistent instruction, including not stating a resume's
unresolved "?"-hedged claim as settled fact elsewhere.

## 6. Either/or requirement extraction accuracy

A JD phrasing a requirement as a substitutable alternative ("field A, B,
or C," "Master's OR Bachelor's + experience," a multi-tier degree chain)
getting flattened into separate, independently-required flat keywords
instead of one satisfiable-by-any-member group - falsely dinging a
candidate who satisfies one alternative for not also holding every other
one.

**Method:** for any job's extracted required/preferred keywords, read the
actual posting text and check for "or"/alternative language the extracted
list doesn't reflect as a group. Don't assume a fix covers every shape of
this pattern (two-way, N-tier chains, dual-role terms) just because it
covers one.

**Real example found 2026-08-09 (by Zahir, hand-reading a JD):** a
systemic sweep once this class was confirmed found 5 of 10 real stored
jobs affected - including a 5-tier degree-level chain (Amgen), a
completely dropped field name ("Business," not just flattened - missing
entirely), and a term needing extraction in two roles at once ("Supply
Chain Management" as both a degree-field alternative and its own
substantive-experience requirement). Fixed across several same-day
commits (`769eb3c`, `129ae16`).

## 7. Silent data loss during AI extraction

Worse than category 6's mis-grouping: a real, named requirement in the
source text vanishing from the extracted output entirely, with nothing
flagging that it went missing.

**Method:** for any AI-extraction step, spot-check the extracted output's
completeness against the actual source text item-by-item, not just its
shape/structure - a field disappearing entirely looks identical to "the
posting never mentioned it" unless someone reads the original text.

**Real example found 2026-08-09:** "Business" was silently dropped as an
acceptable degree field on two separate real postings, while every other
field in the exact same list survived - not a truncation (later keywords
in both lists were extracted fine), most likely the model judging it too
generic to bother with. Fixed with an explicit "don't omit a named
alternative for reading as broad or generic" rule (`129ae16`).

## 8. Passive suggestion instead of active correction

An AI-driven feature correctly identifying a real gap, but only
suggesting the user go fix it themselves ("consider adding X if it
applies") instead of checking whether a fact already on file would
already close that gap.

**Method:** for any "here's a suggestion" output, check whether the
underlying data already contains something that would satisfy the
suggestion - if so, the feature should be closing it directly, not
leaving it as a to-do the user has to notice and act on every time.

**Real example found 2026-08-09:** the resume-writing AI suggested
"consider adding 'commercial scale readiness' if it genuinely applies" as
a passive next-action, even though the candidate's own profile already
had a real, on-point fact supporting it (a product's revenue growth from
under $500K to $1B). Fixed by having the AI actively cross-reference the
whole profile for genuine support before falling back to a suggestion
(`ee363f4`).

**Same shape, different surface, 2026-08-10:** this category isn't
limited to suggestion text - a gap-*confirmation question* asking the
user to confirm something as if it might be unknown is the same failure
if the answer already exists on file. Zahir asked to confirm "Customer
Engagement" and "Design Applications" as gap questions, even though his
structured profile (skills/work history/client engagements/
certifications/education) already substantively covered both - the check
had only looked at the drafted resume text and previously-answered
questions, not the full profile. Fixed via `_profile_supports_skill()`
checking every missing keyword against the full structured profile before
generating a question (`1ed1c8c`). Broadening the method above: check
*any* "does the user need to tell us this" moment (suggestion, gap
question, clarifying prompt) against the full available profile, not just
document-level suggestion text.

## 9. Silent state changes with no user-facing confirmation

An action that genuinely saves/changes real data but shows nothing on
screen to prove it happened - the user has no way to distinguish "worked
silently" from "did nothing," and has to ask someone to check the raw
data to find out.

**Method:** for any button/widget whose action persists data, confirm
there's a visible, immediate confirmation (a toast, a status change) tied
to the actual save succeeding - not just that the save function itself
works correctly in isolation.

**Real example found 2026-08-09:** answering a gap-question in the
Analyze Fit panel saved correctly (confirmed against real stored data),
but the screen gave zero visible feedback either way, and the already-
answered question could still show as "open" for one extra render.
Fixed with a toast + same-cycle rerun (`5b98437`) - which itself exposed
a real pre-existing infinite-loop bug the fix's own live-testing caught
before it shipped.

## 10. AI-dependent code tested only against small synthetic inputs

A test suite that only ever exercises an AI-call code path with a small,
convenient synthetic input can hide a failure that only appears at
realistic real-world scale - the code "works" in every test yet fails on
the first real use.

**Method:** for any code path that sends real user data to an AI call
(a full profile, an accumulated document, a long history), check whether
at least one test uses an input sized close to real production data, not
only a small fixture built for convenience.

**Real example found 2026-08-09:** `request_additional_gap_questions()`
had a hardcoded `max_tokens=3000` and passed every test - because every
test's synthetic profile was tiny. Against Zahir's real ~98,000-character
profile it truncated and crashed on essentially the first real call, a
100%-reproducible failure invisible to every mocked test. Fixed with a
tiered-retry escalation pattern (`306ef30`), found only because Release
Manager live-fire tested it against real data instead of trusting mocks.

## 11. An AI call's trigger changing without re-costing that specific call

A call that was previously gated behind an explicit user click getting
rewired to fire automatically (on render, on open, on a schedule) - costed
against the wrong baseline, or not costed at all, because the change is
framed as a UX improvement rather than a spend change. Compounding factor:
an auto-fire usually has no "is this record still actionable?" gate, so it
also spends on records the user can no longer do anything about.

**Method:** whenever a commit moves an AI call from click-triggered to
automatic, (a) look up that specific call's own real mean/max in
`data/cost_log.json` via `cost_log.load_cost_log()` - not a sibling call's,
and not the commit author's estimate; (b) count how many real stored
records would fire it on the next render, using the project's own
`*_is_current()`/guard predicate against live data; (c) check whether
terminal-state records (applied / not interested / closed by employer) are
excluded. Multiply (a) x (b) and compare against the project's total spend
to date - if the queued exposure is a meaningful fraction of everything
spent so far, that's the finding.

**Real example found 2026-08-10 (by Mirror):** commit `584653d` auto-fired
`request_additional_gap_questions()` on every Analyze Fit render for an
unscanned resume version. That call's real logged mean is **$0.3699**
(n=11) - the single most expensive purpose in the whole log, ~3x a full
`draft_resume` ($0.1251). 29 real applications were primed to fire it,
24 of them already applied/closed/not-interested - ~$10.73 queued, ~$8.88
of it unactionable, against $8.06 total project spend to date. The commit
messages costed the *retry-on-failure* risk (`08b3fd1`) and the *bulk-loop*
risk carefully, but never the per-call cost of the call being automated;
the same session's `47b5983` did do a real cost check for the cheaper
resume loop, so the discipline existed and just wasn't applied here.

## 12. A cost/impact estimate in a commit message, checked against the log

An estimate stated as a verified fact in a commit message ("real cost check
against production cost_log.json") that used a stale or unrepresentative
slice of the log, or projected a multi-call loop from single-call means
when the later calls are systematically larger.

**Method:** re-derive the number from `cost_log.load_cost_log()` yourself.
For loops, check whether later iterations carry bigger prompts (a retry
that includes the previous attempt's full text does) - a mean-of-first-
calls x N projection understates those. Look for same-job call clusters
spaced ~60-90s apart in the log; those are one click's internal attempts.

**Real example found 2026-08-10 (by Mirror):** `47b5983` claimed
"draft_resume calls average $0.104, so worst case is ~$0.31/click." Real
figures across all 24 logged calls: mean $0.1251, max $0.2649 - 3x max is
$0.795. A real 3-attempt cluster on job 4449005464 (03:43:33 $0.1171 /
03:45:00 $0.2151 / 03:46:12 $0.1866) cost **$0.5188 in one click**, 67%
over the stated worst case, logged the same night the feature shipped.

## 13. Seam check — disconnected parallel systems doing the same job twice

A feature built for one code path but never extended to a structurally
identical sibling path, or two related capabilities built in the same
area that were never connected - each looks complete reviewing it in
isolation, the gap only shows up when asking "does this actually connect
to / cover the same ground as the thing right next to it?"

**Method:** for any feature, check whether every structurally-similar
entry point into the same underlying action received the same treatment,
not just the one path that was live-tested. When two things are built in
the same area (or even the same commit) that do adjacent jobs, check
whether they were meant to close the loop with each other and actually
do.

**Real example, original (2026-08-06):** `ats_next_actions`
(deterministic missing-keyword list) and `clarifying_questions`
(interactive, AI-judged, persisted gap questions) were built in the same
commit (`a060332`) but never connected - a user-facing "how to raise your
score" list sat there as inert text with no way to act on it, while a
nearly-identical interactive mechanism existed one tab over.

**Real example, found this same class again 2026-08-10 (Zahir, live
testing):** `render_paste_jd_prompt_before_drafting` had two divergent
branches for saving a job description - one via the browser extension's
capture, one via manual paste. The extension-capture branch got the full
Analyze Fit + auto-gap-scan treatment; the manual-paste branch only
persisted the text and showed a toast, with no equivalent surfacing at
all ("i just saved the jd... and nothing happened"). Same underlying
action (a JD got saved), two independently-built paths, only one of them
kept up to date as the feature evolved. Fixed same day (`f4bee21`) by
merging both branches into one shared tail. This category existed in
Mirror's own working memory since 2026-08-06 but was never migrated into
this checklist file when it was created 2026-08-09 - found and corrected
during Mirror's 2026-08-10 self-audit of its own checklist, not by
anyone else pointing it out.

## 14. Present but unsurfaced — a capability that works but isn't discoverable

Distinct from category 1 (missing entirely) and category 13 (disconnected
from a related feature) - the capability genuinely works end-to-end in
code, but a real user looking at the screen, without already knowing it
exists, would never find or use it: buried in an unlabeled expander, no
button/affordance pointing to it, discoverable only by already knowing to
click something non-obvious.

**Method:** for any UI surface reviewed, explicitly ask "would someone
who didn't already know this existed find it from the screen alone?" -
not just whether the code path is reachable, but whether the UI itself
surfaces it clearly enough to be found.

**Real example (2026-08-06):** a Results-tab capability was described as
"mostly already possible but badly surfaced, not missing entirely" -
found only after Zahir asked about it directly, not caught proactively by
a UI review that had already marked that tab reviewed. Same origin story
as category 13 - established in Mirror's working memory 2026-08-06, never
migrated into this file until the 2026-08-10 self-audit.

## 15. UI/UX visual review (design-critique + accessibility-review)

Structural verification of visual layout/usability, not just functional
correctness - proportionality, contrast, touch-target size, icon
labeling for screen readers. Distinct from category 3 (does a control's
handler do what its label says) - this category covers whether the
control/layout is well-formed as a visual/accessible interface at all.

**Method:** run the `design-critique` and `accessibility-review` skills
against the live app for any UI surface reviewed, via a registered
dev-preview slot (`.claude/launch.json`), `preview_stop` when done.
Don't rely only on an accessibility-tree read for proportionality checks
- an accessibility tree reports a stat tile as structurally fine
regardless of whether its content renders in 20px or 250px of column
width. For any stat-row/multi-column layout, run a
`getBoundingClientRect()` pass comparing each column's actual content
width against its allocated container width, the same computed-geometry
method used for touch-target checks, extended to proportionality
generally rather than left to be noticed by chance.

**Real example (2026-08-06):** 5 stat-column tiles on Call to Action
(Offer/Interview request/etc.) rendered as disproportionately wide
full-width columns for a single digit - missed during a pass that relied
on accessibility-tree reads plus ad hoc JS checks for specific failure
classes (contrast, touch targets) rather than a structural geometry sweep
of every multi-column layout. Root cause that session: the Browser
pane's screenshot tool wasn't working, so live verification defaulted to
whatever could be checked without visual rendering. Same origin story as
categories 13-14 - established in Mirror's working memory 2026-08-06,
never migrated into this file until the 2026-08-10 self-audit.

## 16. CLAUDE.md's own stated principles vs. what the code actually does

Distinct from category 4 (CLAUDE.md's *named, itemized* known-failure
patterns list) - this is broader: CLAUDE.md also states general standing
engineering principles (cost/spend blast radius, prompt caching for
content that repeats across calls, race conditions, performance,
circuit breakers) as declarations of how the codebase should work. A
principle being *written down* is not evidence it's actually *true* of
the code - nobody had checked whether declared principles were followed
in practice until a real cost crisis forced it.

**Method:** for each standing principle CLAUDE.md declares, pick a real
call site it should govern and verify directly - don't infer compliance
from the principle's existence. For caching: does a call that repeats the
same large content (a profile, a document) across invocations actually
use `cache_control`/prompt caching, or re-send the full content every
time? For cost: pull the real `cost_log.json` figures for that call
(category 11/12's method) rather than trusting an estimate. For race
conditions/locking: check the actual store's mutators for `locked()`
calls (category 4 sub-pattern 1's method, generalized to any shared
store, not just ones already flagged). This audit's own role now sits
alongside Panga-General and Panga-Documentor's joint closeout-verification
duty against CLAUDE.md principles - Mirror is the independent backstop
that catches what closeout review misses, not a replacement for it.

**Real example found 2026-08-10 (Zahir, real cost crisis, not Mirror):**
CLAUDE.md called for prompt caching on content that repeats across calls
(a profile embedded in every scoring call) for a while, but `fit_score`
never implemented it - the full profile JSON was re-sent on every call,
~$0.31/call, real cost ~$60+/day this week. The gap between "the
principle is written down" and "the principle is actually followed"
went uncaught until the cost forced it. Fix in progress
(`feature/fit-score-prompt-caching`) as of this writing - verify it
actually lands and actually reduces real logged cost per call before
treating this as closed, per this file's own independent-verification
standard (see category 3's status-history pattern for how to track a
fix through to actual resolution, not just a commit claiming one).

## 17. Confirm which mechanism is actually live before declaring automation broken

When two systems with similar names/purposes could both plausibly be
"the" automation for something, checking only one of them and reporting
"broken" on that basis is itself a real audit failure, not a safe
default - the same silent-gap standard this whole file exists to catch
applies to Mirror's own investigation method, not just the code under
review.

**Method:** before concluding a scheduled/automated process isn't
running, identify ALL mechanisms that could plausibly be the real one
(here: Windows Task Scheduler vs. Claude Code's own scheduled-tasks
system, `mcp__scheduled-tasks__list_scheduled_tasks`) and check every
candidate, not just the one that's easiest to check via Bash (`schtasks`)
or the one docs/scripts happen to name. Cross-verify against independent
real-world evidence too (did a real new record actually land in
production data today, not just "does a task exist") before asserting
something is or isn't working.

**Real example found 2026-08-11 (Mirror's own mistake, caught by
Panga-General's independent re-check, not by Mirror):** Mirror checked
`schtasks /Query` and found Panga-JobAlertScan missing and
Panga-CtaFulfillment disabled, and reported both as silently broken -
real findings against Windows Task Scheduler, but that system is dormant
scaffolding for a future not-yet-shipped packaged app, not what's
actually running Panga today. The live mechanism is Claude Code's own
scheduled-tasks system, which Mirror never checked. Panga-General
independently verified against 3 real signals (the real scheduler
showing both tasks enabled with today's `lastRunAt`, a real new job
landing in production data that day, a real healthy CTA run report) that
the automation was genuinely fine. What WAS real: the dashboard's "last
synced" indicator itself was stale/wrong by 2+ days - likely the actual
source of the "looks broken" impression in the first place, and the
thing that was actually worth fixing. Retracted the false part of the
finding same-day per this file's own independent-verification standard;
this category exists so the next sweep checks both systems before
concluding either one.

## Process notes

- **This file is Mirror's own memory to maintain, not just the hub's.**
  Whenever Mirror itself notices a genuinely new class of issue during an
  audit - not just when the hub relays one it was told about - Mirror adds
  it here itself, same format as the categories above (name, method, a
  real example once one exists), in the same session it was found. The
  hub only writes an entry directly when the miss was caught by Zahir or
  another session, not Mirror - Mirror should never need someone else to
  transcribe its own finding into its own checklist.
- This file is the audit's memory, not a chat message. Update it the same
  turn a new category is confirmed - don't let a fix land without the
  checklist growing to match.
- Mirror reports findings to the hub (never directly to Zahir or RM),
  same routing rule as every other dedicated session.
- For a genuinely deep, multi-agent adversarially-verified sweep beyond
  what one session's linear read can catch, Zahir can trigger `/code-review
  ultra` himself (a multi-agent cloud review) as a complement to Mirror's
  ongoing lighter-weight recurring pass - that's a heavier, explicitly
  user-triggered tool, not a replacement for this checklist.
