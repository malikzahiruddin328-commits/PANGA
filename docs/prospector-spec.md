# Panga — Prospector Spec

Moved out of `docs/job-search-automation-prd.md` §16 on 2026-08-11 (Zahir's
request, via Panga-Documentor), same pattern as
`docs/score-first-resume-flow-spec.md` — kept as its own dedicated doc
rather than inlined into `docs/frs.md` given its size. Referenced from
`docs/frs.md`. Internal subsection lettering (§16a/b/c/d) is preserved
unchanged below, since it's referenced by name elsewhere (`docs/backlog-log.md`
§13, `docs/frs.md` §4, module docstrings).

---

## 16. Prospector — Personal Marketing / Sales-Funnel Layer (Designed and built 2026-07-30)

Reframes Panga from a job-matching tool into a full sales-funnel-style
lifecycle: **Prospect → Research → Outreach → Application → Response →
Outcome → Learn.** The middle three stages (Research, Application, Response)
are already built (§4/§9/§14) and unchanged by this design. Prospector adds
or upgrades four stages, detailed below. Zahir confirmed scope 2026-07-30:
build all four pieces, and all four candidate signal types for company
targeting (§16a) — no scope was cut.

**Deliberate constraint, unchanged from the rest of Panga:** nothing is ever
sent, submitted, or contacted automatically. Claude drafts; Zahir sends.
This applies to outreach messages (§16b) exactly as it already applies to
application packages (§2/§9) and Gmail replies (§14).

### 16a. Target Accounts (Prospect stage)

New data model, `target_accounts` — parallel to `jobs`, but for companies
worth watching *before* they've posted a role. Same store pattern as the
other tables (JSON, encrypted at rest via `security.crypto_store`, §7/§15).

Fields (draft): `company_name`, `industry`, `status` (watching / qualified /
contacted / stale / disqualified), `signals` (a list of `{signal_type,
source, detail, date_observed}`), `notes`. No fit-score formula yet — start
simple (see qualification rule below) and refine once there's real signal
data to look at, same approach used for job compatibility scoring (§9).

**Four signal types, all in scope, roughly in build order (cheapest/most
proven first):**

1. **Late-stage trial progress** — Phase 3 trials completing, or with
   recent results posted, on ClinicalTrials.gov. Source: the already-
   connected ClinicalTrials.gov MCP connector (`search_by_sponsor`,
   `search_trials` filtered by phase/status) — reuse it rather than write a
   new HTTP client, same principle as reusing ZipRecruiter/Dice/Indeed as
   connectors instead of scraping them (§4b, "Channel expansion").
2. **Regulatory filing activity** — NDA/BLA submissions or status changes
   in openFDA. Source: extend `src/search/company_sites.py`, which already
   pulls openFDA sponsor data for a different purpose (§4c) — same API,
   new query shape.

   **Built 2026-07-30 (`src/prospector/regulatory_filings.py`, plain HTTP
   API - no MCP connector needed, unlike signal 1, so this can run
   standalone/scheduled later):** lives in `prospector/` rather than
   literally extending `company_sites.py` as sketched above - that file's
   purpose is job/company sourcing for the reactive Results pipeline, a
   different concern, and `prospector/` didn't exist when this line was
   written. Query: original (`submissions.submission_type:"ORIG"`),
   approved (`submission_status:"AP"`) applications, restricted to
   `application_number` prefixes NDA*/BLA* only - a broader ORIG+AP search
   also matches ANDA (generic drug) approvals, which aren't a "approaching
   commercial launch" signal (generic manufacturers are already
   commodity-scale). **Real finding:** openFDA's date-range filter doesn't
   reliably scope to the same nested submission that matched the
   type/status filter (a range query for 2023-2026 still returned 1950s/
   1990s approvals) - a known Elasticsearch nested-field limitation, not a
   query mistake. Recency filtering (default 3-year window) is done
   entirely client-side on one fetched page (limit=1000, openFDA's max) -
   a real, disclosed coverage gap: this sees one page of ~5,800+ total
   matches, not a true global "most recent N."

   **Real finding that improved the shared filter:** this signal's live
   data surfaced large/established companies the mega-pharma keyword list
   missed - either abbreviated openFDA sponsor names ("BRISTOL", "NOVO"
   instead of full names) or companies outside the original US/EU-biased
   list (Sun Pharma, Zydus, Amneal, Servier - all large, established
   multinationals). Added to `company_filters.MEGA_PHARMA_KEYWORDS`,
   confirmed against real data, not guessed. Two categories of noise left
   as known, undocumented-away gaps rather than over-fit: a couple of NDA
   holders that aren't typical pharma companies at all (industrial gas
   suppliers holding an NDA for a medical gas product), and any
   established company not on the keyword list at all - Zahir's manual
   disqualify remains the real correction mechanism.

   Regression-tested (recency window, mega-pharma/company filtering,
   detail-string formatting) with synthetic data. Real target_accounts.json
   now holds 40 companies across both signal types (0 yet reach
   `qualified` - no overlap between the two signal sources' companies so
   far, expected this early).
3. **Commercial-build hiring elsewhere** — the company posting roles like
   VP Commercial, Market Access, or Commercial Operations on the job boards
   Panga already searches (§4b/§4c). This isn't a new external source at
   all — it's a new *lens* over jobs Panga is already ingesting: tag a job
   as a signal about its posting company, separate from scoring it as a fit
   for Zahir.

   **Built 2026-07-30 (`src/prospector/commercial_hiring.py`):** curated
   keyword list (VP Commercial, Chief Commercial Officer, Market Access,
   Commercial Operations, etc.) rather than a bare "commercial" substring
   match - a broad probe of real data showed plain "commercial" matches
   noise like "Semester of Service Volunteer - Commercial Diplomacy
   Analyst" (a Dept. of State internship). USAJOBS excluded entirely for
   this signal - government postings aren't a company signal. One signal
   per company (dedup) rather than one per matching posting.

   **Real result was thin, and for a structural reason worth naming
   explicitly:** only 1 of 620 real jobs matched - "Securitas" (an SVP
   Commercial Growth posting), which is itself a known false positive
   (a security-services company, completely unrelated to pharma/life
   sciences - disqualified immediately, not left for review). The
   underlying cause: `jobs.json` was collected to find roles *for Zahir*
   (CIO/Director/etc., via keyword search and a handful of directly-
   integrated company ATS feeds), not to broadly survey every company's
   commercial hiring - so it only incidentally contains other companies'
   commercial-titled postings. Making this signal genuinely useful would
   need broader company-site ATS coverage or a dedicated commercial-title
   search across ZipRecruiter/Dice/Indeed, independent of Zahir's own
   target roles - a real future expansion, not attempted here. Also added
   `iqvia` to `company_filters.py`'s exclusions (a ~$15B contract-research
   company, not pre-commercial, matched repeatedly before being excluded).

   Regression-tested (keyword matching, USAJOBS exclusion, per-company
   dedup) with synthetic data.
4. **Funding or IPO activity** — no existing source covers this. Needs
   source research before it's buildable (candidates to evaluate when this
   signal is picked up: SEC EDGAR filings for S-1/IPO activity, which is
   free and public; general web search as a live, reasoning-driven v0
   mechanism — same "Claude reasons live, Python just stores the result"
   split as tailoring/scoring — rather than standing up a paid data feed).

   **Source research resolved, built 2026-07-30 (`src/prospector/funding_filings.py`):**
   SEC EDGAR's full-text search API (`efts.sec.gov/LATEST/search-index`)
   turned out to be a clean, free, plain-HTTP fit - no MCP connector, no
   paid feed. Query: S-1/S-1-A filings mentioning "phase 3", restricted
   client-side to pharma SIC codes (2834 Pharmaceutical Preparations, 2836
   Biological Products, 8731 Commercial Physical & Biological Research) -
   the API's `q` parameter doesn't support SIC filtering directly.

   **Real finding: this signal needs no separate mega-pharma exclusion**,
   unlike signals 1-2 - S-1 is specifically the form for a company's OWN
   initial public offering (or an amendment to one already in progress);
   an already-public mega-pharma company has no reason to file one for
   itself, so `company_filters` isn't applied here. One signal per company
   (deduped by CIK, keeping the most recent filing - the same company
   often files an S-1 then one or more S-1/A amendments).

   Real query (2026-01-01 to 2026-07-30): 87 raw filings, 37 distinct
   companies after dedup - genuine, current pre-IPO/recently-public
   biotech names (e.g. Attovia Therapeutics, Kailera Therapeutics,
   Braveheart Bio). **Coverage note:** EDGAR returns up to 100 hits per
   request; one page is fetched, so a query matching more than ~100 total
   only sees that page, same disclosed-not-hidden limitation as signal 2's
   recency window. Regression-tested (company-name cleanup including the
   no-ticker-yet case for pre-IPO companies, per-company dedup keeping the
   latest filing) with synthetic data.

   **All 4 target-account signals are now built** (build steps 3-4-5-7
   of Prospector's sequencing).

**Qualification rule (v0, expected to be revised once tested):** 1
DISTINCT signal type = `watching`, 2+ distinct signal types = `qualified`
(clarified during build - two trials of the *same* type is still one kind
of evidence, not stronger corroboration). Deliberately simple to start,
same spirit as the original job-priority weighting (§9) and compatibility
scoring (§9/79fd4c3) — both started crude and were tuned against real
results rather than designed perfectly upfront. Manual statuses
(`contacted`/`stale`/`disqualified`) are sticky - a new signal arriving
later never silently overwrites a status Zahir set himself.

**Built 2026-07-30 (signal 1 of 4, `src/prospector/target_accounts.py` +
`clinical_trials.py` + `company_filters.py`, new "Target accounts" section
on the Prospector tab):** storage module with add_signal()/set_status(),
a ClinicalTrials.gov normalizer, and a shared company-quality filter
module (built shared on purpose - signals 2/3 will need the same company
filtering, not a copy per source).

**Real finding that changed the plan:** `search_trials` requires at least
one of condition/intervention/location/sponsor - there's no "browse
everything" mode. A location="United States" + phase=PHASE3 +
status=[ACTIVE_NOT_RECRUITING, COMPLETED] query matched **13,659** trials,
dominated by mega-pharma (Sanofi, Novartis, GSK, AstraZeneca, Amgen,
Biogen...) and non-company sponsors (universities, hospitals, government,
individual physicians) - ClinicalTrials.gov has no company-size/commercial-
maturity field, so phase/status alone can't isolate "approaching first
launch" from "established multinational's routine pipeline." Applied two
filters in `company_filters.py`: exclude obvious non-companies (keyword
match), and exclude ~20 universally-recognized mega-pharma majors (common
knowledge, not a fragile guess - same spirit as the CISO-disqualification
rule Zahir gave elsewhere). From a real 30-trial sample, 12 survived;
populated as genuine first data (not test fixtures).

**Zahir caught a real gap live 2026-07-30:** "Forest Laboratories" (acquired
by Actavis in 2014) survived the filter, since being a "company" and not
being mega-pharma doesn't mean still independent. Added a third filter,
`KNOWN_ACQUIRED_KEYWORDS`, for well-known formerly-independent companies
absorbed via M&A - also caught "C. R. Bard" (acquired by Becton Dickinson,
2017), which had been flagged as a judgment-call uncertainty during the
original build. Both removed from the real data via filter-and-resave.
**Explicitly not exhaustive** (manually curated from general knowledge, no
live-verified source, won't catch acquisitions after this knowledge's
cutoff or smaller/obscure deals) - same "review and disqualify" safety net
as everything else in target_accounts is the real correction mechanism,
not a claim this list is complete. Two other imperfect survivors are
already known and left as-is on purpose: "Gustave Roussy, Cancer Campus,
Grand Paris" and "Radiation Therapy Oncology Group" are research
institutions the keyword list didn't catch (no "university"/"hospital" in
the name) - a separate, not-yet-addressed gap, not conflated with the
M&A one.

Regression-tested (filter heuristics, signal normalization, dedup-by-ref,
qualification-by-distinct-type, sticky manual status) with synthetic data,
cleaned up via filter-and-resave. Real target_accounts.json now holds 10
companies, all `watching` (single signal type each, as expected this
early). Verified live in the running Streamlit session - Target accounts
table renders with no console errors.

**Real gap found live 2026-08-09 (Zahir recomputed the Prospector Score,
which happened to mention it in that run's "how to raise it" text - not
a reliable mechanism, see below):** sticky manual statuses (above) mean
`disqualified` never silently changes on its own, but sticky isn't the
same as invisible - three real accounts (UCB Pharma, BAUSCH, BeOne
Medicines USA) were disqualified for good reasons at the time (a stale
trial, an already-approved-drug signal), then each later picked up a real
job posting - BeOne's is now an application under review - with nothing
ever surfacing that mismatch back to Zahir. Fixed with
`target_accounts.find_disqualified_with_new_activity(jobs, applications)`
(renamed 2026-08-10, see below):
a deterministic, zero-cost cross-reference (same normalize-and-substring
match as `commercial_hiring.py`/`connections.py`) that flags any
disqualified account with a real posting that wasn't part of the evidence
that originally disqualified it - doesn't change status (manual stays
manual), just surfaces it. Rendered as an always-visible warning box on
the Prospector tab, above the Target accounts table, deliberately not
gated behind "Compute Prospector Score" - a hard fact like this shouldn't
depend on the LLM's reasoning happening to mention it that particular run
(the same "AI output checked by a literal rule needs a code-level
backstop" lesson as elsewhere in this project, applied to AI *surfacing*
a fact rather than checking one). **The subtlety that made this non-trivial:**
a naive company-name match alone re-flags a company forever on the exact
posting that got it disqualified in the first place - found live via a
real false positive ("Securitas", disqualified as a wrong-industry title-
keyword coincidence, see commercial_hiring.py) before the fix excluded it.
The real fix: only count a matching job as new evidence if its ref isn't
already one of the account's own existing signal refs. Regression-tested
(synthetic: new-evidence flagged, already-known-ref excluded, non-
disqualified accounts excluded, empty input) and against real data (all 3
real examples flagged correctly including BeOne's "under review"
application; Securitas correctly excluded). Live-verified in an isolated
Streamlit instance (port 8505) - warning box renders with the exact
expected content, stopped cleanly after.

**Hardened 2026-08-10 (Zahir's whole-codebase adversarial self-audit
request, items #10/#15/#16/#20):** four real gaps in the fix above, found
by re-examining it under the same "what's the sibling scenario" lens that
built it in the first place.
- **#20 - added `status_updated_at`** to every target account, bumped on
  any real status change (manual `set_status()` or automatic
  qualified/watching recomputation), same rule as `applications.py`'s
  `status_updated_at`. Prerequisite for the two gaps below - without a
  "when did Zahir make this call" timestamp, there's no way to tell
  evidence that existed *before* a status decision from evidence that
  arrived genuinely *after* it. Records whose status hasn't changed since
  before this field existed don't have it - excluded from the two checks
  below, not backfilled or guessed at, same convention as every other
  "added on date X" field in this codebase.
- **#15 - new signals on an already-paused account.** The original fix
  only checked for a real job posting appearing later - a signal added
  via `add_signal()` directly to an already-disqualified/stale account
  (e.g. a fresh ClinicalTrials.gov trial found by a live Claude Code
  session) was silently absorbed into `signals` with nothing surfaced,
  even though it's the exact same "new evidence, sticky status" shape.
  Now compares each signal's `date_observed` against `status_updated_at`.
- **#16 - "stale" had zero coverage.** The original fix checked
  `status == "disqualified"` only; "stale" accounts (also a manual,
  sticky, "stopped watching this" status) got nothing. 0 stale accounts
  exist in real data today so this was unreproduced live, but zero
  protection the moment Zahir marks anything stale. `"contacted"` is
  deliberately still excluded - that status means active engagement, not
  a pause, a genuinely different situation.
- **#10 - real O(paused accounts x jobs) performance cost**, ~675ms
  unconditionally on every single Prospector tab render (Streamlit reruns
  the whole script on any widget interaction anywhere in the app, not
  just Prospector-tab ones) - projected ~1.3s at 5K jobs, ~2.6s at 10K,
  reachable within 2-3 months at real ~1200 jobs/week growth. Fixed two
  ways: (1) each job's normalized organization name is computed ONCE up
  front instead of being re-normalized per paused account (the actual
  dominant cost, not the substring check itself), (2) the UI call site
  wraps the whole function in `st.cache_data` so a rerun that doesn't
  change `target_accounts`/`jobs`/`applications` is a cache hit instead
  of a full recompute. Required changing the function's own signature to
  accept `target_accounts` as a real parameter instead of self-loading it
  internally - the self-load was fine before caching existed, but would
  have made `st.cache_data` silently ignore target_accounts changes
  (stale cache hits on a status Zahir had literally just changed) had it
  stayed hidden inside the function body.

Renamed `find_disqualified_with_new_activity` -> `find_paused_accounts_with_
new_activity` to match the now-broader disqualified+stale scope (new
`PAUSED_STATUSES` constant). Regression-tested: lock-verification tests
carried over unchanged, new tests for the timestamp bump (manual and
automatic transitions), the new-signal-since-status-change check
(including the "no status_updated_at yet" gap case), the stale-status
case, and a real perf measurement against production-scale synthetic data
confirming the precompute-once change actually reduces wall-clock time.

### 16b. Outreach (new funnel stage)

New data model, `outreach`, linked to *either* a `job_id` or a
`target_account_id` (at least one required, not both necessarily) — outreach
can happen against a posted job (e.g. contacting the hiring manager) or a
target account with no posting yet at all.

Fields (draft): `contact_name`, `contact_title`, `channel` (email / LinkedIn
/ phone / in-person), `status` (drafted / sent / responded / no-response),
`sent_date`, `content` (the drafted text), `strategy_tag` (§16d).

**Mechanism:** for email outreach specifically, reuse the Gmail-draft
mechanism already built for CTA fulfillment (§14, `create_draft` via the
Gmail connector) rather than building a second drafting pathway — same
"prepare but don't submit" rule, same connector, different trigger. For
LinkedIn/phone/in-person, Panga logs the outreach record (so it counts
toward KPIs/learning) but can't draft or send on those channels itself.

**Contact sourcing — LinkedIn connections (added 2026-07-30, Zahir's
suggestion):** the open question in the above design was *who* to log
outreach against in the first place. Answer: cross-reference Zahir's
existing LinkedIn network. LinkedIn has no API and blocks scraping/login
automation, so this uses the same manual-export pattern already proven for
his profile PDF and job postings (§13) — Settings → Data Privacy → "Get a
copy of your data" → Connections, which produces a CSV (name, company,
position, connected-on date). Zahir uploads it once (and refreshes
periodically) via the existing LinkedIn page in `src/ui/app.py`, alongside
the profile-PDF uploader. New sibling module to `src/linkedin/ingest.py`
parses the CSV and stores connection rows through `security.crypto_store`
like every other store (§7/§15) — this is PII about other people, not just
Zahir.

Two things get flagged automatically, not hand-searched by Zahir:
1. **Recruiter-titled connections** — keyword match on the position column
   (recruiter, talent acquisition, executive search, headhunter, etc.).
2. **Connections at a `target_account`** — company name cross-referenced
   against §16a's list, case-insensitive/fuzzy match.

Either flag surfaces the connection as a **suggested contact** when Zahir
starts logging outreach against a job or target account, pre-filling
`contact_name`/`contact_title` instead of him having to remember who he
knows there. This is a sourcing/suggestion layer only — it doesn't change
the "Claude drafts, Zahir sends" rule above.

**Built 2026-07-30 (build step 6 of 8):**

- `src/prospector/outreach.py` — storage for the `outreach` table.
  Extended this spec's original 4-value status sketch with a `planned`
  state before any draft exists (same kind of small, documented lifecycle
  extension applications.py's real status set went through). Email
  drafting reuses `panga-cta-fulfillment` (§14) rather than a second
  pathway: `request_draft()` flags a record, the scheduled task's new
  STEP 2C composes and creates a real Gmail draft via `create_draft` (no
  `replyToMessageId` - this is cold outreach, not a reply), STEP 3B
  reconciles once Zahir sends it, same pattern as CTA replies exactly.
- `render_outreach_section()` in `src/ui/app.py` - one shared UI function
  called from both the target-account detail panel and a job's detail
  panel (outreach anchors to either), rather than duplicating the UI
  twice.
- `src/linkedin/connections.py` + `connections_store.py` - CSV parser
  (finds the real header row rather than assuming a fixed line, since
  LinkedIn's export has a "Notes:" preamble first) + recruiter-keyword
  flagging + target-account cross-referencing. New "Connections" section
  on the LinkedIn tab, alongside the existing profile-PDF uploader.
  **Real bug caught by testing, not hypothetical:** a naive substring
  match failed to cross-reference "Napo Pharmaceuticals, Inc." (openFDA-
  style, from §16a) against "Napo Pharmaceuticals Inc" (how it might
  read in a LinkedIn connection's company field) - the comma+period broke
  simple substring containment. Fixed with light punctuation
  normalization before comparing. **NOT yet tested against a real
  export** - Zahir hasn't provided one; built against the well-documented
  real CSV shape, worth a quick check the first time he uploads one.

Regression-tested (outreach status/draft lifecycle including sticky
timestamps, CSV parsing including the preamble/blank-row cases, recruiter
keyword matching, company cross-referencing) with synthetic data.

**Operational finding, not a code bug:** verifying this live, the
already-running shared Streamlit dev server (owned by a different
concurrent chat session, port 8501) threw a `UnicodeDecodeError` opening
the LinkedIn tab - its traceback pointed at line numbers in
`linkedin/storage.py` that don't match the file's actual current content.
Diagnosis: that server process has been running since before some earlier
change (most likely encryption-at-rest, §15) and Python's module cache
never picked up the update - a **stale long-running process**, not broken
code. Confirmed by starting an isolated instance on a different port
(8502, stopped after verification) - loads perfectly, no errors. **If a
future session sees a traceback that doesn't match a file's real content,
suspect a stale cached module in whichever session owns the long-running
port-8501 server before assuming the code is broken** - the fix is
restarting that process (e.g. Zahir relaunching via the desktop shortcut),
not editing already-correct code.

**Hardened 2026-08-10 (Zahir's adversarial self-audit request, #21):**
the "Log new outreach" form had no dedup check and didn't clear itself
after a successful submit - the same filled-in values just sat there, so
an accidental double-click, or a confused re-click on a form that still
visibly showed the same values, created a duplicate outreach record.
Zero real outreach records existed at the time this was found, but a real
risk once Zahir starts logging real outreach. Fixed two ways: (1) every
form field key now carries a "generation" suffix that bumps on a
successful submit, forcing Streamlit to instantiate fresh, empty widgets
next render (can't clear a widget's own session_state key in place after
it's already been instantiated the same run - this sidesteps that
restriction entirely); (2) `_is_likely_duplicate_outreach_submit()`
skips creating a second record only when the same contact/channel/notes
was logged within the last 10 seconds - deliberately narrow, so a genuine
follow-up to the same contact next week is never silently blocked, only
the same-moment accident is. Tested via `streamlit.testing.v1.AppTest`
driving the real form end to end: a normal submit creates one record and
visibly clears the field; an immediate resubmit of the same values
creates nothing extra; a genuinely different contact submitted right
after still creates its own record (the guard doesn't overreach).

### 16c. KPI Layer + Rejection-Pattern Diagnosis (Outcome stage)

Both read existing data — no new store beyond §16a/§16b's additions.

**KPI dashboard:** computed, not stored — Coverage (jobs found, target
accounts identified, per week/channel), Activity (applications submitted,
outreach sent, per week), Outcome (response rate, interview rate, offer
rate, rejection rate — sliceable by channel/role/score-band). **Dependency
resolved 2026-07-30:** these Outcome rates need `applications.status` to
actually distinguish interview/offer/rejected, not just applied — see the
"Application status lifecycle extension" backlog row (§13), built the same
day this was identified as a blocker for building the dashboard itself.

**Built 2026-07-30 (v1 — jobs/applications only, `src/prospector/kpis.py`
+ new "Prospector" tab in `src/ui/app.py`):** Coverage (total jobs +
per-channel breakdown) and Activity (total applications + by-status
breakdown) sections, plus Outcome rates (response/interview/offer/rejection,
overall and sliced by channel, fit-score band, and target-role priority
weight via the existing `weight_for()` lookup). Target-accounts and
outreach counts aren't in the dashboard yet since those tables don't exist
until §16a/§16b are built — the tab says so explicitly rather than showing
silent zeros with no explanation.

Two new fields needed first, since neither existed anywhere in the
codebase: `jobs.date_added` (stamped once, in `job_store.save_jobs()`, only
for genuinely new records) and `applications.created_at`/
`status_updated_at` (stamped in `applications.py`'s `upsert_application()`/
`confirm_status_suggestion()` — `status_updated_at` only bumps on an actual
status change, not on every call, since e.g. "Request documents" re-sends
the current status every time). Records that predate 2026-07-30 have
neither field — counted in totals, excluded from any "last 7 days"/trend
number, and the dashboard says so rather than silently showing 0. Verified
via a regression script (synthetic job + application records exercised
through both timestamp behaviors and all three KPI functions, cleaned up
via filter-and-resave afterward — real data confirmed unchanged before and
after) and live against the already-running Streamlit session (hot-reload,
no console errors, real numbers: 620 jobs, 2 applications, 1 "applied").

**Rejection-pattern diagnosis:** an on-demand pass (a button, not a
scheduled task — this needs Zahir to actually read and react to it, unlike
the daily search task) over rejected/no-response applications, looking for
clustering by score band, channel, role type, or recorded skip-reasons.
This is a direct extension of the already-built "non-applied-job feedback
loop" (§13) — same live-reasoning mechanism, wider input (rejections too,
not just self-marked "not interested"), and it produces a plain-language
write-up + suggested adjustments rather than a raw count.

**Built 2026-07-30 (build step 2, data-gathering half only —
`src/prospector/rejection_diagnosis.py`):** `gather_diagnosis_input()`
assembles rejected + not-interested-with-reason applications, each
enriched with its job's title/organization/channel/score/rationale, and
deliberately makes no "is this a real pattern" judgment itself — that stays
Claude's live-reasoning call, per the module's own docstring. The
Prospector tab's "Prepare diagnosis data" button can't run the actual
analysis (Streamlit has no path to a live Claude conversation, same
constraint as "Start tailoring"), so it gathers and displays the data, then
says to ask Claude Code to read it. Regression-tested read-only against
real data (correctly found the one existing "Assistant Chief Information
Officer — not senior enough" skip-reason) and against a synthetic rejected
record for the join/enrichment logic, cleaned up via filter-and-resave.
First live run (2026-07-30): 0 rejected, 1 not-interested-with-reason — too
little to call a pattern, consistent with a known scoring-search quirk
(§16c's own build-history: USAJOBS keyword search sometimes surfaces
sub-target-level titles) rather than a new concern. Will get more useful as
real rejections accumulate via the CTA-to-status wiring above.

**What changed 2026-07-30:** this diagnosis pass is now one input into the
cross-cutting **Learn Engine (§17)** rather than a standalone analysis —
its output (patterns among applications) gets joined with patterns from
target accounts, outreach, search cadence, LinkedIn, and interview prep,
instead of living in isolation. The KPI dashboard above is unaffected —
it's a straightforward display of existing numbers, not an analysis pass.

### 16d. Learning Loop (Learn stage) — generalized into §17

`strategy_tags` — short tags attached to an application or outreach record
at drafting time (e.g. `concise-1-page`, `leadership-narrative-focus`,
`warm-intro-mentioned`). No upfront taxonomy needed: Claude suggests a tag
based on what's actually different about *this* draft versus past ones,
Zahir confirms or edits it — same confirm-don't-guess pattern as the
gap-probing interview (§3) and skip-reason review (§13). This tagging
mechanism stays local to applications/outreach as described here.

**What changed 2026-07-30:** Zahir asked for the *analysis* side of Learn
(not this tagging step) to stop being Prospector-only and become an
overarching engine reading across all of Panga, not just
applications/outreach outcomes. §16c's rejection diagnosis and this
section's strategy-tag correlation are both absorbed into that single
cross-cutting mechanism — see **§17, Learn Engine** — rather than being
three separate analysis passes bolted onto different tables.

**Tagging built 2026-07-30 (build step 8 of 8):** `applications.set_strategy_tag()`
and `outreach.set_strategy_tag()` (the field was already reserved on every
outreach record from §16b's build), plus a small text input near each
record's status controls in `src/ui/app.py` — no suggestion engine wired
up yet (that's Claude's live-reasoning call per a conversation, not a
hardcoded UI prompt), just the mechanical set/store/display half, same
split as everywhere else.

### UI placement

New **"Prospector" tab** in the Streamlit sidebar, alongside Results / Call
to Action / Settings — target accounts list, the outreach log, and the KPI
dashboard all live there. Mirrors how Call to Action (§14) got its own tab
rather than being folded into Results; the same reasoning applies here —
this is a genuinely different mode of work (proactive targeting, not
reacting to a posted job), not a variation on the existing Results screen.

### Build sequencing (completed 2026-07-30, all 8 steps, same day as design)

Ordered by dependency + fastest-to-value, not by the order listed above -
executed in this exact order, same day:

1. KPI dashboard (§16c) — 100% existing data, no new integration.
2. Rejection-pattern diagnosis (§16c) — same.
3. `target_accounts` + late-stage-trial signal (§16a.1) — connector already
   available.
4. `target_accounts` + regulatory-filing signal (§16a.2) — API already
   integrated elsewhere.
5. `target_accounts` + commercial-hiring signal (§16a.3) — reuses existing
   job data, no new source.
6. Outreach logging + drafting (§16b), including the LinkedIn-connections
   contact-sourcing layer — needs `jobs`/`target_accounts` to link against,
   so it naturally follows them.
7. `target_accounts` + funding/IPO signal (§16a.4) — needs source research
   first (see above).
8. Strategy tagging (§16d) + the cross-cutting **Learn Engine (§17)** —
   needs outreach + applications records to tag, and benefits from every
   other subsystem above having real usage history, so it's last by
   construction.

This surfaces value early (KPIs and diagnosis on data already in hand)
while working toward the flagship proactive-targeting piece that motivated
the Prospector name in the first place.

