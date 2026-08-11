"""Local JSON-backed store for the `jobs` table (PRD §4) - no real database
for v0, matching the plain-JSON pattern already used for the master profile.
Dedupes by (source, job_id) so repeated searches don't create duplicates.
Encrypted at rest (PRD §7) via security.crypto_store.
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

LINKEDIN_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOBS_PATH = PROJECT_ROOT / "data" / "jobs" / "jobs.json"


def load_jobs() -> list[dict]:
    return read_json(JOBS_PATH, default=[])


def save_jobs(new_jobs: list[dict]) -> int:
    """Merges new_jobs into the store, keyed by (source, job_id). Returns the
    number of genuinely new jobs added (existing ones are left untouched).

    Stamps date_added on genuinely new records only (added 2026-07-30, PRD
    §16c - the Prospector KPI dashboard needs a discovery timestamp to
    report "jobs found this week"). Jobs saved before this date have no
    date_added and are counted in totals but not in any date-based slice -
    there's no real discovery date to recover for them."""
    with locked("jobs"):
        existing = load_jobs()
        seen = {(j.get("source"), j.get("job_id")) for j in existing}

        added = 0
        for job in new_jobs:
            key = (job.get("source"), job.get("job_id"))
            if key in seen:
                continue
            job.setdefault("date_added", datetime.now(timezone.utc).isoformat())
            existing.append(job)
            seen.add(key)
            added += 1

        write_json(JOBS_PATH, existing)
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
    save_jobs([job])
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
