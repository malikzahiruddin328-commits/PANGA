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
  2. Finds jobs missing a `fit_score`, reasons about fit against
     `data/profile/structured/master_profile.json` (seniority, domain,
     the CISO/security-officer disqualification - see below), writes
     `fit_score` (0-100) + a plain-language `fit_rationale` to each.
  3. Sends one push notification only if a new job scores 60+; stays silent
     otherwise.
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

## Manually re-running or adjusting the score

The scoring pass isn't code - it's Claude's reasoning, so there's no
function to call for a full re-score. To adjust: either wait for the next
daily run (only scores new jobs), or ask Claude directly to re-score
specific jobs or the full set with updated criteria.
