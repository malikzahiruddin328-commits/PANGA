# Panga — Learn Engine Spec

Moved out of `docs/job-search-automation-prd.md` §17 on 2026-08-11 (Zahir's
request, via Panga-Documentor), same pattern as
`docs/score-first-resume-flow-spec.md` — kept as its own dedicated doc
rather than inlined into `docs/frs.md` given its size. Referenced from
`docs/frs.md`.

---

## 17. Learn Engine — Cross-Cutting Feedback Loop (Designed and built 2026-07-30, data-gathering half)

**What changed:** Learn started as one Prospector funnel stage (§16d),
scoped to correlating strategy tags with application/outreach outcomes.
Zahir asked to broaden it: every part of Panga that makes a prediction or a
judgment call should feed the *same* feedback loop, not just Prospector's
own new tables. This section replaces §16c/§16d's analysis pieces with one
mechanism spanning the whole app.

**No new mandatory logging table.** Rather than adding a universal
"decisions" ledger that every module has to remember to write to (real
abstraction cost for a non-developer-maintained codebase, and a departure
from how every other Panga feature was built — see the general
no-premature-abstraction principle in `CLAUDE.md`), the Learn Engine reads
the *prediction* and the *outcome* each subsystem already stores in its own
natural place:

| Subsystem | "Decision" it already records | "Outcome" it already records |
|---|---|---|
| Compatibility scoring (§9) | `jobs.fit_score` | `applications.status` (applied → interview → offer/rejected) |
| Search channels/cadence (§8) | which channels searched, how often | new-listings-found-per-run, cost-per-run (§8 already planned tracking this) |
| Target-account qualification (§16a) | `target_accounts.signals` / `status` | whether that company later posts a real job Zahir applies to |
| Outreach (§16b) | `channel`, cold vs. LinkedIn-connection-sourced contact | `outreach.status` (responded / no-response) |
| Strategy tags (§16d) | `strategy_tag` on an application/outreach record | that record's downstream outcome |
| LinkedIn profile (§13) | which suggested edits Zahir accepted vs. dismissed | recruiter contact rate after the edit (self-reported/observed) |
| Interview prep (§13) | persona/question approach used for a round | how the interview actually went (a lightweight optional "how did it go?" field to add to `interview_prep.py`, since this outcome can only ever be self-reported) |

**Mechanism:** an on-demand reasoning pass — not scheduled, since (like
rejection diagnosis) it needs Zahir to read and react, not just receive a
silent notification. Claude reads across the tables above, joins decisions
to outcomes, and looks for patterns a single-table view can't show — e.g.
"jobs scored 70+ get interviews at 3x the rate of 30–49, the default
30-point display threshold may be hiding a lot of noise," or "warm-intro
outreach sourced from your LinkedIn connections gets responses 4x more
than cold outreach — worth prioritizing," or "the funding/IPO signal type
has never once led to a real application — not yet worth the build effort
it'd take." Output format matches what Zahir already asked for in the
skip-reason feedback loop (§13): plain-language findings, then "Option 1
(Recommended) + description, Option 2, Option 3..." — never a raw dashboard
of numbers with no interpretation.

**Autonomy boundary (confirmed 2026-07-30):** the Learn Engine only ever
recommends — it never changes a score threshold, search weighting,
qualification rule, or anything else on its own. Zahir confirms every
change, same rule as every other judgment call in Panga (skip-reason
review, rejection diagnosis, CTA drafting). This was a deliberate choice
over letting it auto-tune mechanical parameters like §8's search cadence,
to keep exactly one trust model across the whole app rather than a
patchwork of which parts are allowed to self-adjust.

**Expect thin output early.** Like `role_skills` (§4, "grows over time as
new roles/industries are encountered"), the Learn Engine needs enough
decision-outcome pairs before a pattern is more than noise. Early runs may
legitimately say "not enough data yet on X" rather than force a finding —
that's correct behavior, not a bug, and shouldn't be read as the feature
being broken.

**Built 2026-07-30 (build step 8 of 8, data-gathering half —
`src/prospector/learn_engine.py` + "Insights" section on the Prospector
tab):** `gather_learn_engine_input()` joins scoring-vs-outcome
(applications × jobs), target-account-vs-real-posting (cross-referencing
`target_accounts` company names against `jobs.organization`, with light
punctuation normalization), outreach-vs-response, and interview-outcomes
(the new self-reported "how did it go?" field on `interview_prep.py`
rounds, added this same build step) into one structure. Same "Python
gathers, Claude reasons" split as `rejection_diagnosis.py` - this module
makes no pattern-finding judgment itself.

**One input from the original table above is honestly absent, not
silently faked:** LinkedIn profile edits vs. recruiter-contact-rate has no
capture mechanism anywhere in Panga - there's no way today to log "got
contacted on LinkedIn after a profile edit." The gathered data structure
includes an explicit `known_gaps` list saying so, surfaced directly in the
UI, rather than pretending that row of the design table is covered.
Closing it would need a small new manual-log feature - flagged as future
work, not attempted here. Search-cadence metrics (§8's row) are similarly
absent - never built, as already noted in §17's own design table above.

Regression-tested (cross-table joins, company-name-normalized matching,
the interview-outcome filter excluding rounds with no outcome recorded)
with synthetic data. Verified live: real data flows through correctly (78
target accounts, 2 scored applications, 0 outreach/interview-outcomes
since none exist yet) with no errors, on a fresh isolated Streamlit
instance (see the port-8501-stale-process note under §16b — verifying
UI-heavy changes on an isolated port is now the established fallback when
the shared long-running dev server's cached modules are suspect).

**This closes out Prospector's full build sequencing (steps 1-8).** What
was designed 2026-07-30 is now built 2026-07-30, same day - see §13's
backlog table for the final status.

**UI placement:** lives inside the existing **Prospector tab** (§16) as an
"Insights" or "Learn" section with a "Run analysis" button, even though the
data it reads spans the whole app, not just Prospector's own tables — it
doesn't need a separate tab of its own, since it's a report-and-recommend
tool rather than something used moment-to-moment.

**Build sequencing:** last, by construction — it needs real outcome
history from scoring, target accounts, and outreach to have anything to
say. Folded into build step 8 above, not a separate step.

**Fixed 2026-08-10 (Zahir's adversarial self-audit request, #22):** the
"nothing to analyze yet" gate on the "Run Learn Engine analysis" button
summed raw list lengths across all four `learn_input` categories, but
`target_account_vs_outcome` appends ONE ENTRY PER TARGET ACCOUNT
UNCONDITIONALLY, not just ones with a real correlatable outcome - with 78
real target accounts already in the store, that one list alone always
pushed the sum well past zero regardless of whether `scoring_vs_outcome`/
`outreach_vs_outcome`/`interview_outcomes` had anything real in them. The
gate could never correctly say "nothing to analyze yet" again once
Prospector had been used at all. Fixed with
`_count_analyzeable_learn_inputs()` in `src/ui/app.py`: only a
`target_account_vs_outcome` entry with `real_posting_appeared_since=True`
counts - a "still watching, nothing has happened yet" account genuinely
has no outcome to correlate its qualification against. The other three
categories are left as raw counts, since each is already conditionally
populated in `learn_engine.py` (same "meaningful count, not raw list
length" fix as the sibling rejection-diagnosis gate above it, which
already used explicit `rejected_count`/`not_interested_with_reason_count`
rather than a bare `len()`). Checked against real production data: today
the gate happens to still pass either way (49 scored applications alone
clear zero), so this wasn't visibly broken yet - but the raw-`len()`
version would have silently defeated the gate the moment scoring/
outreach/interview data went back to zero (a fresh install, or historical
data cleanup), which is exactly the scenario regression-tested here (78
synthetic target accounts, zero everything else, gate correctly reports
nothing analyzeable). Tests: `tests/test_learn_engine_gate.py`, 5 cases
including the real 78-account reproduction. Full suite: 816 passed.

