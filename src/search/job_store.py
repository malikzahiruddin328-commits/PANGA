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
    """
    match = LINKEDIN_JOB_ID_RE.search(posting_url)
    job_id = match.group(1) if match else hashlib.sha256(posting_url.encode()).hexdigest()[:16]

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


def update_job_score(source: str, job_id: str, fit_score: int, fit_rationale: str) -> None:
    """fit_score is 0-100: how well this job matches the master profile,
    per PRD §3 "Fit + Tailoring". Computed by Claude reasoning over the job
    + master profile, not a keyword heuristic - this function only persists
    the result, matching the mechanical/reasoning split used elsewhere
    (interview.py, tailor.py)."""
    jobs = load_jobs()
    for job in jobs:
        if job.get("source") == source and job.get("job_id") == job_id:
            job["fit_score"] = fit_score
            job["fit_rationale"] = fit_rationale
            break
    write_json(JOBS_PATH, jobs)
