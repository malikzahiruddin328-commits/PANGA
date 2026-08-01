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
- **`applications`** — tailored package per job + status. Shipped status values (`src/tailoring/applications.py`): "under review", "applied", "interview scheduled", "offer", "rejected" (last three added 2026-07-30, see §16c), "not interested", "save for later" + optional skip-reason text when status is not-interested (feeds §13 feedback loop) + `strategy_tag` (§16d/§17, added 2026-07-30)
- **`target_accounts`** — companies worth watching before they've posted a role (Prospector §16a, built 2026-07-30). `src/prospector/target_accounts.py`: company_name, industry, status (watching/qualified/contacted/stale/disqualified), signals (list of `{signal_type, source, detail, date_observed, ref}`), notes
- **`outreach`** — direct contact logged against a job or a target account (Prospector §16b, built 2026-07-30). `src/prospector/outreach.py`: contact_name/title/email, channel, status (planned/drafted/sent/responded/no_response), strategy_tag, plus the Gmail-draft request/reconciliation fields shared with the CTA fulfillment mechanism (§14)

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
| UI polish: pay column formatting | Built | 100% | 2026-07-30. `format_pay()` in `ui/app.py` renders `$151,661-$228,000` instead of `$151661-228000`; falls back to the raw string for non-numeric values (e.g. Indeed's occasionally-messy parsed compensation text) rather than crashing. Found and fixed alongside a real `IndexError` Zahir hit live: `st.dataframe`'s row-selection state persists across reruns by widget key, but the filtered job list can shrink in that same rerun (e.g. moving the compatibility slider) - a stale selected index could point past the end of the new list. Fixed with a bounds-check before indexing, applied to both the Results per-channel tables and the Prospector target-accounts table. |
| Direct LLM API integration (replace Claude Code orchestration) | Deliberately deferred | 0% | Sequenced last on purpose — revisit only at multi-user scale (§12 trigger), not before. Partially revisited 2026-07-30, scoped narrowly to document drafting only — see "Direct API document drafting" below. Zahir explicitly chose to take on that one exception's cost/complexity because it needs a synchronous in-app result; the broader replacement of every other reasoning step (fit scoring, LinkedIn analysis, Prospector Score, interview prep, CTA classification) remains deferred per the original trigger. |
| **Prospector** — personal marketing/sales-funnel layer (KPIs, rejection-pattern diagnosis, proactive FDA/ClinicalTrials/PubMed-based company targeting, strategy-tagging/learning loop) | Built | 100% | Added 2026-07-29, designed 2026-07-30, all 8 build steps built 2026-07-30 (same day) — see §16 for the full design/build detail. New "Prospector" tab: target accounts (78 real companies, all 4 signal sources), outreach logging/drafting + LinkedIn-connections contact sourcing, KPI dashboard, rejection-pattern diagnosis, strategy tags, and the Learn Engine's data-gathering half (§17). Known gaps disclosed in-app rather than hidden: LinkedIn recruiter-contact-rate has no capture mechanism, search-cadence metrics were never built, LinkedIn connections CSV parsing is untested against a real export. |
| **Learn Engine** — cross-cutting feedback loop over every prediction/outcome pair in Panga (scoring, cadence, target accounts, outreach, strategy tags, LinkedIn edits, interview prep) | Built (data-gathering half) | 100% | Added 2026-07-30, generalized from Prospector's Learn stage (§16d) at Zahir's request — see §17. Recommend-only, never auto-applies changes (confirmed 2026-07-30). `src/prospector/learn_engine.py` + "Insights" section on the Prospector tab built 2026-07-30 - gathers cross-table decision/outcome data; the actual pattern-finding reasoning happens live in a Claude Code conversation, same split as rejection diagnosis. |
| Application status lifecycle extension (interview scheduled / offer / rejected) | Built | 100% | 2026-07-30. Prerequisite identified while scoping Prospector's KPI dashboard (§16c) — without real interview/offer/rejection outcomes, "interview rate"/"rejection rate" would have nothing to compute from. `suggest_status()`/`confirm_status_suggestion()` in `applications.py` were already generic (no code change needed there); what changed: the "Mark status" dropdown in `src/ui/app.py` now offers the 3 new values, "Prep for interview" now shows for "interview scheduled" too (not just "applied"), and `panga-gmail-cta-scan`'s SKILL.md gained step 3C — the scan already classified emails into rejection/interview_request/offer/assessment_request/recruiter_question (for the dashboard mirror, §14) but never matched rejection/interview/offer against a specific application to suggest a status change; now it does, same confidence bar and confirm-don't-guess rule as the existing "applied" matching. |
| LinkedIn manual job intake + document checklist | Built | 100% | 2026-07-30, GUI form added same day. Since LinkedIn has no public jobs API and blocks scraping/bot logins (ToS), `search/job_store.add_manual_job()` creates the job record (`source="linkedin"`, job_id parsed from the LinkedIn `/jobs/view/<id>/` URL pattern so re-pasting the same posting dedupes correctly even with different tracking params). Original design was conversational-only (paste the posting into a Claude Code chat message); Zahir expected a Results-tab form instead, so added "Add a job manually (e.g. from LinkedIn)" - a plain expander with Title/Organization/Location/Posting URL/Source/Description fields and a Save button that calls `add_manual_job()` directly, no reasoning needed for this mechanical step. `tailoring/applications.py` gained `exec_bio_text`/`leadership_summary_text` (two new senior-exec-specific document types, fully tailored per job) and a `documents_requested` list. The Results tab's per-job detail panel replaced the old single "Start tailoring" button with 4 checkboxes + a "Request documents" button (applies to every job source, not just LinkedIn). Verified live end-to-end via real keystroke simulation (synthetic `dispatchEvent`/scripted `.blur()` didn't reliably commit `st.text_input` values in the browser-automation environment - only genuine focus+type+Tab did) - job saved correctly with the LinkedIn job ID correctly extracted from the URL. |
| Native Windows app packaging (Mac dropped from scope for now) | Built — not yet tested | 100% | **Update 2026-08-01:** Zahir confirmed this is now fully developed; verification/testing still pending, status not yet upgraded to "Built" until that happens. Discussed 2026-07-30, two separate pieces. **(1) Packaging (straightforward, no rewrite):** bundle the existing Python/Streamlit codebase with **PyInstaller** into a single standalone `.exe` — no separate Python install needed on the machine — paired with **pywebview** to open the Streamlit UI in a plain app window instead of a browser tab. Turns the existing desktop-shortcut launcher (`run_app.vbs`/`run_app.bat`, §13 above) from "double-click → browser opens to localhost" into "double-click → app window opens." Low effort, a few days at most, no logic changes. **(2) The real dependency, same item as "Direct LLM API integration (replace Claude Code orchestration)" above:** Gmail/ZipRecruiter/Dice access and all reasoning (fit scoring, drafting, CTA classification) currently work only because Panga runs inside a live Claude Code session — that's what the MCP connectors and the 3 scheduled tasks rely on. A standalone `.exe` can't carry a live Claude Code session with it, so this becomes a hard prerequisite (not just an optional scale-trigger item) once standalone/unattended operation is the actual goal: own Anthropic API key + direct API calls for reasoning (separate token billing from whatever's paid for Claude Code/claude.ai today — get real cost numbers before committing), Gmail's official API in place of the MCP connector, Windows Task Scheduler in place of Claude Code's scheduled-task system, and per-source research on whether ZipRecruiter/Dice expose anything outside MCP. **No language change either way** — C#/VB.NET/Java were considered and rejected; Python has mature native-packaging tooling (PyInstaller/Nuitka) and a rewrite would cost all existing scoring/search/encryption logic for no functional gain on a single-user personal tool. Recommended sequencing: do (1) soon since it's cheap; only take on (2) when unattended standalone operation (no Claude Code open) is an actual near-term goal, not before. |
| Native Windows app packaging — testing/verification | Not started | 0% | Added 2026-08-01, split out as its own line rather than folded into the "Built" status above per Zahir's explicit request: development and testing are tracked separately. Needs: a clean-machine install/launch check (no local Python/dependencies pre-installed) of the packaged `.exe`, confirming the `pywebview` app window opens and the Streamlit UI behaves identically to the browser version, and that the existing desktop-shortcut launcher still works end-to-end. Not yet tested. |
| Update/hotfix distribution mechanism for packaged app | Built — not yet tested | 100% | **Update 2026-08-01:** Zahir confirmed this is now fully developed; verification/testing still pending, status not yet upgraded to "Built" until that happens. Added 2026-07-31, Zahir's request while discussing dev-environment permissions: broadened Claude Code tool permissions to standing (not per-session) for the Panga project, since dev-time code writes/git commits stay Claude-gated regardless (Edit/Write/NotebookEdit and `git commit` are explicitly denied in `.claude/settings.local.json` even with everything else broadly allowed). Zahir's point: the eventual distributed package (see "Native Windows app packaging" row above) will have **zero** code-write or git-commit capability for end users by design — it's a compiled/packaged app, not a dev checkout — so when a real upgrade or hotfix needs to reach already-installed copies, there's currently no mechanism to deliver it. Sequencing: blocked on "Native Windows app packaging" above actually shipping first — no concrete design started, this is a placeholder to make sure it isn't forgotten once packaging becomes real. **Design direction converged 2026-07-31 (two ideas discussed and combined, not yet built):** (1) NOT literal `git` on the end-user machine — would require git installed locally and, for a private repo, an embedded credential in the distributed binary (real leak risk once that binary is out in the wild). Instead, the installed app polls a lightweight version manifest (e.g. GitHub Releases API over plain HTTPS, no git binary needed) for the latest version tag, compares to the locally installed version, and if newer, downloads the release asset (a rebuilt PyInstaller bundle) and applies it via a small updater step. (2) NOT a "click here to install" link in a mass email — that's the exact pattern phishing emails use (trains users toward risky click-through, and is the kind of content spam filters are tuned to block). Email's role, if used at all, is a heads-up notification only ("a hotfix is available"), linking to a normal changelog/webpage rather than triggering a direct download — the actual update action stays inside the already-running, already-trusted app (an in-app "Update available" check + button), not a link clicked from an inbox. |
| Update/hotfix distribution mechanism — testing/verification | Not started | 0% | Added 2026-08-01, split out as its own line rather than folded into the "Built" status above per Zahir's explicit request: development and testing are tracked separately. Needs: an end-to-end check that a real new version is detected against the version manifest, downloads and applies correctly over an existing install without data loss (`data/` must survive an update), and that the in-app "Update available" indicator/button and offline/grace-period messaging behave as designed. Not yet tested. |
| Licensing / subscription + per-user API key handling for a sold product | Designed, not built | 0% | Added 2026-07-31, designed 2026-07-31 (full design session, see `docs/licensing-scope.md`). **Business model:** 1-year subscription, 15-day free trial, billed via **Stripe** (Stripe Billing + card processing — 2.9% + $0.30 per charge, +0.5% of subscription volume, no fixed monthly cost). **LLM billing:** each customer connects their own Anthropic account (or creates one during setup) — Panga never meters or marks up API usage, customer is billed by Anthropic directly, same "cost-transparent, never hidden" principle as the in-app API cost displays elsewhere in Panga. **Activation:** one device per license; planned device moves are self-service (in-app "deactivate this device" releases the binding); lost/stolen-device transfers go through manual support review initially (not full self-service automation) rather than over-building identity verification for a rare case. Rate-limited (max 1 transfer/30 days) against license-sharing abuse. **Offline handling:** license check-in on launch + daily; 3-day offline grace period before hard lock, with a small persistent top-right status indicator ("License verified" / "License unverified — N day(s) left" + a Refresh button to force a re-check) — distinct messaging for "can't verify (offline)" vs. "grace expired" vs. "actually expired (confirmed lapsed/trial-ended)," since conflating those would mislead the user about what action fixes it. **Backend:** a small serverless license service (Stripe webhook-driven for renewal/cancellation, database of license/device/trial-start records) — deliberately boring/minimal infrastructure, not a service Zahir has to run and patch himself, same "least infrastructure that works" instinct as GitHub Releases for the update mechanism (row above). **Onboarding UX:** single email-based screen (no separate login/signup fork), plain-language explanation of the Anthropic-account requirement (no unexplained "API key" jargon) with a clear default action. **Pricing:** $200/year, placeholder Zahir confirmed 2026-07-31 pending the real cost-evaluator backlog item below — not a final number. **Refund/cancellation policy:** no refunds (US), confirmed 2026-07-31 — feeds into the Terms of Service/EULA legal-docs gap noted below, not yet drafted. Still distinct from and building on "Direct LLM API integration" above (this item assumes that prerequisite is done) and depends on the native-packaging branch's build shape being far enough along to know what's actually being licensed. Not yet built — next step is its own branch/worktree/dedicated session, same pattern as native-packaging and update-mechanism. |
| Generalize Panga for multi-vertical sale (currently hardcoded to Zahir's own career/industry) | Designed, not built | 0% | Added 2026-07-31, surfaced while scoping licensing — selling Panga as a general job-search tool exposes that a lot of its current logic is Zahir-specific, not per-user configurable: the CISO/security-officer-title disqualification rule (his personal disqualifier, hardcoded into scoring instructions), Prospector's entire signal-sourcing stack (openFDA regulatory filings, ClinicalTrials.gov, mega-pharma denylist, commercial-hiring keyword list — all life-sciences/pharma-specific), and `target_roles`/industry weights (one shared config file, not per-user). **Design direction, confirmed 2026-07-31:** (1) after resume + support-doc ingestion, a new intake step asks the user's desired industries/verticals (not a hardcoded dropdown — trades vary too widely, e.g. a physician's title ladder looks nothing like a nurse practitioner's or a chemical engineer's, and even within one trade the target companies differ hugely by sub-vertical — a chemical engineer targeting nuclear plants needs different target-account signals than one targeting plastics/injection-molding plants). (2) Job titles prefilled from the resume, cross-checked against live-reasoning "what's the standard title ladder for this trade/vertical" — same live-reasoning architecture as the existing `src/skills/` role/skill lookup (step 2 of the original build order), extended to be vertical-aware rather than assuming Zahir's own IT/CIO ladder. (3) `target_roles`/weights generated per-user via a reasoning pass over the resume + self-reported seniority/years of real-world experience + chosen verticals — proposing adjacent/equivalent roles the user might not have listed themselves — then loaded into the existing Settings `st.data_editor` for the user to review/edit, not typed in blind. (4) Prospector's proactive signal-sourcing (FDA/ClinicalTrials/etc.) stays **life-sciences-specific for now** rather than speculatively pre-built for every trade — recommended to build new verticals' signal sources incrementally as real customers in those verticals sign up, not upfront; life-sciences remains the proven reference implementation new verticals get built against. (5) Disqualifiers (like the CISO rule) become a user-editable list gathered during the existing gap-probing interview, not hardcoded in Python. Not yet built — likely its own branch/worktree/dedicated session given its size and centrality (touches `profile/interview.py`, `skills/`, `target_roles` config, and the whole `prospector/` signal architecture), and arguably should be sequenced *before* the packaging/licensing branches finish, since it changes the core product those branches are shipping. |
| Marketing and sales strategy | Not started | 0% | Added 2026-07-31, Zahir's explicit instruction to backlog rather than design now. |
| Realistic cost evaluator (real infra + Stripe + support costs vs. subscription price) | Not started | 0% | Added 2026-07-31, Zahir's explicit instruction to backlog rather than design now — needed before the $200/year placeholder price (see licensing row above) can be treated as a real number. |
| Gmail OAuth scaling for non-technical customers (shared verified app + CASA assessment) | Not started | 0% | **Cost to proceed: real money (CASA security assessment fee + Google verification lead time) — deprioritized until customer volume justifies it.** Added 2026-08-01. Currently every customer must register their own Google Cloud project + OAuth client to use Gmail monitoring (native-packaging's `data/gmail/credentials.json` model) — a real barrier for a non-technical buyer (Zahir's own example: a plumber). **Decision 2026-08-01:** ship with the current per-customer model for now (**$0 cost** — engineering time only), invest in building the best possible in-app guided wizard for it (native-packaging's job — deep-link buttons into the right Google Cloud Console pages, auto-detect the downloaded JSON and move it into place, in-app video). The real fix — one shared, Microsoft-verified Google OAuth app so customers just click "Connect Gmail" with zero console setup — is scoped here for later: Panga's Gmail scopes (`gmail.modify`, `gmail.compose`) are Google-classified **restricted scopes**, so this requires Google's app verification process plus an annual third-party security assessment (CASA) — real cost and lead time. Revisit once customer volume justifies the investment, not before. |
| Terms of Service / EULA / Privacy Policy | Not started | 0% | **Cost to proceed: $0 if drafted via reasoning + Zahir's review rather than a lawyer — high priority, do before any paid step below.** Added 2026-08-01, surfaced while researching Microsoft Store submission requirements (see `docs/business-requirements-document.md` §7) — a privacy policy is a hard Store certification requirement (policy 10.5.1) for any product accessing Personal Information, not just a legal nicety, and is required for direct-distribution sale regardless of the Store. Needs to cover: resume/profile/Gmail data handling, the no-refunds policy (§3 of the BRD), and Anthropic-account/LLM-usage disclosure. Not drafted yet. |
| Microsoft Store submission prerequisites | Not started | 0% | **Cost to proceed: real money on 1 of 4 items (corrected 2026-08-01 with real pricing) — sequence last, after every $0 item above is done.** Added 2026-08-01, full detail in `docs/business-requirements-document.md` §7. Real, concrete gaps found researching current Store policy (version 7.19): (1) **paid, ~$211-226/yr (Sectigo) up to $399-560/yr (DigiCert standard/EV)** — a code-signing certificate is required if distributing via direct download URL or Store-hosted binary (policy 10.2.9) — previously only noted as "deferred, nice to have" in native-packaging's scope doc, now a hard submission blocker. A 2026 CA/Browser-Forum rule caps validity at ~1 year, so this is an annual recurring cost, not a one-time purchase. (2) **$0 — corrected 2026-08-01.** Originally flagged as paid; real research shows Microsoft removed the $99 Company-account fee as of May 2026 — both Individual and Company Partner Center accounts are now free. Zahir likely still wants a Company account given the product requires financial/subscription functionality (policy 10.8.3), but it costs nothing either way now. (3) **$0** — Microsoft's certification reviewers need working demo/test credentials (policy 10.3.1), i.e. a test license that bypasses the paywall so they can actually exercise the app — just engineering/process work; (4) **$0** — Live Generative AI content policy (11.16) requires disclosing Claude/AI usage in the Store listing and providing a way for users to report bad AI-generated output, which the developer must act on — Panga's existing point-and-talk feedback widget is a plausible reuse candidate, no new spend implied, but needs explicit confirmation not an assumption. Good news: Stripe as the payment processor needs no rework and no extra cost (policy 10.8.6 explicitly allows third-party billing for non-game subscriptions), and **direct-download distribution (outside the Store entirely) avoids even the code-signing cert** — worth treating as the launch channel, with Store submission as a later step that's now cheaper than originally thought. |
| Desktop shortcut (`run_app.bat`) — dedicated port to stop stale dev-server collisions | Fix written, not yet committed | ~90% | Was previously only an operational diagnosis buried in §16b/§16c narrative (2026-07-30/31: a shared long-running Claude Code dev-preview process on port 8501, holding stale cached modules, was mistaken for a code bug more than once — worked around each time by restarting via the desktop shortcut, never fixed at the root). Actual fix found uncommitted in the main checkout's working tree 2026-08-01 (author unknown to this session — a different concurrent session's in-progress edit, left untouched per the release-manager convention's "never touch another session's unrelated uncommitted files" rule): `run_app.bat` now launches on a dedicated port 8510 (separate from Claude Code's dev-preview default of 8501, so the shortcut's own launches can never collide with or get shadowed by a dev-preview server) and kills anything already listening on 8510 first, so every double-click starts a genuinely fresh process rather than reusing a hung one. Not yet reviewed, tested, or committed by anyone — flagging here so it doesn't get lost, not claiming it's verified. |
| **Prioritization note (2026-08-01, Zahir's explicit instruction):** among everything added to the backlog today, sequence the $0-cost items first — drafting the ToS/EULA/Privacy Policy, building the Gmail setup wizard (Option 2), the Store's demo-license and AI-disclosure/reporting items, and now (corrected) the Partner Center account registration itself — and treat every item with a real, researched dollar cost (CASA assessment $500-$4,500/yr, code-signing certificate ~$211-560/yr) as deliberately deferred until there's revenue or a specific funded decision to spend, not something to schedule alongside the free work by default. Full real-cost table in `docs/business-requirements-document.md` §11. |
| Cross-source dedup (job-board postings folded into a matching direct company-site listing) | Built | 100% | 2026-07-30, per Zahir's request: "what is on job boards should be deduped by company site... if the company has the same job then show only that." New `ranking.prioritize.dedupe_across_sources()` — when a job-board posting (Indeed/ZipRecruiter/Dice/USAJOBS/industry boards) matches a direct company-site posting (Eisai/AbbVie/IQVIA today, via `company_sites.py`) on organization (exact, after stripping corporate suffixes like Inc/LLC) + title (exact), the company-site version is kept as authoritative and the board posting folds into it — surfaced via the same "Open posting (i of N)" mechanism already used for same-channel merges, and the channel expander's dup-note now reports both kinds of merges separately. Identified structurally (`source == organization` is how `company_sites.py` always shapes these records), not a hardcoded company list, so any company added to `company_sites.py` later is covered automatically with no further code change. Matching is deliberately narrow, not fuzzy — a false merge (two different real jobs shown as one) is worse than a missed one, same reasoning that already ruled out matching between two job boards with no company-site anchor to match against. Verified two ways: a synthetic Eisai/Indeed/ZipRecruiter test (correct merge, and correctly left alone a same-title-different-org job and a different-title-same-org job), and a direct check against the real 620-job store — zero actual overlaps exist yet for Eisai/AbbVie/IQVIA specifically (the board searches haven't independently surfaced those companies' postings so far), so this hasn't visibly changed Results counts today but is wired and will activate the moment a real duplicate appears. |
| IQVIA restricted to US-only postings | Built | 100% | 2026-07-30, Zahir's explicit request — IQVIA is a large global CRO and most of its Workday listings are outside the US (confirmed: 29 of 64 stored IQVIA jobs were non-US, spanning Mexico/Korea/Chile/China/Malaysia/Poland/Belgium/Germany/Finland/Croatia/UK/Canada/Ireland/Portugal/Singapore/Japan/Switzerland/Italy). `search.company_sites.search_workday_jobs()` gained a generic `applied_facets` passthrough to Workday's own server-side CXS facet filter (the same mechanism the career site's own filter UI uses); the daily scheduled task's IQVIA call now passes `{"Location_Country": ["bc33aa3152ec42d4995f4791a106ed09"]}` (IQVIA's Workday facet ID for "United States of America," discovered live by POSTing an empty search and reading the response's facets list — documented in the SKILL.md in case it needs rediscovering later). Retroactive cleanup of already-stored jobs was done carefully, not by text-matching: an initial "does the location text say United States" heuristic was WRONG (multi-location postings show as "N Locations" regardless of country in the search-list response — e.g. a genuine Rosemont, IL posting displayed that way and would have been wrongly deleted), so each stored IQVIA job was instead checked against its own Workday job-detail endpoint's authoritative `country` field before removing anything; confirmed zero linked applications on the removed set first. Net: 620 → 591 jobs in the store; 2 postings 403'd on the detail lookup and were left alone rather than guessed at. |
| Point-and-talk UI feedback widget | Built | 100% | 2026-07-30. `src/feedback/` (JSON store + free `SpeechRecognition`/Google-endpoint transcription, no API key) + `src/ui/feedback_widget.py`: a "Leave feedback on this screen" recorder on every tab, voice or typed, tagged to that tab as a proxy for "point" (true click-to-annotate isn't reachable with current tooling). Settings tab shows all open notes for review. Confirmed working in the wild - a real note left through it (Call to Action badge color-coding suggestion) was read and actioned the same session. |
| UI visual polish pass | Built | 100% | 2026-07-30. New `.streamlit/config.toml` theme (navy accent, light+dark, Inter font), Material icons on the 6 tab buttons, `use_container_width` migrated to `width="stretch"` throughout (deprecated param). Readability standard established and applied app-wide: no `st.caption()` anywhere in Panga (all 50 instances converted to `st.markdown()` for full-contrast text) - captions read as unreadably light gray for content users need to actually read; going forward all new Panga UI code uses `st.markdown()`, never `st.caption()`. `st.code(..., wrap_lines=True)` for any code block showing prose rather than literal code. |
| Settings "Your documents" — consolidated intake | Built | 100% | 2026-07-30. Resume ingestion (`profile/ingest.py`, build step 1) had zero GUI before this - a YAML manifest + script only Claude Code could run. Added `ingest_uploaded_document()`/`load_manifest_result()`/`remove_document()` (upsert-by-filename, handles .docx/.pdf) and a Settings section to upload/categorize (resume/context/reference)/remove source documents. LinkedIn profile PDF and connections CSV uploads relocated here too from the LinkedIn tab (one shared home, not duplicate uploaders) - the LinkedIn tab now shows analysis/suggestions only, with a "Go to Settings" pointer. |
| `st.rerun()` confirmation-message bug, app-wide | Fixed | 100% | 2026-07-30, found while investigating a real "I click the button and nothing happens" report (Results tab's "Request documents"). Root cause: `st.success()`/`st.info()` immediately followed by `st.rerun()` - the message renders for a fraction of a second before the rerun wipes the page, so it's never actually seen. Same anti-pattern existed in ~11 places across the app; all converted to `st.toast()`, which survives a rerun. Established as a standing rule: never pair `st.success`/`st.info`/`st.warning` with an immediately-following `st.rerun()`. |
| Prospector Score | Built | 100% | 2026-07-30, Zahir's explicit request: "there has to be an overall prospector score and as this is self-learning we need to emphasize that... this is a key differentiator." `src/prospector/prospector_score.py`: same "Python gathers, Claude reasons" split as `fit_score`/LinkedIn's profile-strength score - reuses `gather_learn_engine_input()` and the existing KPI summaries wholesale. New headline section at the top of the Prospector tab (bordered card, same pattern as LinkedIn's score fix), with an explicit "why this score" + "how to raise it" (concrete `next_actions` list, not vague encouragement) - the self-learning angle is made visible in the UI copy, not just true in the architecture. Seeded with a real computed score (not a placeholder): 25/100, reflecting strong discovery/coverage (591 jobs, 78 target accounts) but almost no real engagement yet (2 applications, 0 outreach, 0 target accounts advanced past "watching"). Recomputing at any point naturally "pulls" real progress since the gather function always re-reads live data - no separate completion-tracking needed, unlike LinkedIn's suggestions (which rewrite text living outside Panga). Open question, not yet resolved: whether job `fit_score` should get similar next-actions treatment - flagged as architecturally different (a per-posting match score, not a profile-style score Zahir can directly improve). |
| Application "dossier" — per-job traceable file | Built | 100% | 2026-07-30, Zahir's request: one traceable file per job engaged with (applied/rejected/etc.), consolidating everything instead of cross-referencing 3 separate JSON stores by hand. New `src/tailoring/dossier.py`: `write_dossier(source, job_id)` renders one Markdown file per job to `data/applications/dossiers/{org-title-slug}-{hash}.md` (hash suffix guarantees uniqueness for duplicate org+title postings) - posting details, fit score, status/timeline, drafted documents, full interview-prep detail. Regenerated wholesale on every relevant mutation, hooked into `applications.py`'s `upsert_application`/`set_strategy_tag`/`confirm_status_suggestion` and `interview_prep.py`'s `start_round`/`save_round`/`record_round_outcome` via a lazy-imported `_write_dossier()` helper (avoids a circular import, since dossier.py reads from both those modules) - fires regardless of whether the trigger was the Streamlit UI or a live Claude Code session. **Superseded 2026-07-31 - see "Application edit-review workspace" row below**: the encrypted-only, Markdown-only design here was extended into a real per-application folder holding plain, directly-editable `.docx` files alongside the dossier, closing the exact tradeoff flagged here. |
| Call to Action category color-coding | Built | 100% | 2026-07-30, addressed real feedback left through the point-and-talk widget above. New `CATEGORY_COLORS`/`CATEGORY_URGENCY` dicts (offer=green/"Act now", interview_request=violet/"Time-sensitive", assessment_request=orange/"Time-sensitive", recruiter_question=gray/"Routine", rejection=gray/"No action needed") drive `st.badge()` calls on the category stat row and above each category's email list - native Streamlit semantic colors, no custom CSS. Built by a background agent while foreground work continued on the LinkedIn tab in the same session; verified independently afterward (not just trusted the agent's own report) - `st.badge`'s color values confirmed valid via `inspect.signature`, code compiles, renders live with no errors, and none of the session's other concurrent edits were clobbered. |
| Applications pivot table | Designed, not built | 0% | See §18. Brainstormed interactively with Zahir via mockups (visualize tool) before writing any Streamlit code - channel-then-company grouped rows, selectable columns/values, KPI cards, and a "last activity" date column, converged over several iterations. |
| Direct API document drafting (one-click "Generate documents") | Built | 100% | 2026-07-30. Zahir's request, in his words: "when one clicks on the job rec and if they click on the type of docs and click generate. Then the documents should be generated so that the user can enter the actual job application and apply for the job." The Results tab's document checkboxes previously fed a "Request documents" button that only saved intent and told Zahir to go draft it in a separate Claude Code conversation - the exact friction point behind his repeated "the button isn't doing anything" reports. Fixing it for real needed a synchronous in-app result, which Panga's usual "Python orchestrates, Claude reasons live" split (§11) can't give (no live Claude Code session backs the Streamlit process itself) - offered Zahir the tradeoff via AskUserQuestion (direct Anthropic API key = instant but new per-token billing, vs. a background scheduled task = a few minutes' delay but no extra cost); he chose the direct API key. New `src/tailoring/drafting.py`: reads `ANTHROPIC_API_KEY` from `.env` (same `load_dotenv` pattern as `search/usajobs.py`'s `USAJOBS_API_KEY`; the key line already existed blank in `.env.example`), calls Claude Opus 5 with `output_config.format` (structured JSON output, one field per requested document type) so the response maps straight onto `applications.py`'s existing `resume_text`/`cover_letter_text`/`exec_bio_text`/`leadership_summary_text` fields - no text parsing to get wrong. System prompt is explicit about not fabricating facts outside the master profile, and switches to full federal-resume conventions (detailed chronological history, hours/week, no page limit) when the posting is a US federal job. This is a deliberate, narrow exception to the live-reasoning architecture, not a reversal of it - see the "Direct LLM API integration" row above for how the broader deferral still stands. First real run 2026-07-30 against the live National Endowment for the Humanities CIO application: resume + cover letter drafted and saved, verified round-tripping correctly through the encrypted `applications.json` store. Rough cost: roughly $0.10-0.25 per job for a full 4-document set, less for a partial selection. **Two same-day refinements from live user testing.** (1) Zahir watched the "Drafting your documents..." spinner and asked for a real progress bar instead - `generate_documents()` was restructured from one combined API call requesting all doc types at once into one call per doc type, each updating an `st.progress()` bar ("Drafting 2 of 3: Cover letter..."), so the progress shown is real completion state, not a simulated animation. The job+profile context is identical across those calls, so it's marked `cache_control: ephemeral` and only the first call pays full price for it - later calls in the same batch read it back at ~10% cost, keeping the per-call-overhead from splitting the request mostly offset. (2) Zahir's explicit follow-up: "the generated resume needs to be ATS perfect so that resume that you generate needs to have compatibility score and also ability to increase compatibility score." The resume's drafting instructions were rewritten around real ATS-parsing constraints (literal standard section headers, no tables/markdown, consistent Month-YYYY dates, contact info as plain text, exact keyword overlap with the posting's stated requirements woven in only where truthfully supported), and the resume's structured-output schema was extended beyond the other three doc types to also return `ats_score`/`ats_rationale`/`ats_next_actions` in the same call - Claude self-assesses the exact text it just wrote against the job posting, same "score + why + how to raise it" shape already established for Prospector Score and LinkedIn's profile-strength score. New `applications.py` fields `resume_ats_score`/`resume_ats_rationale`/`resume_ats_next_actions`, shown in a bordered container above the drafted resume text. First real scored run: 74/100 against the NEH posting, with genuinely specific gaps (no Executive Core Qualifications narrative, missing federal frameworks like FISMA/FedRAMP, no quantified team/budget figures, no board-briefing examples) rather than generic advice - confirms the self-assessment is grounded in the posting's real content, not boilerplate. |

| Document drafting - writing voice, gap-probing loop, real resume styling (2026-07-31) | Built | 100% | Four more refinements from continued live testing, same day. **(1) Writing voice.** Zahir: "the writing style MUST be human like for me it will be British style with US english spellings... no other AI tool should be able to pick this up." `SYSTEM_PROMPT` in `drafting.py` now specifies British prose conventions with American spellings and an explicit list of AI-writing tells to avoid (buzzwords, repetitive triads, formulaic openers, em-dash overuse) - framed honestly as writing-quality guidance, not a guaranteed detector-evasion claim, since that can't be tested against arbitrary third-party tools. **(2) Progress, one level deeper.** Each document's own API call now streams (`client.messages.stream`) instead of a single blocking request, surfacing a live thinking->writing sub-status with a running character count via a new `on_progress(..., substatus=...)` callback - the bar now moves continuously during a single document's generation, not just between documents. **(3) The gap-probing question loop - the core architectural piece.** Zahir: "the whole purpose of this application... you will change not invent facts but ask questions... generate a version that is 100%." The resume's structured-output schema gained `clarifying_questions` (specific, directly-answerable questions for real facts the resume is missing, e.g. "What was your total annual IT budget at SK Life Science?") alongside `ats_next_actions`. Answering them in the Results tab calls new `drafting.save_gap_answers()`, which writes into the master profile's own `gap_interview_answers` via the existing `profile/interview.py` mechanism from onboarding (build step 2) - a fact confirmed here helps every future job's drafting, not just this one - then regenerates just the resume with the enriched profile. Verified end-to-end against the real NEH job with a clearly-marked test answer, cleaned up immediately after via filter-and-resave. **(4) Real resume-style `.docx` export.** Zahir: "the document save function should follow my resume style," and separately noted the file's Author metadata showed "python-docx" instead of his name. `docx_export.py` was rewritten after directly inspecting Zahir's actual resume file on disk (`C:/Users/User/Desktop/GAND/Zahir Resume.docx`) via `python-docx` rather than guessing at a style: Times New Roman throughout, a compact 10pt body where headers are bold-not-bigger (his real resume's own convention), and his own teal accent color (`#00786C`) on his name, pulled from the real file's Title run. Plain-text lines are mapped to Word styles via heuristics (first line = name, contact block after it = centered, ALL-CAPS = bold header, "- " = real bulleted list item, a line matching a date-range pattern = bold company/role line) - these heuristics work because they're reading a format `RESUME_SPEC` already instructs Claude to produce, not guessing at arbitrary text. `core_properties.author`/`last_modified_by` now set to the candidate's real name from the master profile instead of python-docx's literal default value of the string `"python-docx"`. Verified by round-tripping a real generated resume through the new export and re-reading the resulting file's paragraph styles and metadata. **Also, unrelated to drafting:** the Results table's native row-selector checkbox column was replaced with a `st.column_config.ButtonColumn` on the Role column itself - clicking a job's title now opens its detail panel directly, confirmed against the exact API signature of the installed Streamlit version (1.60) rather than assumed from memory. |

| Document drafting - resume length/date fidelity, Apply Assist packet, cover-letter formatting (2026-07-31) | Built | 100% | Three more refinements, same day, found through continued live use against Zahir's real VP of Data & Analytics application. **(1) Resume length wasn't actually binding.** The previous "target roughly 2 pages, recent ~10-12 years in full detail" wording (see row above) still produced ~5-page-equivalent output in practice - measured directly (~10,995 characters) after a real regeneration. `RESUME_SPEC` was rewritten with hard caps instead of soft targets: full bullets for only the 3 most recent roles (5-6/4-5/3-4 bullets respectively), consecutive same-employer promotions share one company header instead of repeating it, everything else condenses into one-line `EARLIER CAREER` entries regardless of age, and a roughly 900-1100 word total budget. Verified down to ~1,100 words (roughly 2 pages) on the same real job, ATS score unchanged at 87-88. **(2) A real date-fidelity bug, caught while verifying (1).** The regenerated resume kept printing "Vice President ... February 2020" instead of the correct "March 2020." Root cause wasn't the AI ignoring instructions - Zahir's date correction from an earlier session had only ever been saved as a free-text `gap_interview_answers` note; the authoritative structured `work_history` record in `master_profile.json` still held the original, wrong `02/2020` from the source resume file, and drafting reads that structured record. Fixed the structured record directly (title also corrected to "Vice President, Head of Applications"), and added an explicit exact-date-fidelity rule to `SYSTEM_PROMPT` ("never round or smooth a date to make a transition look gapless") as a second line of defense. **(3) Cover letter export was reusing the resume's formatting heuristics wholesale**, which broke it: Zahir's real complaint was the opening greeting line ("Dear Hiring Team,") rendering as a giant 20pt heading, because `docx_export.py`'s one shared renderer always treats a document's first non-blank line as "the candidate's name." New dedicated `cover_letter_to_docx_bytes()` in `docx_export.py`: real system date at the top (never AI-generated), a recipient block (company name from `job["organization"]`, falling back to a `[Company Name]` placeholder for blind/"Confidential" postings; company address always shows `[Company Address]` since no field for it exists anywhere in the job schema), then plain body paragraphs at one consistent size with the signature line bolded. Verified against the real cover letter text for the same live application and sent to Zahir as an actual rendered `.docx` before wiring it in, per his ask to "show me how it would look" first. **(4) Apply Assist packet** - a 5th document type (`apply_answers`) alongside resume/cover letter/exec bio/leadership summary: a structured list of ready-to-paste `{label, value}` answers for common ATS-form fields (phone, LinkedIn URL, work authorization, salary expectations, etc.), pulled only from facts already in the profile - never invents a missing fact, writes `"[Not yet provided - ask Zahir]"` instead. Shown as an expander of label/value pairs on the Results tab rather than a downloadable document, since it's meant to be copy-pasted field-by-field into the real application form, not submitted as a file. **Also, unrelated to drafting:** the Role-column `ButtonColumn` click-to-select experiment (see row above) was reverted back to the native `on_select`/`selection_mode="single-row"` row-selector after Zahir reported the click doing nothing while actively blocked on a real task - couldn't verify canvas-grid clicks end-to-end in this environment, so the proven mechanism won over the unverified one. Every `st.dataframe()` call app-wide (12 tables) now left-aligns and auto-sizes its columns via a shared `left_aligned_columns()` helper. A real widget-state bug was also fixed: clarifying-question text areas keyed by list position bled a previous round's answer text onto a differently-worded question at the same position after a regeneration - re-keyed by a hash of the question text instead, and 5 corrupted `gap_interview_answers` entries this had caused were identified and removed. |

| Application edit-review workspace | Built | 100% | 2026-07-31, Zahir's request, in his words: "it should be a mandatory field which says you have made changed to the documents i created... tell me what you changed and why," followed by "you need to monitor the downloaded file... start a folder in the application folder itself... a folder per application," and "this data does not need to be encrypted." Originated as a proposed rename/repurpose of the existing "Strategy tag" field, but scoped instead into a new mechanism alongside it (strategy tags keep their original Learn Engine purpose - correlating an intentional drafting approach with outcomes - since that's a different, still-useful thing from tracking unplanned hand-edits). Reused `tailoring/dossier.py`'s existing per-application folder (§ row above) rather than introducing a new UUID scheme, as Zahir's own suggestion allowed ("you can have your own UUID reference") - the folder's existing `{org-title-slug}-{hash}` naming already guarantees uniqueness per (source, job_id) and is more human-readable in File Explorer than a bare UUID. Two new functions split cleanly by *when* they're allowed to touch a workspace file: `sync_workspace_documents()` writes/overwrites `resume.docx`/`cover_letter.docx`/`exec_bio.docx`/`leadership_summary.docx` (via the existing `docx_export.py` renderers) ONLY at the moment a document is freshly (re)generated - never as a side effect of `write_dossier()`'s generic refresh (status change, skip reason, strategy tag), which would otherwise silently clobber Zahir's in-progress edits on an unrelated save. If a workspace file already differs from the last stored draft text when a fresh regenerate happens (Zahir edited it since), the existing file is renamed to a timestamped backup (`resume.edited-2026-07-31T121500.docx`) before the new draft is written - his edits are preserved on disk, never destroyed. `check_for_edits()` reads a workspace file back via `python-docx` and diffs it (stdlib `difflib`) against the stored text to detect what Zahir changed without him pasting anything back manually; a document drafted before this feature existed (no workspace file yet) is reported distinctly (`no_workspace_file: True`) rather than being silently indistinguishable from "no changes found" - a real gap caught by checking against Zahir's own 2 pre-existing real applications before calling this done. Whole folder switched from encrypted-at-rest to plain files per his explicit call - needs to be directly double-clickable/editable in Word, which encryption prevented; still lives under `data/` so it persists across code updates/reinstalls exactly like every other Panga store (the only loss path is deleting `data/` itself, same as everything else). New `applications.py` fields: `documents_drafted_at` (bumped whenever any prose doc field changes) and `document_edit_review` (`{checked_at, documents, reason}`, saved via `record_document_edit_review()`); `needs_edit_review()` is pure/no-I/O so the Results tab can gate on an already-loaded record - True whenever a drafted document exists and either no review has been saved or the saved review predates the latest `documents_drafted_at` (so regenerating invalidates a stale review rather than leaving stale reasoning attached to newly re-drafted text). Zahir explicitly asked for a hard gate, not a nag (confirmed via AskUserQuestion): the Results tab's "Save status" button now refuses to persist a status of "applied" and shows an error instead if `needs_edit_review()` is still true, rather than silently letting it through. Regression-tested against the real `applications.json`/`jobs.json` stores (write/read/backup-on-regenerate/stale-review-invalidation/cleanup, real record counts confirmed unchanged before and after) and verified live on the isolated port-8504 instance with zero console/server errors. |

| Results tab - UX/reliability sweep (2026-07-31) | Built | 100% | A long string of live-usage fixes across one session, each caught by Zahir actually using the app. **Role-click retried and this time it stuck.** The prior "ButtonColumn on Role" revert (row above) was retried after confirming Streamlit 1.60 (the version actually installed) supports a real `on_click` callback for `ButtonColumn` with a documented `key`/session-state contract - implemented, then confirmed live in his running app that clicking a Role cell opens the detail panel with no checkbox. Added an invisible-anchor `scrollIntoView` (`st.html(..., unsafe_allow_javascript=True)`, not iframed) that auto-scrolls to the newly opened detail panel on a fresh click only - gated on a flag `_activate_row`'s callback sets, so typing in a box inside the panel doesn't yank the view back up on every keystroke. **Human-readable timestamps.** New `format_timestamp()` helper renders `2026-07-30T16:15:55...` as `Jul 30, 2026 at 4:15 PM`; applied to the Prospector Score and UI-feedback timestamps - Zahir: "please change the time format it is not human understandable." **`st.caption()` violations fixed.** Several new captions added mid-session (clarifying-question help text, workspace-folder instructions, diff summary) broke the standing project-wide rule (session memory: never use `st.caption`, always `st.markdown`, full-contrast text only) - caught and converted before shipping. **Downloads-folder button removed.** The generic "Download (.docx)" button wrote a second, untracked copy to Chrome's Downloads folder - the literal reason `check_for_edits()` couldn't see edits Zahir made to a file he'd downloaded instead of opening from the per-application workspace folder. Removed entirely (`descriptive_doc_filename()`, the `re` import, and the two docx-export imports all went with it as now-dead code); the workspace folder is the one editable copy going forward. **Workspace files renamed to match.** `dossier.py`'s `resume.docx`/`cover_letter.docx` etc. were renamed to the same `Name_DocType_Role_Company.docx` convention the removed download button used, via new `_workspace_filename()` + a one-time `_migrate_legacy_filename()` that renames any pre-existing folder's files in place the first time they're touched - verified against Zahir's real Aerospike application folder, migration confirmed working live. **Edit-review flow consolidated and made honest.** The "why did you change this" box was originally one field pre-filled with a line-count summary ending in a dangling "Why: " - Zahir: "this seems like an incomplete message." Split into a read-only "What changed" line (real diff stats, never editable) plus a separate required "Why" box that only ever shows a placeholder, never a prefilled guess - Zahir: "why should have a reasoning right that is what we want to educate the use[r]." Also merged three previously separate buttons (Save review / Save tag / Save status) into one "Save status" button, and put the Why box, Strategy tag, and Mark status in one 3-column row instead of three stacked rows - Zahir: "in order to be frugal with space." **Suggested-answer prefills, and a real staleness bug fixed twice.** Resume clarifying questions and the Strategy tag field now both prefill with a Claude-proposed value (hedged/guessable facts for clarifying questions, e.g. "Roughly 8-10 engineers?"; a non-hedged drafting-choice label like "concise-2-page-ats-focused" for Strategy tag, via a new `suggested_strategy_tag` field on the resume schema) - but the Strategy tag box kept showing stale (blank) content after a regenerate, because Streamlit ignores a new `value=` once a widget `key` already has session state. Root-caused and fixed the same way for both the Strategy tag box and (proactively, before Zahir hit it) the clarifying-question boxes: fold the suggestion's own content into the widget `key` so a genuinely new suggestion gets a fresh box while an unchanged one keeps whatever he typed. |
|
| Prospector tab - score compute, signal-quality fixes, click-to-activate, website lookup (2026-07-31) | Built | 100% | **Prospector Score now actually computes.** The old "Prepare Prospector Score data" -> "go ask Claude Code" two-step (same friction Zahir already flagged once for document drafting) replaced with a single "Compute Prospector Score" button: new `prospector_score.compute_prospector_score()`, same deliberate direct-Anthropic-API exception as `tailoring/drafting.py`, reusing its `_client()`/`DEFAULT_MODEL`. **A real pharma signal-quality bug, found because Zahir recognized a wrong company.** He spotted UroGen showing as "watching" off what he knew was an old approval - checking the real data confirmed `regulatory_filings.py`'s "approval within 3 years" signal was structurally backwards (an already-approved company has already passed the moment it needs to build out corporate functions, not before it); deprecated the signal generation with a full postmortem docstring, and bulk-disqualified the 30 real accounts that existed only because of it. Asked Zahir for four more real examples to sanity-check the fix and found a SECOND bug: `clinical_trials.py` had **no recency filter at all** - a Phase 3 trial that completed in 2003 or 2005 passed exactly like one from last month. Two of his four examples (Gustave Roussy, a French cancer hospital; Radiation Therapy Oncology Group, a defunct trials cooperative) were also non-companies that slipped past `company_filters.py`'s keyword list; a third (VectivBio AG) was acquired by Ironwood Pharmaceuticals in 2023. Added a `STALE_YEARS=2` recency filter to `clinical_trials.py`, new `company_filters.py` keywords (`cancer campus`, `cooperative group`, `oncology group`, `vectivbio`), and disqualified all 8 real accounts affected (78 total accounts unchanged, only statuses corrected) - then caught 2 more (VectivBio's own record, missed by a crude first-pass cleanup script; AlgoRx Pharmaceuticals, a COMPLETED trial with no date at all) on a second, more rigorous sweep using the actual new filter logic instead of an ad-hoc check. **Disqualified accounts were invisible-but-not-hidden.** Zahir: "urogen and bravehear[t] bio are still in the list" - both were correctly marked disqualified, but disqualifying never hid a row, so it looked untouched; added a "Show N disqualified/stale account(s)" toggle (hidden by default, same pattern as the Results tab's "not interested" jobs) - a real off-by-one-list bug (row index pointing into the wrong, unfiltered list once the toggle existed) was caught and fixed before shipping. **Signals column and Company click.** Bare signal count replaced with a human-readable comma-joined list via a new `SIGNAL_TYPE_LABELS` map; Company column got the same ButtonColumn click-to-activate + auto-scroll treatment as the Results tab's Role column. **Website column.** New `prospector/company_lookup.py` (`lookup_company_website()`, same one-time-search-then-cache web-search pattern as the cover letter's address lookup) plus a `website` field on target-account records; an explicit "Look up website for N account(s)" button (never silent/automatic) since it's a real per-company API call. Real cost is computed from each response's actual usage and shown on the button itself as "($X.XX for the last run)" - Zahir asked for this reusable rather than one-off, so the cost math now lives in new top-level `src/api_cost.py` (`estimate_response_cost()`, Claude Opus 5 token pricing + the web_search tool's $10/1,000-search fee), meant to be reused by any future direct-API call site, not just this one. Small table-width cleanup alongside all of this: the Coverage/Activity/Outcome/Learn-Engine summary tables switched from `width="stretch"` to `width="content"` (tables holding real free text, like rejection reasons, kept `stretch`). |
|
| Appearance (color themes) + codemap + standing HCI practice (2026-07-31) | Built | 100% | Zahir asked for 4 color-scheme mockups (shown via the visualize tool - teal/blue/coral/slate-purple) plus a plain Light and a plain Dark theme, "added in config" so he could pick one. Built as 6 real Streamlit theme files under `.streamlit/themes/` (the 4 color ones each carry both `[theme.light]` and `[theme.dark]` sections, so Streamlit's own menu gives a native light/dark toggle within whichever color is chosen) plus a new "Appearance" section in Settings with a selectbox + "Apply theme" button that overwrites the live `.streamlit/config.toml` and reruns - confirmed working live (Zahir applied Coral himself mid-session; visible later in his own `config.toml`). Deliberately did not click "Apply theme" myself during testing since it's the same file his live app reads. Separately, Zahir asked to streamline token usage across sessions on this project - added `docs/codemap.md` (module map, a Mermaid data-flow diagram, a "where to look for X" table) as the first thing to read for orientation instead of re-exploring `src/` from scratch each session, with an explicit ask to keep it checked/updated at commit time. He also asked that HCI/UX principles be applied proactively rather than caught one at a time - added a section to this file's own project `CLAUDE.md` naming the concrete patterns from this session (stale prefilled widgets, avoidable extra clicks, scroll-to-result, space efficiency, hedged-vs-real prefills) as a standing check for every future UI change. |

| Website lookup - real bug fixed, and a new regression test suite (2026-07-31) | Built | 100% | **A real bug caught by Zahir doing his own Google search.** After the website-lookup batch ran (36 accounts, real $3.18 spent), he found only 2 of 36 populated and said he'd just Googled one himself and found `parabilismed.com` in seconds. Diagnosed directly rather than guessed: the model was actually finding the right URL most of the time, but occasionally prepending a short lead-in sentence despite the system prompt saying "ONLY the URL, no commentary" (e.g. `"I'll search for this company's official website.https://brainstorm-cell.com"`) - `company_lookup.py`'s validation rejected the ENTIRE response if it contained any space at all, throwing the real URL away along with the unwanted preamble. Fixed by extracting the URL with a regex pattern instead of requiring the whole reply to *be* the URL - confirmed against all three of Zahir's real failures live before shipping (one, Parabilis, actually already had a good search result on the earlier failed attempt too). Reset the 34 wrongly-empty `website` fields back to "not yet looked up" rather than re-spending his API budget automatically - the next "Look up website" click is his to trigger. **New regression test suite**, Zahir's explicit request: "create regression testing back test all that is developed and then keep on enhancing the test pack after new features are added." Scoped after confirming with him: pure deterministic logic only (filtering rules, file naming, cost math, status/gating logic, data-store CRUD), no real Anthropic API calls (drafting/lookups/Prospector Score) so the suite stays free and fast to run anytime. New `tests/conftest.py` isolates every data-store path constant to `tmp_path` per test (the project has a documented near-data-loss incident from a careless real-store write, so tests can't repeat that by construction) - 70 tests across 8 files covering `ranking/prioritize.py`, `prospector/kpis.py`, `prospector/company_filters.py`, `prospector/clinical_trials.py` (specifically regression-guarding the two real bugs found this session: the missing recency filter and the two non-company sponsors), `api_cost.py`, `tailoring/dossier.py`'s naming/migration functions, `search/job_store.py`, and `tailoring/applications.py`'s `needs_edit_review()` gate. All pass; confirmed the real `data/` stores were untouched (78 target accounts before and after). `pyproject.toml` added (`pythonpath = ["src"]` so `pytest` runs with no manual `PYTHONPATH`); `pytest` added to `requirements.txt`. `tests/README.md` documents the scope decision and the pattern for extending the suite as new features land. |

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

## 18. Applications Pivot Table (Designed 2026-07-30, not yet built)

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
