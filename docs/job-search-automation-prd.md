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
- **`applications`** — tailored package per job + status (drafted, reviewed, submitted-by-user, not-interested, save-for-later) + optional skip-reason text when status is not-interested (feeds §13 feedback loop)

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
| **Prospector** — personal marketing/sales-funnel layer (KPIs, rejection-pattern diagnosis, proactive FDA/ClinicalTrials/PubMed-based company targeting, strategy-tagging/learning loop) | Named, design not started | 0% | Added 2026-07-29. Reframes the project from job-matching/coverage to a full Prospect→Research→Outreach→Application→Response→Outcome→Learn lifecycle. `target_accounts` data model planned, parallel to `jobs`/`applications`. Claude drafts outreach content; user always sends it himself. |

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
