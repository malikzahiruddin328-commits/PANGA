# Panga codemap

Read this first, before grepping/exploring `src/` from scratch. It's a
navigation aid, not documentation of behavior - for the "why" behind a
feature, see `docs/job-search-automation-prd.md` §13 (backlog log, most
recent entries at the bottom) or the module's own docstring.

## Data flow, top to bottom

```mermaid
flowchart TD
    A[Job boards / USAJOBS / company sites / LinkedIn manual entry] --> B[search/job_store.py<br/>jobs.json]
    B --> C[ranking/prioritize.py<br/>fit_score, dedup]
    C --> D[ui/app.py Results tab]
    D -->|Generate documents click| E[tailoring/drafting.py<br/>direct Anthropic API call]
    E --> F[tailoring/applications.py<br/>applications.json]
    E --> G[tailoring/docx_export.py<br/>.docx bytes]
    G --> H[tailoring/dossier.py<br/>per-application workspace folder]
    F --> H
    D -->|status change / edits| F
    F --> I[tailoring/dossier.py<br/>dossier.md summary]
    D --> J[tailoring/cta_emails.py<br/>Gmail call-to-action scan]
    D --> K[tailoring/interview_prep.py]
    D --> L[prospector/*<br/>target accounts, outreach, KPIs, Prospector Score]
    M[profile/ingest.py + profile/interview.py] --> N[profile/storage.py<br/>master_profile.json]
    N --> E
    N --> C
```

## Module map (one line each)

**search/** - finding and storing jobs
- `job_store.py` - the `jobs` table (JSON, encrypted). `load_jobs`/`save_jobs`/`update_job_score`/`update_job_address`.
- `usajobs.py`, `boards.py`, `industry_boards.py`, `company_sites.py` - one source-channel each, all normalize into the same job shape.

**ranking/**
- `prioritize.py` - sorts/dedupes the combined job list (fit_score, role-weight, cross-source dedup).

**profile/** - the candidate's own data
- `storage.py` - `load_profile`/master_profile.json, encrypted.
- `ingest.py` - parses uploaded resume/context .docx/.pdf into the master profile.
- `interview.py` - gap-probing interview engine; `save_answer()` feeds `gap_interview_answers`, also the landing spot for drafting's clarifying-question answers.

**tailoring/** - everything about one application
- `drafting.py` - the ONE direct-Anthropic-API module (deliberate exception to "Python orchestrates, Claude reasons live"). `generate_documents()` drafts resume/cover_letter/exec_bio/leadership_summary/apply_answers; resume also gets ATS score + clarifying questions + suggested strategy tag; cover letter triggers a one-time web-search company-address lookup (cached on the job record).
- `applications.py` - the `applications` table (JSON, encrypted). `upsert_application`, status suggestions, edit-review gate (`needs_edit_review`/`record_document_edit_review`).
- `dossier.py` - per-application workspace folder under `data/applications/dossiers/{org-title-slug}-{hash}/` - plain (unencrypted) `.docx` files named `Name_DocType_Role_Company.docx` plus `dossier.md`. `sync_workspace_documents()` writes them on (re)generate with edit-preserving backups; `check_for_edits()` diffs Zahir's hand-edited copy back against the stored draft text.
- `docx_export.py` - plain text -> styled `.docx` bytes (resume style vs. cover-letter style are different renderers).
- `interview_prep.py` - interview rounds/interviewer research store.
- `cta_emails.py` - Gmail call-to-action store (offers/interview requests/rejections/etc., populated by the external `panga-gmail-cta-scan` scheduled task, not by this app).
- `tailor.py` - older/lower-level JD+profile drafting helper (mostly superseded by `drafting.py`'s one-click flow - check before extending, it may be legacy).

**prospector/** - proactive search (target accounts, outreach, self-scoring)
- `target_accounts.py`, `outreach.py`, `kpis.py`, `learn_engine.py`, `rejection_diagnosis.py` - the KPI/self-learning layer.
- `prospector_score.py` - Coverage/Activity/Outcome summary gathering PLUS `compute_prospector_score()`, a direct-Anthropic-API call (same deliberate exception as `tailoring/drafting.py`) that computes the actual score - no more "prepare data, go ask Claude Code" two-step.
- `clinical_trials.py`, `commercial_hiring.py`, `funding_filings.py`, `company_filters.py` - signal sources that feed target_accounts; `clinical_trials.py` filters out trials with a stale (2+ year) primary completion.
- `regulatory_filings.py` - DEPRECATED as a target-account signal source (2026-07-31) - "already approved" pointed the wrong direction for prospecting; read its module docstring before reusing.
- `company_lookup.py` - one-time web-search lookup of a target account's real company website (`lookup_company_website()`), cached via `target_accounts.set_website()`.

**src/api_cost.py** - `estimate_response_cost(response, model)`, real dollar cost of one direct-API call from its actual `usage` + web-search count. Reusable across every direct-API call site (drafting.py, company_lookup.py, prospector_score.py); use it instead of re-deriving token math per module.

**linkedin/** - the one channel with no public search API
- `ingest.py`/`connections.py` - mechanical PDF/CSV parsing of user-provided exports.
- `storage.py`/`connections_store.py` - encrypted stores for the above.
- `enhance.py` - profile-strength scoring/suggestions (same "score + why + how to raise it" shape as everything else).

**security/**
- `crypto_store.py` - AES-256-GCM read_json/write_json used by every store above EXCEPT `dossier.py`'s workspace files (Zahir's explicit call - needs to be directly Word-editable).

**ui/**
- `app.py` - the whole Streamlit app, one file, tabs switched via `active_tab` session state. Long - use the section headers (`elif active_tab == "..."`) to jump, don't read top to bottom.
- `feedback_widget.py` - the point-and-talk feedback widget embedded on every tab.

**feedback/**, **skills/** - small support modules (voice transcription, UI feedback store, industry/role skill lookup table).

## Where to look for X

| Task | Start here |
|---|---|
| A job isn't showing / scored wrong | `search/job_store.py`, `ranking/prioritize.py` |
| Document drafting behavior/prompts | `tailoring/drafting.py` (`DOC_SPECS`, `SYSTEM_PROMPT`, schemas) |
| Anything about the per-application folder / edit tracking | `tailoring/dossier.py` |
| A Results-tab UI change | `src/ui/app.py`, search for `active_tab == "results"` |
| Application status / gates on marking "applied" | `tailoring/applications.py` (`needs_edit_review`) |
| Outreach / target accounts / Prospector Score | `prospector/` |
| Encryption questions | `security/crypto_store.py` - note `tailoring/dossier.py` is the one deliberate exception |
| "Is this already built?" | `docs/job-search-automation-prd.md` §13, bottom rows are most recent |

## Streamlining sessions (why this file exists)

Zahir's ask (2026-07-31): reduce token spend from re-exploring the same
ground every session. Practice going forward:
1. Read this file first for orientation instead of Glob/Grep sweeps across
   `src/`.
2. Only open the specific module(s) the table above points to, not the
   whole app.
3. Update this file's module map when a module's *purpose* changes (new
   file, module repurposed) - not on every small edit. Keep it to one line
   per module; put the "why"/history in the PRD backlog table instead.
   Check it as part of every commit that adds/removes/repurposes a module
   or changes the data flow (Zahir's explicit ask 2026-07-31) - a stale
   codemap defeats the point of reading it first.
4. For memory: the session's auto-memory project file
   (`project_panga_job_search_tool.md`) should stay a log of decisions and
   open threads, not a restatement of what's in this codemap or the PRD -
   if something's derivable from either, don't duplicate it into memory.
