# Panga — Functional Requirements Specification (FRS)

Split out of `docs/job-search-automation-prd.md` on 2026-08-11 (Zahir's
request, via Panga-Documentor) to match the BRD+FRS structure used
colony-wide (see `C2/docs/frs.md`). This is the current-state functional/
technical spec — what the system does and how it's structured. Business
decisions live in `docs/business-requirements-document.md` (BRD);
build-progress/backlog tracking lives in `docs/backlog-log.md`.

**Section numbers below are preserved unchanged from the original PRD** —
any existing `§N` reference elsewhere in the codebase/docs still resolves
correctly to a section in this file, with three exceptions: §5 moved into
the BRD (it's a business/roadmap commitment, not a functional spec), and
§13/§16/§17 moved into `docs/backlog-log.md` / `docs/prospector-spec.md` /
`docs/learn-engine-spec.md` respectively (see those files).

---
## 1. Problem Statement
Manual job searching, resume tailoring, and applying is slow and error-prone. The goal is a personal tool that:
- Understands the user's real experience deeply (deeper than what's on the resume)
- Finds relevant jobs across standard boards *and* targeted industry sources
- Tailors the resume/cover letter per job description, truthfully
- Prepares (but does not auto-submit) applications for the user to review and send

Long-term, this becomes a packaged Windows app (later Mac), but the immediate goal is a working personal tool.

## 2. Scope for MVP

**In scope:**
- Resume ingestion (parse existing resume)
- Job aspiration intake (target roles, industries, seniority, etc.)
- Adaptive onboarding interview to build a "master profile" richer than the resume
- Industry/role → skill lookup table (see §4) used to drive gap-probing questions
- Job search across standard boards + targeted industry sources (initial: lifesciences/pharma, phase 2 clinical companies, sourced via FDA + PubMed data, expandable to full lifesciences/pharma universe)
- Per-job resume/cover letter tailoring based on the master profile + JD
- Cross-industry fit check (flag when the user's experience transfers to adjacent industries/roles)
- Output: a ready-to-submit, tailored application package per job

**Explicitly out of scope for MVP:**
- Auto-submission of applications (user submits manually — avoids ToS violations, bot detection, account bans on ATS platforms like Workday/Greenhouse/LinkedIn Easy Apply)
- Native Windows/Mac packaging (comes after the workflow is proven)

## 3. Core Workflow

1. **Onboarding**: User provides base resume + career aspirations (target roles, industries, locations, comp, etc.)
2. **Gap-Probing Interview**: System looks up the relevant industry/role skill profile (§4), joins it against the parsed resume, and asks targeted questions about anything missing or ambiguous (e.g., "Your title is Head of IT in pharma — have you implemented/maintained DocuSign under CFR 21 Part 11 / GxP validation? What SSO tooling have you used?")
3. **Master Profile Build**: Answers are captured with full nuance (not just yes/no — e.g., "implemented AND maintained validated state given monthly DocuSign releases" or "moved from Okta to MS Authenticator to reduce licensing cost") and stored permanently against the user's profile.
4. **Job Discovery**: Searches standard job boards + industry-specific sources (initially lifesciences/pharma via FDA/PubMed-derived company lists; extensible to other industries e.g. aerospace, defense for other users) + **USAJOBS.gov** (U.S. federal government positions — has a public API, so integrates more cleanly than boards requiring scraping).
5. **Fit + Tailoring**: For each job found, the system checks fit (including cross-industry transferability) and generates a tailored resume/cover letter drawing only from verified master-profile content — no invented experience. **Fit scoring built 2026-07-29**: a 0-100 compatibility score + plain-language rationale per job, computed by Claude reasoning against the master profile (not a keyword heuristic — see docs/daily-job-search-task.md), run once daily via a scheduled task so new jobs are pre-validated before the user sees them. The Results screen sorts by this score and defaults to hiding anything below 30.
6. **Review Queue**: Tailored packages are queued for the user to review and manually submit.

## 4. Data Model Direction (relational)

This needs to be **industry-agnostic and ever-growing**, not hardcoded to pharma:

- **`industries`** — e.g. Lifesciences/Pharma, Aerospace, Defense
- **`roles`** — e.g. Head of IT, Clinical Data Manager (scoped to an industry, since the same title means different things across industries)
- **`role_skills`** — lookup table of keywords/skills/certifications expected for a given role+industry combo (e.g. CFR 21 Part 11 validation, GxP, SSO/Okta/MS Authenticator). Populated via web search / LLM reasoning and **grows over time** as new roles/industries are encountered.
- **`user_profile`** — parsed resume + all confirmed gap-interview answers (the master profile)
- **`user_profile_skills`** — join of user_profile to role_skills, with the *detailed* qualifying answer attached (not just a checkbox)
- **`jobs`** — scraped/sourced listings (board + company-site origin tagged)
- **`applications`** — tailored package per job + status. Shipped status values (`src/tailoring/applications.py`): "under review", "applied", "interview scheduled", "offer", "rejected" (last three added 2026-07-30, see §16c), "not interested", "save for later" + optional skip-reason text when status is not-interested (feeds §13 feedback loop) + `strategy_tag` (§16d/§17, added 2026-07-30)
- **`target_accounts`** — companies worth watching before they've posted a role (Prospector §16a, built 2026-07-30). `src/prospector/target_accounts.py`: company_name, industry, status (watching/qualified/contacted/stale/disqualified), signals (list of `{signal_type, source, detail, date_observed, ref}`), notes
- **`outreach`** — direct contact logged against a job or a target account (Prospector §16b, built 2026-07-30). `src/prospector/outreach.py`: contact_name/title/email, channel, status (planned/drafted/sent/responded/no_response), strategy_tag, plus the Gmail-draft request/reconciliation fields shared with the CTA fulfillment mechanism (§14)

Mechanism for populating `role_skills`: reasoned out (via search/LLM) at the time a new role/industry is first encountered, then persisted — so the system doesn't re-derive it from scratch every time, but keeps refining as more users/roles are added.

## 6. User Persona (from base documents)
Zahir Uddin — CIO / Head of IT, 25+ years, primary depth in **commercial-stage life sciences/pharma** (GxP, CSV, 21 CFR Part 11, SOX ITGC, MDM, enterprise applications, cybersecurity, AI-enabled analytics), most recently Head of IT/CIO at SK Life Science (through Jan 2026). Also has real cross-industry range: AbbVie, Eisai (life sciences), TD Bank, Great American Financial (financial services), Univision (media), EMC/BP/Ethicon-J&J/The Hartford (EDW/analytics across tech, energy, healthcare, insurance).

Important build constraint: **not a hands-on developer** (last coded in 2006; ran API calls at Reltio without deep understanding). This means:
- The v0 interface must be something he can actually use without a terminal — a simple UI, not a CLI he has to operate.
- Anything "self-serve" (like the encrypted-file recovery flow below) needs to be genuinely foolproof, since he can't debug it himself.
- Currently jobless since Jan 2026 — the MVP priority is speed to a usable job-hunting workflow, not architectural elegance.

## 7. Data Security / Storage
- Resume/PII master profile stored in an **encrypted file, local-only, on the user's machine** (no external/support-side copy for now).
- **Built (2026-07-30)**: all of `data/` (master profile, raw resume text, interview answers, job history, applications, CTA emails) is encrypted at rest — AES-256-GCM via `src/security/crypto_store.py`. Full detail in `docs/encryption-at-rest.md`; summary below.
- **Key model, revised from the original "user passphrase" plan**: the original spec below (a typed passphrase, "the only key for now") turned out to be incompatible with the scheduled tasks built after this section was written (§14, §13's daily search task) — those run unattended, several times a day, with no one present to type anything. Instead, the AES key is a random 256-bit value generated on first use and held in the OS credential store via the `keyring` library (Windows Credential Manager/DPAPI on this machine; Keychain if this ever runs on a Mac) — it unlocks automatically for this Windows login, for both the interactive Streamlit app and unattended scheduled tasks. This protects `data/` if the files are copied off this machine or the disk is stolen/imaged; it does **not** protect against someone else using this same Windows login — narrower than a passphrase would be, but a passphrase model isn't actually usable given the automation already built.
- **Original plan (superseded by the above):** user sets a passphrase correctly at first setup; this is the only key. Kept here for history — no passphrase exists in the shipped design.
- **Built (2026-07-30)**: recovery for the risk above — a one-time-generated recovery code (Settings page, "Data Recovery" section) unwraps a saved copy of the same key if this Windows account's credential store ever loses it. Full detail in `docs/encryption-at-rest.md` §"Recovery"; summary below.
- **Backlog**: since there's no passphrase in the current design, "passphrase recovery" as originally scoped no longer applies — see §13, now marked built under its re-scoped name.

## 8. Job Search Cadence
- Target: **2–3 scheduled runs per day**, plus an **on-demand "run now" button**.
- Frequency should ultimately be **self-adjusting**: track runtime metrics (new-listings-found-per-run, cost-per-run) over time, and tune the schedule to balance freshness against query cost automatically rather than using a fixed cadence forever.

## 9. V0 Interface (given non-developer user, urgent job search)
Not a script — a simple results-driven interface (built with Streamlit, opens in the user's normal web browser, no terminal use required):
1. User sets **target roles** with a **numeric priority weight** (e.g. CIO=10, SVP=9, VP=9, Director=6). This weight is a **sort order only** for v0 — it determines the order jobs are displayed in (best-fit/highest-priority roles surfaced first), not how hard or how often the system searches for each.
2. System runs search (scheduled + on-demand) and shows a **prioritized results list** of matching jobs, sorted by role priority weight.
3. User picks a job from the list; its JD is pulled in.
4. System (Claude) uses the JD + master profile to help build/tailor the resume and supporting documents for that specific application — this is a guided, back-and-forth step, not fully automated.
5. User manually applies (per §2/§3 — no auto-submit).

**Per-job actions on the results screen (confirmed 2026-07-27):**
- Open the original job posting (link out to source site)
- Start tailoring for that job (kicks off step 4 above)
- Mark status: applied / not interested / save for later (feeds the `applications` table, §4)

## 11. LLM Architecture
- **v0: paid-only, Claude via Claude Code.** No local LLM. Rationale: at this usage scale (single user, 2-3 scheduled runs/day + on-demand), token cost is not the likely bottleneck — search/scraping calls are. A local LLM stack (e.g. Ollama) adds a second system to install/maintain, which is a poor tradeoff for a non-developer user.
- **Backlog: hybrid local (free) + paid model split.** Revisit only if/when this scales toward a funded, multi-user product — split lightweight mechanical work (dedup, basic filtering) to a local model, keep quality-sensitive work (tailoring, gap-probing, fit scoring) on the paid model.

## 12. Multi-User Scope
- **v0 is single-user**, designed for the current user only (local-only encrypted storage, per §7).
- Multi-user support is a **later-stage aspiration**, contingent on validating the concept (e.g. VC backing) — not a near-term build target. Architecture should avoid unnecessary lock-in to single-user assumptions where it's free to do so, but should not be over-engineered for scale it doesn't need yet.

## 14. Gmail Call-to-Action Handling (built 2026-07-28 through 2026-07-29)

Full technical detail lives in `docs/email-monitoring-task.md`; this section
records the product decision and shape for the working spec.

**Problem:** email is where recruiters/ATS systems actually reach Zahir —
interview invites, offers, rejections, take-home assessments, direct
questions — mixed into his normal personal inbox (not a dedicated job
mailbox). Missing or forgetting to act on one of these costs more than a
missed job listing. §9's results-driven UI doesn't see any of this unless
it's explicitly wired in.

**Shape, in three layers:**
1. **Scan** (`panga-gmail-cta-scan`, 3x/day — 8am/12pm/4pm) — reads Zahir's inbox, classifies
   each new thread, and applies Gmail labels for state (no database). Also
   tries to auto-match "application received" confirmations to a specific
   `applications` row (§4) via `applications.suggest_status()`, so Zahir
   doesn't have to remember to mark a job "applied" himself — he still
   confirms every suggestion.
2. **Dashboard mirror** — call-to-action emails are written to a local JSON
   store (`src/tailoring/cta_emails.py`) and shown on a dedicated **Call to
   Action** page in the Streamlit UI (§9), grouped by category, so Zahir has
   one place to work through everything instead of hunting through his inbox
   for a `Panga/Call-to-Action` label.
3. **Fulfillment loop** (`panga-cta-fulfillment`, 2x/day — 8am/4pm, throttled
   down from every 10 minutes 2026-08-05 for cost reasons — a manual "Send
   and receive" button on the dashboard covers the gap in between) —
   executes what Zahir clicks on that page. **Dismiss** archives the email
   in Gmail and labels it `Panga/Handled`. **Draft reply** has Claude
   compose a real Gmail draft tailored to the email (category + content),
   created via the Gmail connector but **never auto-sent** — Zahir reviews
   and sends it himself, same "prepare but don't submit" principle as §2's
   application
   packages. The loop also runs in reverse: once Zahir sends a draft it
   created, it notices (by checking Gmail's live Drafts list) and clears that
   item from the dashboard automatically, so he never has to remember to come
   back and mark it done.

**Deliberate constraint carried over from §2/§3:** Panga never sends anything
without a human in the loop. The scan task only reads and labels; drafting
only happens because Zahir explicitly clicked a button, and even then a
draft just waits in Gmail until he sends it himself.

**Known limits:** both tasks require the Claude app to be open to run. The
dashboard has no live Gmail access of its own (Streamlit can't reach MCP
connectors) — it only ever reflects what the fulfillment task last wrote, so
there's up to a ~10-minute lag between clicking a button and seeing it
resolved, never truly instant.

## 15. Encryption at Rest (built 2026-07-30)

Full technical detail lives in `docs/encryption-at-rest.md`; this section
records the product decision and shape for the working spec, and supersedes
§7's original passphrase plan.

**Problem:** §7 originally called for a user-typed passphrase as "the only
key." By the time this was actually built, that plan conflicted with
automation added afterward (§13's daily search task, §14's Gmail tasks) —
those run unattended, several times a day, with no one present to type a
passphrase. A pure passphrase model would have broken every scheduled run.

**What shipped instead:** all of `data/` (master profile, raw resume text,
interview answers, jobs, applications, CTA emails) is encrypted with
AES-256-GCM. The key is a random 256-bit value generated once and stored in
this Windows account's credential store via the `keyring` library — no
passphrase, unlocks automatically for both the Streamlit app and the
scheduled tasks. `src/security/crypto_store.py` is the single point that
does this; the existing store modules (`profile/storage.py`,
`search/job_store.py`, `tailoring/applications.py`,
`tailoring/cta_emails.py`) call it instead of raw file I/O, so nothing
about their own interface changed. Existing plaintext data was migrated in
place via `scripts/encrypt_existing_data.py` (idempotent — safe to re-run,
skips files already encrypted).

**Threat model, stated plainly:** this protects `data/` if the files are
copied off this machine, or the disk is stolen/imaged. It does **not**
protect against someone else using this same Windows login — that's a
narrower guarantee than a passphrase would give, accepted deliberately
because a passphrase isn't compatible with unattended automation.

**Cross-platform note:** `keyring` was chosen over calling Windows'
DPAPI directly because it also targets macOS Keychain (and Linux Secret
Service) with the same code path — relevant given §5's Mac-port plan. It
does *not* by itself let the same encrypted `data/` directory be read on
both a Windows machine and a Mac at once (each OS's credential store holds
its own copy of the key) — that's a multi-machine sync problem, out of
scope while §12 keeps this single-machine/single-user.

**Backlog impact:** §7's "passphrase recovery" backlog item no longer
applies as originally scoped, since there's no passphrase to recover — see
§13's re-scoped "Windows-account-loss recovery" item, now also built (see
below).

### 15a. Recovery (built 2026-07-30)

Envelope encryption on top of the same key, not a second copy of the
encrypted files: a recovery code (20 random bytes, shown once as a
hyphenated base32 string) wraps the existing data-encryption key via
PBKDF2 + AES-GCM, saved to `data/security/recovery_envelope.json`. Two
pieces:

1. **Generate** — Settings page, "Data Recovery" section. Shows the code
   exactly once with a "save it somewhere other than this computer"
   warning; nothing about the plain code is ever written to disk.
2. **Recover** — `scripts/recover_access.py` (double-click
   `recover_access.vbs`, no terminal, same pattern as the desktop
   shortcut), a small tkinter prompt for the code that reinstalls the
   unwrapped key into this account's credential store. Deliberately
   separate from the Streamlit app, since the app needs the key just to
   load its own pages.

Also closed a related gap while building this: previously, a missing
credential-store key silently produced a *new* random key rather than
signaling a problem. Now, a missing key is checked against whether a
recovery envelope already exists — if one does, that's proof a real key
was set up before, so it fails loudly and points at
`scripts/recover_access.py` instead of minting a key that could never
decrypt the existing files.

**Not covered:** losing the entire disk/machine, not just the Windows
account/profile — there's nothing to recover from that regardless. See
`docs/encryption-at-rest.md` for full detail including how this was
verified.

## 18. Applications Pivot Table (Designed 2026-07-30, not yet built)

**Status: not yet built.** Tracked as a backlog item in `docs/backlog-log.md`
§13 ("Applications pivot table" row).

A dedicated page for slicing application history the way a spreadsheet
pivot table would, converged on interactively with Zahir via the
`visualize` MCP tool's mockup mode across several iterations before any
Streamlit code was written - each round showed a live, clickable HTML
mockup with sample data shaped like his real job search, and he redirected
the design each time rather than approving prose alone.

**Data source:** application records (the ones with a real `status` in
`applications.json`), not the full ~591-job Results list - only jobs
actually engaged with have status/strategy-tag/score history worth
slicing.

**Layout, top to bottom:**

1. **KPI cards** (always visible, not tied to the pivot selection below):
   Total applications, applications in the last 30 days, applications in
   the last 7 days. Simple counts over `applications.json`, same
   `created_at`-based recency windows `prospector/kpis.py`'s
   `activity_summary()` already computes elsewhere - reuse that function
   rather than re-deriving the date logic.
2. **Rows are a fixed two-level grouping, not a user-selectable dimension:**
   Channel (bold section header, e.g. "USAJOBS (5)") with each company
   Zahir has applied to within that channel indented underneath. Zahir's
   own framing: "there should be channel, then respective company and
   then what you have got."
3. **Columns and Values remain interactively selectable** (mirroring a
   real pivot table's field list): Columns from {Status, Score band,
   Strategy tag}, Values from {Count, Average compatibility score}. Applies
   within each company row, not to the row grouping itself.
4. **Last activity column** (far right, every company row): human-readable
   recency of the most recent record touching that company - "5d ago",
   "3w ago", "2mo ago" - not a raw timestamp. Purpose is explicitly
   different from the KPI cards: KPI cards answer "how much volume,"
   this column flags "is this one going cold" (e.g. something stuck at
   "under review" for 2 months is worth a follow-up nudge). Confirmed
   valuable directly by Zahir when asked ("do you think date would be of
   use here").

**Not yet decided, revisit before/during build:**
- Exact page placement - a new standalone tab, or a section within
  Results or Prospector. Not discussed yet.
- Whether to add sort/filter by staleness (e.g. "show only companies with
  no activity in 2+ weeks") - raised as a natural follow-on, not committed
  to.
- Whether KPI cards should react to the pivot's current filter state or
  always show unfiltered overall totals - raised, not yet answered.

**Build note for whoever picks this up:** the interactive mockups
(rendered via `mcp__visualize__show_widget`, not saved as files) used
synthetic data - real company names from Zahir's actual target accounts/
applications appeared in later iterations for concreteness, but no real
personal data was in the mockups since they're client-side sample arrays,
not live Panga data.

## 19. Cost Governance — Daily Spend Cap (built 2026-08-11)

Added by Panga-Documentor while producing the first BRD/FRS DOCX export —
a real, permanent piece of system behavior that had only ever been
described in `docs/backlog-log.md`'s build history, not in the FRS proper.
Full technical detail lives in `src/llm_client.py`'s own module docstring;
this section records the product-level shape.

**Problem:** the cost-blast-radius principle (`CLAUDE.md`) governs new code
being written, but does nothing to stop an already-running process from
overspending once started — a real overnight incident (455 jobs scored
through the existing `fit_score` pipeline) crossed $10 within ~7 minutes
and reached $63.86 before the day was half over.

**What shipped:** every real AI call now passes through
`_check_spend_cap()` before any HTTP request is prepared — it blocks NEW
calls once today's real logged spend (`cost_log`) reaches a daily cap
(`PANGA_DAILY_SPEND_CAP_USD`, default $10), while letting anything already
in flight finish. The first block of a UTC day logs at CRITICAL severity;
every blocked call is also recorded in `cost_log` itself
(`error_type="spend_cap_exceeded"`), visible via the Ops tab's failed-call
count. The daily job-search notification (the one Zahir actually receives)
leads with a warning if the cap tripped that day, so a cap-hit day never
silently produces "nothing to report."

**Known, disclosed limitation:** the cap check is a circuit breaker, not a
hard reservation — under genuine cross-process concurrency (the Streamlit
app and a scheduled task both mid-call at once) the cap can be overshot by
the combined cost of everything already in flight before the block
engages. Documented as an accepted tradeoff in the code itself rather than
a gap to silently assume away; see `docs/backlog-log.md`'s spend-cap row
for the full history.

