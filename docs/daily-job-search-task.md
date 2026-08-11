# Daily job search + compatibility scoring

Not a Python build step - runs as a Claude scheduled task (like the Gmail
call-to-action monitor), since scoring requires genuine reasoning against
the master profile, not a keyword heuristic (per `docs/frs.md` §11 LLM architecture).

- **Task ID:** `panga-daily-job-search`
- **Schedule:** once daily, 7:26am local
- **Task file (lives outside this repo):** `C:\Users\User\.claude\scheduled-tasks\panga-daily-job-search\SKILL.md`
- **What it does each run:**
  1. Searches USAJOBS by target-role keyword (wide net, not restricted to
     any job category - see below) plus job-series codes as a supplementary
     net, saves new results to `data/jobs/jobs.json` (deduped automatically).
  2. Searches ZipRecruiter, Dice, and Indeed (MCP connector tools, added
     2026-07-29) by target-role keyword, normalizes via `search/boards.py`,
     saves same as USAJOBS. This is the "hook" for those channels - the
     Streamlit "Run now" button still can't reach them directly (no
     LLM/connector access from Streamlit), same constraint as ever; skips a
     connector gracefully if it's not connected rather than failing the
     whole run. Indeed's tool returns one formatted markdown blob instead of
     structured JSON like the other two, and its "Job Id" field isn't stable
     across searches - `normalize_indeed_jobs()` parses the blob and uses the
     short code in the posting URL as the stable dedup key instead. The
     "Jobs and Careers" community connector (a different tool, canonical
     "job-search") was evaluated and rejected for this pipeline - its
     results have no posting URL or stable ID, so there's nothing to dedupe
     on or link to.
  3. Searches company career sites via ATS APIs, not scraping (added
     2026-07-29, `search/company_sites.py`) - `search_workday_jobs()` and
     `search_smartrecruiters_jobs()` call the same JSON endpoints a
     company's own careers page JavaScript calls (Workday's CXS API,
     SmartRecruiters' public posting API), zero HTML parsing, zero
     scraping risk. Confirmed against Eisai/IQVIA (Workday) and AbbVie
     (SmartRecruiters) - real companies from Zahir's own background. Not
     every company uses one of these two ATS platforms, but it covers a
     large share of large employers. More companies can be added by
     finding their tenant/site (Workday) or company-id (SmartRecruiters)
     via web search.
  4. Searches Planet Pharma (added 2026-07-29, `search/industry_boards.py`)
     - genuine HTML scraping (no API), the one board built so far out of
     19 researched (see "Industry board reconnaissance" below for why only
     one). Fetches the general listing, no server-side category filter
     (FacetWP's filter only applies via JS, not a URL param) - relevance
     filtering happens via compatibility scoring like every other source.
  5. Finds jobs missing a `fit_score` (from any source),
     reasons about fit against `data/profile/structured/master_profile.json`
     (seniority, domain, the CISO/security-officer disqualification - see
     below), writes `fit_score` (0-100) + a plain-language `fit_rationale`
     to each.
  6. Checks for any unreviewed "not interested" reasons (see below) - just
     detects, doesn't evaluate them itself.
  7. Sends one push notification if a new job scored 60+ and/or unreviewed
     rejection reasons exist; stays silent if neither applies.
- **Cost tradeoff:** runs once/day, not multiple times, since scoring is
  real reasoning work per job, not a cheap mechanical check - matching
  Zahir's own framing ("jobs that come on daily basis need to be validated
  like this").

## Industry board reconnaissance (2026-07-29)

Before writing any scraper code against the 19 candidate boards in
`config/industry_job_boards.yaml`, checked each for actual scrapeability
(does raw HTML contain listing data, or is it JS-rendered / bot-blocked).
Result: **5 confirmed scrapeable** (Planet Pharma, BioSpace, Beacon Hill
Life Sciences, Atrium, GForce Life Sciences), **10 rejected** (403/429
blocking, or no jobs board exists at all), **4 need a headless browser**
(JS-rendered, not attempted with plain requests). Built so far: Planet
Pharma only, as a proof of the pattern - the other 4 confirmed-scrapeable
ones are documented in the YAML with their confirmed-working URLs, ready to
build the same way when there's time.

Notable finding: the three sites originally flagged as most IT-relevant
(Life Search Technologies, TSP Life Sciences, Frontline Source Group) all
turned out blocked or JS-only. Relevance and ease-of-scraping are
independent - don't assume the most relevant source is also the easiest
to build against.

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

## "Not interested" feedback loop (`docs/backlog-log.md` §13, built 2026-07-29)

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
