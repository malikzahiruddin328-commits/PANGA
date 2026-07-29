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
- User sets a passphrase correctly at first setup; this is the only key for now.
- **Backlog**: real recovery mechanism (e.g. recovery code, or a support-assisted decrypt flow for a future multi-user version) — deferred. For now, losing the passphrase means losing the profile, so this is a real risk to be aware of during v0 use.
- **v0 kickoff decision (2026-07-27)**: encryption implementation itself is deferred to backlog for this phase (see §13) — `data/` still stays local-only and gitignored (never committed to version history), but the profile file is stored unencrypted at rest for now. Revisit before this leaves this machine or if others gain access to this Windows account.

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
- **Interview prep module** — added to backlog for a later phase (once core search/tailor loop is stable).

## 11. LLM Architecture
- **v0: paid-only, Claude via Claude Code.** No local LLM. Rationale: at this usage scale (single user, 2-3 scheduled runs/day + on-demand), token cost is not the likely bottleneck — search/scraping calls are. A local LLM stack (e.g. Ollama) adds a second system to install/maintain, which is a poor tradeoff for a non-developer user.
- **Backlog: hybrid local (free) + paid model split.** Revisit only if/when this scales toward a funded, multi-user product — split lightweight mechanical work (dedup, basic filtering) to a local model, keep quality-sensitive work (tailoring, gap-probing, fit scoring) on the paid model.

## 12. Multi-User Scope
- **v0 is single-user**, designed for the current user only (local-only encrypted storage, per §7).
- Multi-user support is a **later-stage aspiration**, contingent on validating the concept (e.g. VC backing) — not a near-term build target. Architecture should avoid unnecessary lock-in to single-user assumptions where it's free to do so, but should not be over-engineered for scale it doesn't need yet.

## 13. Backlog Additions
- **MCP connector pipeline**: as new job-board/company-data MCP connectors become available, follow a test → validate → productionalize process before relying on them in the live workflow (don't wire in unvetted connectors directly).
- **Industry-specific job boards**: beyond generic boards (Indeed, LinkedIn, etc.), maintain an extensible list of specialized boards per industry (e.g. life sciences/pharma-specific boards) — same "ever-growing lookup" pattern as the industry/role/skill table in §4.
- **Interview prep module** (carried over from §10).
- **Passphrase recovery mechanism** (carried over from §7).
- **Email monitoring — simple-scan version BUILT 2026-07-28** (moved up from backlog at the user's request — see `docs/email-monitoring-task.md`): a scheduled Claude task (`panga-gmail-cta-scan`, 4x/day) scans Gmail for call-to-action emails (interview invites, rejections, recruiter follow-ups) and sends a push notification when it finds one, using Gmail labels for state instead of a database. Flags emails generically — does NOT yet link a flagged email to a specific job record. **Still backlogged:** the richer version (tagging each flagged email to its specific `applications` table row and surfacing it in the results UI) still depends on the `applications` table, so it can't be built before search + tailoring + results UI exist (steps 4c/5/6).
- **Non-applied-job feedback loop — BUILT 2026-07-29** (added 2026-07-27): when the user marks a job "not interested" (§9), a reason can be given via the skip-reason field (§4); the job is hidden from Results either way (checkbox to unhide, nothing deleted). A reason marks the record for review (`applications.get_unreviewed_skip_reasons()`); the daily scheduled task detects and surfaces the count but does not evaluate reasons itself — that needs live back-and-forth with Claude (what the reason implies for future search params, presented as ranked options with a recommendation), done in conversation, then `mark_skip_reason_reviewed()` once walked through.
- **LinkedIn profile enhancement** (added 2026-07-27): cross-examine the resume/master profile against the user's LinkedIn profile and suggest improvements, including a collaborative marketing-style graphic-generation step. **Safe approach only**: the user manually pastes or exports their LinkedIn profile text into the tool — no automated login or scraping of linkedin.com, since that would violate LinkedIn's Terms of Service and risk account restriction (same class of risk already excluded for auto-submission in §2). Steps for the user must be written as plainly as possible ("idiot-proof"), consistent with §6's non-developer constraint.
- **Recruiter mail-blast / marketing outreach** (added 2026-07-27): send marketing material (e.g. about the user's candidacy) to recruiter contacts the user has accumulated. This sends messages to real third parties on the user's behalf — every use requires the user's explicit, per-instance sign-off (reviewing the message and recipient list before anything sends), never automatic or scheduled without confirmation.
- **Industry-specific job boards — Category B, public postings** (added 2026-07-28): 18 pharma-specific job boards and life-sciences/IT recruitment-firm career pages researched and captured in `config/industry_job_boards.yaml` (Planet Pharma, BioSpace, RAPS Career Center, FierceBiotech Jobs, Life Search Technologies, TSP Life Sciences, and others), status `candidate` — none have a documented API, so building against any of them means fetching public search-result pages (no login, low ToS risk, but fragile). Prioritize by relevance to CIO/Head-of-IT roles when this gets built.
- **Paid executive candidate-network membership — Category A** (added 2026-07-28): BlueSteps ($329 one-time + $89/yr, feeds profile to 16,000+ AESC-vetted retained-search recruiters) and ExecuNet (free profile, ~$39+/mo for full access) work in reverse from a job board — recruiters search the user's profile rather than the user searching listings, since much of the C-suite/VP-level market is filled via confidential retained searches never posted publicly. Not something Panga automates — the user creates/pays for the profile themselves. **Status: user is checking with personal HR contacts before committing spend** — revisit once that feedback is in.
- **Retained executive search firm outreach** (added 2026-07-28, sourced from a personal email from Dave Walko/PharmTALENT — a 10+ year friend of the user's, not a cold contact): Korn Ferry, Heidrick & Struggles, Spencer Stuart, Russell Reynolds (top-tier global retained search), plus CIO-specific boutique CIO Partners, The Good Search, Cowen Partners, the IT LeaderBoard profile registration, and the Pinnacle Society recruiter directory (searchable by keyword, e.g. CIO/IT). Same non-automatable shape as Category A above — these firms fill roles confidentially from their own network, there's nothing for Panga to search, the action is the user personally reaching out/registering. Captured in `config/industry_job_boards.yaml` under `retained_search_firms`. **Claude: prompt the user about this backlog item once the first working version of Panga (steps 4c/5/6) is done** — the user explicitly asked to be reminded then rather than act on it now.
- **Embed direct LLM API calls in the app, replacing Claude Code orchestration for reasoning tasks — FINAL backlog item, added 2026-07-29**: today, all reasoning (scoring, tailoring, feedback-loop evaluation) runs through Claude Code (this conversation + scheduled tasks), with Streamlit as a thin display/data-entry layer — a deliberate §11 decision, not a limitation of Streamlit itself (any framework has the same non-issue; the real lever is whether the app calls an LLM API directly). Explicitly sequenced LAST, evaluated only once the core loop and other backlog items are settled, because: (1) single-user + evolving requirements + async-acceptable latency + flat-subscription cost all favor the current setup for now, and every one of those signals flips once this goes multi-user (§12's own stated trigger for bigger architecture questions); (2) building the API integration now (separate billing, cost monitoring, rate limiting, error handling) would be complexity spent solving a problem that doesn't exist yet — nothing today is waiting on instant in-app reasoning. When revisited: cost-assess actual API usage volume against the current flat-subscription cost first; prefer the smaller move (direct API calls added to the same Streamlit app) over the larger one (full framework replacement), and only consider replacing Streamlit itself if the trigger is multi-user scale or UI needs Streamlit's component model can't meet — not the API-calling question, which is separable.
