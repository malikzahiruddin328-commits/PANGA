# Daily job search + compatibility scoring

Not a Python build step - runs as a Claude scheduled task (like the Gmail
call-to-action monitor), since scoring requires genuine reasoning against
the master profile, not a keyword heuristic (per PRD §11 LLM architecture).

- **Task ID:** `panga-daily-job-search`
- **Schedule:** once daily, 7:26am local
- **Task file (lives outside this repo):** `C:\Users\User\.claude\scheduled-tasks\panga-daily-job-search\SKILL.md`
- **What it does each run:**
  1. Searches USAJOBS by target-role keyword (wide net, not restricted to
     any job category - see below) plus job-series codes as a supplementary
     net, saves new results to `data/jobs/jobs.json` (deduped automatically).
  2. Searches ZipRecruiter and Dice (MCP connector tools, added 2026-07-29)
     by target-role keyword, normalizes via `search/boards.py`, saves same
     as USAJOBS. This is the "hook" for those channels - the Streamlit "Run
     now" button still can't reach them directly (no LLM/connector access
     from Streamlit), same constraint as ever; skips a connector gracefully
     if it's not connected rather than failing the whole run.
  3. Finds jobs missing a `fit_score` (from any of the three sources),
     reasons about fit against `data/profile/structured/master_profile.json`
     (seniority, domain, the CISO/security-officer disqualification - see
     below), writes `fit_score` (0-100) + a plain-language `fit_rationale`
     to each.
  4. Checks for any unreviewed "not interested" reasons (see below) - just
     detects, doesn't evaluate them itself.
  5. Sends one push notification if a new job scored 60+ and/or unreviewed
     rejection reasons exist; stays silent if neither applies.
- **Cost tradeoff:** runs once/day, not multiple times, since scoring is
  real reasoning work per job, not a cheap mechanical check - matching
  Zahir's own framing ("jobs that come on daily basis need to be validated
  like this").

## Why keyword search, not a job-category filter

Tried restricting search to `job_category_code=2210` (Information Technology
Management) first - it seemed like a precision win, but USAJOBS
classification turned out to be inconsistent relative to actual job
content: two of the best real matches found, "Audit Director (IT)" and
"Head of Innovation," are filed under Auditing (0511) and Program
Management (0340), not IT Management at all. A hard category filter would
have silently excluded both. Switched to keyword search by target-role name
(wide net, catches roles regardless of misclassification) with job-series
search as a supplementary net, and let the compatibility score - not the
search filter - do the actual quality filtering. Confirmed 2026-07-29.

## CISO disqualification

Zahir has explicitly said he does not consider himself qualified for
CISO-titled or other specialized security-officer-titled roles (SISO, "IT
Security Officer"), despite his broader cybersecurity oversight experience
as CIO/Head of IT - he lacks the additional specialized security
qualifications those roles require. This is saved as a
`gap_interview_answers` entry in the master profile (dated 2026-07-29) and
referenced explicitly in the scheduled task's scoring instructions - score
these low regardless of subject-matter proximity.

## "Not interested" feedback loop (PRD §13, built 2026-07-29)

Marking a job "not interested" in the app hides it from the Results screen
immediately (there's a checkbox to unhide - nothing is deleted). If Zahir
gives a reason, `applications.upsert_application()` marks it
`skip_reason_reviewed: False`. The scheduled task only *detects* unreviewed
reasons and mentions the count in its notification - it deliberately does
NOT evaluate them itself, since that needs genuine back-and-forth reasoning
(what the reason implies for future searches, presented as ranked options
with a recommendation) that only makes sense in a live conversation. To
actually get that evaluation: bring it up with Claude directly, or wait for
the scheduled task's notification to prompt you to. Claude calls
`applications.mark_skip_reason_reviewed()` once it's actually walked through
the recommendation with Zahir, not before.

## Manually re-running or adjusting the score

The scoring pass isn't code - it's Claude's reasoning, so there's no
function to call for a full re-score. To adjust: either wait for the next
daily run (only scores new jobs), or ask Claude directly to re-score
specific jobs or the full set with updated criteria.
