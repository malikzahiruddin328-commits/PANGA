# Job Search Automation Tool — Working Spec (v0.1)

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
- **`applications`** — tailored package per job + status. Shipped status values (`src/tailoring/applications.py`): "under review", "applied", "interview scheduled", "offer", "rejected" (last three added 2026-07-30, see §16c), "not interested", "save for later" + optional skip-reason text when status is not-interested (feeds §13 feedback loop)

Mechanism for populating `role_skills`: reasoned out (via search/LLM) at the time a new role/industry is first encountered, then persisted — so the system doesn't re-derive it from scratch every time, but keeps refining as more users/roles are added.

## 5. Long-Term Path
- Prove out the workflow as a local tool/script first
- Package as a Windows desktop app once stabilized
- Port to Mac after that
- (Open question, deferred: whether native Windows app vs. cross-platform framework like Electron/Tauri gives better ROI — worth revisiting once the core logic is proven, since packaging choice shouldn't block MVP validation)

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

## 10. Backlog
- **Interview prep module** — built 2026-07-30, see §13.

## 11. LLM Architecture
- **v0: paid-only, Claude via Claude Code.** No local LLM. Rationale: at this usage scale (single user, 2-3 scheduled runs/day + on-demand), token cost is not the likely bottleneck — search/scraping calls are. A local LLM stack (e.g. Ollama) adds a second system to install/maintain, which is a poor tradeoff for a non-developer user.
- **Backlog: hybrid local (free) + paid model split.** Revisit only if/when this scales toward a funded, multi-user product — split lightweight mechanical work (dedup, basic filtering) to a local model, keep quality-sensitive work (tailoring, gap-probing, fit scoring) on the paid model.

## 12. Multi-User Scope
- **v0 is single-user**, designed for the current user only (local-only encrypted storage, per §7).
- Multi-user support is a **later-stage aspiration**, contingent on validating the concept (e.g. VC backing) — not a near-term build target. Architecture should avoid unnecessary lock-in to single-user assumptions where it's free to do so, but should not be over-engineered for scale it doesn't need yet.

## 13. Backlog Additions

| Item | Status | % Complete | Notes |
|---|---|---|---|
| MCP connector vetting pipeline | Ongoing practice | N/A | Test → validate → productionalize before relying on any new connector live. |
| Interview prep module | Built | 100% | 2026-07-30. `src/tailoring/interview_prep.py` (encrypted store, one record per job holding a list of interview rounds) + new Interview Prep tab; "Prep for this interview" on both the Call to Action and Results tabs jumps there with context. Persona-driven, not just role-driven, per Zahir's framing (2026-07-30: "the interview questions will be persona driven... we are marketing/sales and the user is the commodity") — for each named interviewer/panelist, Claude researches their public professional background live (web search, no scraping) and drafts likely questions paired with matching talking points from the master profile, plus questions to ask them, in a fixed schema per round (not free-form prose). CTA-triggered prep reads the full email thread first to find interviewer names before falling back to manual entry. Reasoning happens in a live Claude Code conversation, same mechanical/reasoning split as tailoring (§9) and the gap-probing interview (§3). First demo pass run 2026-07-30 against the real National Endowment for the Humanities CIO application (Results tab, applied status) — labeled `[DEMO]` in the Interview Prep tab and left in place as a reference example rather than deleted, since no real interview was scheduled for it (no confirmed interviewer name — the demo used a generic "typical federal SES hiring panel" placeholder instead of inventing one). The company-research half proved out well beyond a format demo: live web search surfaced NEH's real FY2026 elimination proposal and 2025 staff cuts, which reframed the likely questions toward continuity/cost-cutting instead of generic CIO fluff — a concrete signal that the live-research step earns its keep beyond just filling in the schema. |
| Encryption at rest for `data/` | Built | 100% | 2026-07-30. AES-256-GCM, key in OS credential store via `keyring` (no typed passphrase — see §7). `docs/encryption-at-rest.md` has full detail. |
| Windows-account-loss recovery (re-scoped from "passphrase recovery mechanism") | Built | 100% | 2026-07-30. Recovery code (Settings page) wraps a recoverable copy of the encryption key via PBKDF2 + AES-GCM; standalone `scripts/recover_access.py` (no-terminal launcher `recover_access.vbs`) restores it into a new/repaired Windows account's credential store. `docs/encryption-at-rest.md` §"Recovery" has full detail. |
| Email monitoring — simple scan | Built | 100% | Superseded by the full version in §14. `panga-gmail-cta-scan`, 4x/day, Gmail-label state, push notification on CTA emails. |
| Non-applied-job feedback loop | Built | 100% | Skip-reason capture + hide-from-results done; detection of unreviewed reasons wired into the daily task; live evaluation happens in conversation, not automated by design. |
| LinkedIn profile enhancement | Built | 100% | Manual-export-only, no scraping/login (ToS risk). New "LinkedIn" Streamlit page: upload a PDF export of your current profile (LinkedIn's own "Save to PDF", and/or a browser print of the profile page), text extracted via `pypdf` (`src/linkedin/ingest.py`), then ask Claude to analyze against the master profile + role_skills — produces a 0-100 profile strength score + per-section suggested rewrites (headline/about/experience/skills/certifications), shown as copyable text blocks with Mark-updated/Dismiss actions. Encrypted at rest via `security.crypto_store`, same as the other data stores. Nothing is ever posted to LinkedIn automatically. First real analysis run 2026-07-30 against Zahir's actual exported PDFs. |
| Recruiter mail-blast / marketing outreach | Not started | 0% | Requires per-instance user sign-off before any send, by design. |
| Industry-specific job boards (Category B, public postings) | Built | 100% | 2026-07-30. 19 candidates researched 2026-07-29; all 5 confirmed-scrapeable ones now built in `src/search/industry_boards.py` (Planet Pharma, BioSpace, Beacon Hill Life Sciences, Atrium, GForce Life Sciences) and wired into the daily scheduled task. The other 14 candidates are rejected (blocked/no-jobs-board) or need a headless browser (JS-rendered) — not pursued, see `config/industry_job_boards.yaml` for per-site detail. First live run added 63 new jobs; none scored 60+ (this source mix skews toward clinical/sales/hands-on-technical roles, not IT leadership) — expected given what these firms staff for, not a scraper defect. |
| Paid candidate-network membership (BlueSteps/ExecuNet) | On hold — user decision | N/A | Not a Panga build item; user checking with personal HR contacts before spend. |
| Retained executive search firm outreach | Surfaced to user | N/A | Not automatable (confidential firm-side search); already flagged to user once per his request, his call on timing. |
| UI polish: pay column formatting | Not started | 0% | `$151661-228000` should render as `$151,661-$228,000`. Small, deferred. |
| Direct LLM API integration (replace Claude Code orchestration) | Deliberately deferred | 0% | Sequenced last on purpose — revisit only at multi-user scale (§12 trigger), not before. |
| **Prospector** — personal marketing/sales-funnel layer (KPIs, rejection-pattern diagnosis, proactive FDA/ClinicalTrials/PubMed-based company targeting, strategy-tagging/learning loop) | In progress (steps 1-7 of 8 built) | ~87% | Added 2026-07-29, designed 2026-07-30 — see §16 for the full design. Steps 1-7 built 2026-07-30: KPI dashboard, rejection-pattern diagnosis, all 4 target_accounts signals (late-stage-trial/regulatory-filing/commercial-hiring/funding-IPO via SEC EDGAR), and outreach logging/drafting + LinkedIn-connections contact sourcing (§16b). 78 real target accounts populated. Remaining: strategy tags/Learn Engine (step 8, §16d/§17). |
| **Learn Engine** — cross-cutting feedback loop over every prediction/outcome pair in Panga (scoring, cadence, target accounts, outreach, strategy tags, LinkedIn edits, interview prep) | Designed, not built | 0% | Added 2026-07-30, generalized from Prospector's Learn stage (§16d) at Zahir's request — see §17. Recommend-only, never auto-applies changes (confirmed 2026-07-30). |
| Application status lifecycle extension (interview scheduled / offer / rejected) | Built | 100% | 2026-07-30. Prerequisite identified while scoping Prospector's KPI dashboard (§16c) — without real interview/offer/rejection outcomes, "interview rate"/"rejection rate" would have nothing to compute from. `suggest_status()`/`confirm_status_suggestion()` in `applications.py` were already generic (no code change needed there); what changed: the "Mark status" dropdown in `src/ui/app.py` now offers the 3 new values, "Prep for interview" now shows for "interview scheduled" too (not just "applied"), and `panga-gmail-cta-scan`'s SKILL.md gained step 3C — the scan already classified emails into rejection/interview_request/offer/assessment_request/recruiter_question (for the dashboard mirror, §14) but never matched rejection/interview/offer against a specific application to suggest a status change; now it does, same confidence bar and confirm-don't-guess rule as the existing "applied" matching. |
| LinkedIn manual job intake + document checklist | Built | 100% | 2026-07-30. Since LinkedIn has no public jobs API and blocks scraping/bot logins (ToS), the user browses LinkedIn himself and hands Claude a posting URL directly in conversation instead of an automated search channel finding it. `search/job_store.add_manual_job()` creates the job record (`source="linkedin"`, job_id parsed from the LinkedIn `/jobs/view/<id>/` URL pattern so re-pasting the same posting dedupes correctly even with different tracking params); if the URL can't be read (login wall/bot-check), Claude asks the user to paste the job description text instead — either way the description is captured at intake time rather than re-fetched later, unlike other channels. `tailoring/applications.py` gained `exec_bio_text`/`leadership_summary_text` (two new senior-exec-specific document types, alongside the existing resume/cover letter, fully tailored per job — not a single reused core version) and a `documents_requested` list. The Results tab's per-job detail panel (`ui/app.py`) replaced the old single "Start tailoring" button with 4 checkboxes + a "Request documents" button (applies to every job source, not just LinkedIn) plus expanders showing already-drafted document text in a copyable block, matching the pattern used for LinkedIn profile suggestions. Verified live: app loads clean, a real manually-added job correctly appeared as its own dynamically-grouped "linkedin" channel section with no code changes needed for that grouping. The checkbox/button click path itself was verified against the exact data layer it calls (`upsert_application`/`get_application`) rather than by mouse click — the Browser pane couldn't visually composite in this session (screenshot/canvas-click unavailable), so genuine mouse-driven row-selection in the dataframe grid wasn't possible; worth a quick manual click-through next time the app is open normally to confirm the on-screen behavior matches. |

**Already fully built (for reference, not backlog):** resume ingestion, gap-probing interview, USAJOBS/ZipRecruiter/Dice/Indeed search, company-site search via Workday/SmartRecruiters ATS APIs, tailoring context bundling, applications tracking, Streamlit Results UI with per-channel tables, compatibility scoring, daily scheduled search+score task, desktop shortcut, Gmail call-to-action monitoring (full version, see §14), encryption at rest for `data/` plus its key-recovery flow (see §7/§15, `docs/encryption-at-rest.md`).

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
1. **Scan** (`panga-gmail-cta-scan`, 4x/day) — reads Zahir's inbox, classifies
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
3. **Fulfillment loop** (`panga-cta-fulfillment`, every 10 min) — executes
   what Zahir clicks on that page. **Dismiss** archives the email in Gmail
   and labels it `Panga/Handled`. **Draft reply** has Claude compose a real
   Gmail draft tailored to the email (category + content), created via the
   Gmail connector but **never auto-sent** — Zahir reviews and sends it
   himself, same "prepare but don't submit" principle as §2's application
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

## 16. Prospector — Personal Marketing / Sales-Funnel Layer (Design, 2026-07-30)

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
  Extended the PRD's original 4-value status sketch with a `planned`
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

### UI placement

New **"Prospector" tab** in the Streamlit sidebar, alongside Results / Call
to Action / Settings — target accounts list, the outreach log, and the KPI
dashboard all live there. Mirrors how Call to Action (§14) got its own tab
rather than being folded into Results; the same reasoning applies here —
this is a genuinely different mode of work (proactive targeting, not
reacting to a posted job), not a variation on the existing Results screen.

### Proposed build sequencing

Ordered by dependency + fastest-to-value, not by the order listed above:

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

## 17. Learn Engine — Cross-Cutting Feedback Loop (Design, 2026-07-30)

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

**UI placement:** lives inside the existing **Prospector tab** (§16) as an
"Insights" or "Learn" section with a "Run analysis" button, even though the
data it reads spans the whole app, not just Prospector's own tables — it
doesn't need a separate tab of its own, since it's a report-and-recommend
tool rather than something used moment-to-moment.

**Build sequencing:** last, by construction — it needs real outcome
history from scoring, target accounts, and outreach to have anything to
say. Folded into build step 8 above, not a separate step.
