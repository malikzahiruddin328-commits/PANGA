"""Local JSON-backed store for the `jobs` table (PRD §4) - no real database
for v0, matching the plain-JSON pattern already used for the master profile.
Dedupes by (source, job_id) so repeated searches don't create duplicates.
Encrypted at rest (PRD §7) via security.crypto_store.
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from search import exclusion_filter
from security.crypto_store import read_json, write_json
from security.file_lock import locked

LINKEDIN_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOBS_PATH = PROJECT_ROOT / "data" / "jobs" / "jobs.json"


def load_jobs() -> list[dict]:
    return read_json(JOBS_PATH, default=[])


def save_jobs(new_jobs: list[dict], apply_exclusion: bool = True, review_required: bool = True) -> int:
    """Merges new_jobs into the store, keyed by (source, job_id). Returns the
    number of genuinely new jobs added (existing ones are left untouched).

    Stamps date_added on genuinely new records only (added 2026-07-30, PRD
    §16c - the Prospector KPI dashboard needs a discovery timestamp to
    report "jobs found this week"). Jobs saved before this date have no
    date_added and are counted in totals but not in any date-based slice -
    there's no real discovery date to recover for them.

    Search-time exclusion (2026-08-12, search.exclusion_filter) runs as an
    earlier gate before the (source, job_id) dedup check below, which is
    otherwise unchanged: a job matching a predictably-poor-fit title
    pattern (IC-tier seniority with no executive qualifier, or a
    clinical/medical domain role) is never written to jobs.json at all, so
    it never reaches tailoring.fit_score's paid call. Per Zahir's
    non-negotiable "never silently dropped" rule, every exclusion is still
    logged to data/jobs/search_exclusion_log.json - see
    exclusion_filter.log_exclusions() - logged AFTER the "jobs" lock is
    released below, under its own separate lock, so this never holds two
    locks at once.

    apply_exclusion=False (used by add_manual_job() below) deliberately
    bypasses all of the above. This module already has a standing, explicit
    rule for one add_manual_job() caller - scripts/job_alert_scan.py's
    email-digest extraction - that every listing found must be added, never
    skipped for looking like the wrong industry/vertical/domain (Zahir's
    explicit 2026-08-06 instruction; see this repo's CLAUDE.md,
    "Processing job-alert emails into job records"): a dropped-at-intake
    job never reaches him to evaluate at all, unlike a merely low-scored
    one. The title-pattern exclusion this module adds is exactly that kind
    of intake-time skip, so it must never apply to add_manual_job()'s path
    (email-digest listings AND Zahir's own manual LinkedIn-paste UI, which
    has the same problem for a different reason - he chose that specific
    posting himself, so silently refusing to save it would be a confusing,
    unexplained UI failure). It's scoped to apply only to the automated
    search channels (USAJOBS, ZipRecruiter, Dice, Indeed, company sites,
    industry boards) that this feature was built for.

    review_required (2026-08-13, basket/review-gate build): stamps
    review_status="pending" on genuinely new records when True (the
    default - every source connector, USAJOBS/Dice/company-sites/etc.,
    calls save_jobs() with no override, so a fresh search result never
    reaches scoring until Zahir explicitly accepts it in the Results
    tab's review UI - see ui/app.py's "Review new search result(s)"
    section and scripts/run_search.py's score_unscored_jobs(), both of
    which only score review_status == "accepted" jobs).
    add_manual_job() below passes both apply_exclusion=False and
    review_required=False - a job Zahir pastes in himself (or
    job_alert_scan.py extracts) is already a considered choice, not a
    broad-net search hit, so gating it behind a second manual accept
    click would be pure friction with no real review value, on top of
    it already being exempt from the exclusion filter above. A job saved
    before this field existed has no review_status at all - every reader
    of this field must treat a missing key as "accepted" (the implicit
    historical default), never as "pending", or every job ever saved
    before 2026-08-13 would silently vanish behind an unintended review
    gate."""
    to_log: list[tuple[dict, dict]] = []
    with locked("jobs"):
        existing = load_jobs()
        seen = {(j.get("source"), j.get("job_id")) for j in existing}

        added = 0
        for job in new_jobs:
            if apply_exclusion:
                exclusion = exclusion_filter.check_exclusion(job)
                if exclusion:
                    to_log.append((job, exclusion))
                    continue
            key = (job.get("source"), job.get("job_id"))
            if key in seen:
                continue
            job.setdefault("date_added", datetime.now(timezone.utc).isoformat())
            job.setdefault("review_status", "pending" if review_required else "accepted")
            existing.append(job)
            seen.add(key)
            added += 1

        write_json(JOBS_PATH, existing)

    exclusion_filter.log_exclusions(to_log)
    return added


def add_manual_job(
    title: str,
    organization: str,
    location: str,
    description: str,
    posting_url: str,
    source: str = "linkedin",
) -> dict:
    """Creates a job record for a posting the user found himself (PRD §13
    LinkedIn manual intake) rather than one an automated search channel
    found. Unlike the other channels, `description` is captured and stored
    up front - USAJOBS/Indeed/etc. jobs get their JD fetched live from
    `posting_url` at tailoring time instead, but LinkedIn URLs often can't be
    reliably refetched later (login wall/bot-check), so this is the one
    exception.

    job_id is derived from posting_url so re-adding the same posting later
    (e.g. pasting the URL again) dedupes correctly via save_jobs(): LinkedIn
    URLs carry the real job ID in the path (/jobs/view/<digits>/), which
    stays stable even when tracking query params differ between pastes: a
    hash of the full URL would treat those as different jobs.

    A blank posting_url falls back to hashing title+organization+
    description instead of the (empty) URL - found 2026-08-08 via a manual
    backlog catch-up run: hashing "" always produces the same job_id, so
    two genuinely different listings that both happened to have no URL
    would collide and save_jobs()'s (source, job_id) dedup would silently
    drop the second one as a "duplicate" of the first. This caller's own
    UI form requires posting_url, and scripts/job_alert_scan.py already
    skips any extracted listing with no posting_url before ever reaching
    here - so this wasn't observed to actually collide in practice - but
    a future caller with no such guard would hit it silently, so it's
    fixed at the source rather than left as an implicit assumption only
    today's two callers happen to uphold.
    """
    match = LINKEDIN_JOB_ID_RE.search(posting_url)
    if match:
        job_id = match.group(1)
    elif posting_url:
        job_id = hashlib.sha256(posting_url.encode()).hexdigest()[:16]
    else:
        job_id = hashlib.sha256(f"{title}|{organization}|{description}".encode()).hexdigest()[:16]

    job = {
        "source": source,
        "job_id": job_id,
        "title": title,
        "organization": organization,
        "location": location,
        "description": description,
        "posting_url": posting_url,
    }
    # apply_exclusion=False, review_required=False: see save_jobs()'s own
    # docstring - this path (Zahir's manual paste UI AND job_alert_scan.py's
    # email-digest extraction) is explicitly exempt from both search-time
    # exclusion and the review gate.
    save_jobs([job], apply_exclusion=False, review_required=False)
    return job


def update_job_address(source: str, job_id: str, address: str) -> None:
    """Caches a company's real mailing address (found via a one-time web
    search in tailoring/drafting.py, for the cover letter's recipient
    block) on the job record so it isn't re-searched on every regenerate.
    "" is a valid cached value meaning "searched, genuinely not found" -
    distinct from the key being absent, which means "never searched yet"."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job["organization_address"] = address
                break
        write_json(JOBS_PATH, jobs)


def update_job_ats_keywords(
    source: str, job_id: str, required_keywords: list[str], preferred_keywords: list[str],
    extractor_version: int | None = None,
) -> None:
    """Caches the AI-extracted required/preferred ATS keyword list for this
    job (tailoring/drafting.py's _extract_ats_keywords - one real-NLP-
    judgment call over the posting's own text) so the same posting always
    scores against the same keyword list rather than re-extracting (and
    potentially drifting) on every resume regenerate. Same
    cache-on-the-job-record shape as update_job_address(); an empty list is
    a valid cached value meaning "extracted, genuinely no such keywords" -
    tailoring/drafting.py only calls this on a successful extraction, never
    on a failed/unconfigured API call, so a transient failure doesn't
    permanently freeze a job at "no keywords found".

    extractor_version (2026-08-10) - tailoring.drafting.ATS_KEYWORDS_
    EXTRACTOR_VERSION at the time of this extraction, stamped alongside the
    keywords so a later caller can tell "extracted, but under an older,
    possibly-corrected extraction/cleanup pipeline" apart from "extracted
    under the current one" (see tailoring.drafting.is_ats_keywords_stale) -
    optional/None stays backward-compatible with any caller that doesn't
    supply one, rather than forcing every call site to know about this."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job["ats_required_keywords"] = required_keywords
                job["ats_preferred_keywords"] = preferred_keywords
                if extractor_version is not None:
                    job["ats_keywords_extractor_version"] = extractor_version
                break
        write_json(JOBS_PATH, jobs)


def update_job_description(source: str, job_id: str, description: str) -> None:
    """Backfills real JD text onto an already-saved job record (2026-08-06:
    company_sites.py's Workday/SmartRecruiters searches now capture this at
    search time for new jobs, via scripts/backfill_jd_text.py for jobs
    saved before that fix existed).

    Also clears any previously-cached ats_required_keywords/
    ats_preferred_keywords: those were computed from empty text before this
    backfill ran, and drafting.py's _extract_ats_keywords() only re-attempts
    extraction when those keys are absent, not when they're merely an empty
    list (a real, deliberately "sticky" cache - see its own docstring) - so
    without clearing them here, a backfilled job would keep looking like
    extraction was already tried and genuinely found nothing, even though
    real text is now available. Clearing them lets the next resume
    regenerate re-extract for real."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job["description"] = description
                job.pop("ats_required_keywords", None)
                job.pop("ats_preferred_keywords", None)
                break
        write_json(JOBS_PATH, jobs)


def flag_employer_attribution_uncertain(source: str, job_id: str, likely_organization: str | None = None) -> None:
    """Marks a job's `organization` field as possibly wrong (2026-08-07,
    real bug found in production data: an Indeed posting for a life-
    sciences IT role was attributed to "Suncoast Credit Union" - the JD
    body clearly said "About Celldex..." throughout, a real biopharma
    company with nothing to do with the stated employer. Confirmed via
    Indeed's own get_job_details tool, not a Panga-side parsing bug - the
    field is wrong at the source). likely_organization is the name the JD
    text itself suggests, if one was found, for display alongside the
    caution - None means "flagged as suspect, but no better guess found."
    Surfaced in the Results tab as a caution note, not auto-corrected -
    same "let Zahir judge for himself" principle as fit_score, since a
    heuristic guess at the real employer could itself be wrong."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job["employer_attribution_uncertain"] = True
                job["likely_organization"] = likely_organization
                break
        write_json(JOBS_PATH, jobs)


def flag_freshness_check_downgraded(targets: list[tuple[str, str]], reason: str) -> int:
    """Marks freshness_check_downgraded=True + freshness_check_downgrade_reason
    on every (source, job_id) pair in `targets` - one locked read-modify-
    write for the whole batch, not one per job (avoid an O(n) full-file
    rewrite per flag on what can be a multi-job batch - see CLAUDE.md's
    performance check). Returns how many were actually found and flagged
    (targets naming a job that's since been removed/never existed are
    silently skipped, not an error).

    Used when a company is removed from job_sources.yaml (2026-08-10,
    Zahir's explicit product call on a real gap found in Mirror's audit):
    the company's own postings lose their fast/reliable platform-API
    freshness check the moment it leaves the config (see
    freshness_check.py's build_api_source_lookup()) and fall back to a
    generic page-fetch check - that already happened implicitly with no
    visible signal to Zahir. This makes it explicit and visible (a Results
    tab caution, same pattern as flag_employer_attribution_uncertain()
    above) for exactly the affected set - see
    ranking.prioritize.find_freshness_downgrade_targets() for how that set
    is computed (the company's postings AND their known cross-source
    duplicates, not a blanket downgrade of everything associated with the
    company)."""
    with locked("jobs"):
        jobs = load_jobs()
        target_set = set(targets)
        flagged = 0
        for job in jobs:
            key = (job.get("source"), job.get("job_id"))
            if key in target_set:
                job["freshness_check_downgraded"] = True
                job["freshness_check_downgrade_reason"] = reason
                flagged += 1
        write_json(JOBS_PATH, jobs)
    return flagged


def set_review_status(source: str, job_id: str, status: str) -> None:
    """Moves a job out of the "pending" review gate save_jobs() puts every
    fresh source-connector result into (2026-08-13). `status` is
    "accepted" (proceeds to the normal scoring/ranking pipeline the next
    time score_unscored_jobs() runs) or "rejected" (stays in the store -
    same hide-but-never-delete pattern as
    application_status "not interested"/"closed by employer" - but is
    permanently excluded from scoring and from the Results tab's ranked
    list, since a rejected job was never even judged worth scoring in the
    first place). Silently a no-op if the (source, job_id) pair no longer
    exists - same defensive shape as update_job_score()/update_job_
    address() above, not an error, since a job could theoretically be
    reviewed from a stale page render after being removed some other way."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job["review_status"] = status
                break
        write_json(JOBS_PATH, jobs)


def add_to_basket(source: str, job_id: str) -> None:
    """Marks a job as in the basket (2026-08-13 basket build). Basket
    membership is stored on the job record itself, not in Streamlit
    session_state, so it survives page reloads/app restarts the same way
    every other piece of job state does (fit_score, employer_attribution_
    uncertain, etc.) - a session-state-only basket would silently empty
    itself on every browser refresh, which is a real, easy-to-hit trap for
    anything meant to hold state across a "come back to this later"
    workflow like generating documents for several jobs at once."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job["in_basket"] = True
                break
        write_json(JOBS_PATH, jobs)


def remove_from_basket(source: str, job_id: str) -> None:
    """Inverse of add_to_basket() above. Deletes the key entirely rather
    than setting it False - keeps every basket-membership check a plain
    `job.get("in_basket")` truthy check (matches this store's existing
    convention: employer_attribution_uncertain/freshness_check_downgraded
    are also only ever set True and otherwise simply absent, never set
    False)."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job.pop("in_basket", None)
                break
        write_json(JOBS_PATH, jobs)


def update_job_score(source: str, job_id: str, fit_score: int, fit_rationale: str) -> None:
    """fit_score is 0-100: how well this job matches the master profile,
    per PRD §3 "Fit + Tailoring". Computed by Claude reasoning over the job
    + master profile, not a keyword heuristic - this function only persists
    the result, matching the mechanical/reasoning split used elsewhere
    (interview.py, tailor.py)."""
    with locked("jobs"):
        jobs = load_jobs()
        for job in jobs:
            if job.get("source") == source and job.get("job_id") == job_id:
                job["fit_score"] = fit_score
                job["fit_rationale"] = fit_rationale
                break
        write_json(JOBS_PATH, jobs)
