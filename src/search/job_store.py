"""Local JSON-backed store for the `jobs` table (PRD §4) - no real database
for v0, matching the plain-JSON pattern already used for the master profile.
Dedupes by (source, job_id) so repeated searches don't create duplicates.
Encrypted at rest (PRD §7) via security.crypto_store.
"""

from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOBS_PATH = PROJECT_ROOT / "data" / "jobs" / "jobs.json"


def load_jobs() -> list[dict]:
    return read_json(JOBS_PATH, default=[])


def save_jobs(new_jobs: list[dict]) -> int:
    """Merges new_jobs into the store, keyed by (source, job_id). Returns the
    number of genuinely new jobs added (existing ones are left untouched)."""
    existing = load_jobs()
    seen = {(j.get("source"), j.get("job_id")) for j in existing}

    added = 0
    for job in new_jobs:
        key = (job.get("source"), job.get("job_id"))
        if key in seen:
            continue
        existing.append(job)
        seen.add(key)
        added += 1

    write_json(JOBS_PATH, existing)
    return added


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
