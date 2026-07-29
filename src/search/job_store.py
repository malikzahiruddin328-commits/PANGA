"""Local JSON-backed store for the `jobs` table (PRD §4) - no real database
for v0, matching the plain-JSON pattern already used for the master profile.
Dedupes by (source, job_id) so repeated searches don't create duplicates.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOBS_PATH = PROJECT_ROOT / "data" / "jobs" / "jobs.json"


def load_jobs() -> list[dict]:
    if not JOBS_PATH.exists():
        return []
    return json.loads(JOBS_PATH.read_text(encoding="utf-8"))


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

    JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOBS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return added
