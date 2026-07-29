"""Local JSON-backed store for the `applications` table (PRD §4): one record
per job the user has started tailoring or set a status on. Status values per
PRD §9: drafted, reviewed, submitted-by-user, not-interested, save-for-later.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS_PATH = PROJECT_ROOT / "data" / "applications" / "applications.json"


def load_applications() -> list[dict]:
    if not APPLICATIONS_PATH.exists():
        return []
    return json.loads(APPLICATIONS_PATH.read_text(encoding="utf-8"))


def _save_all(applications: list[dict]) -> None:
    APPLICATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPLICATIONS_PATH.write_text(json.dumps(applications, indent=2), encoding="utf-8")


def get_application(source: str, job_id: str) -> dict | None:
    for app in load_applications():
        if app["source"] == source and app["job_id"] == job_id:
            return app
    return None


def upsert_application(
    source: str,
    job_id: str,
    status: str,
    resume_text: str | None = None,
    cover_letter_text: str | None = None,
    skip_reason: str | None = None,
) -> None:
    """Creates or updates the application record for (source, job_id).
    Fields left as None don't overwrite previously saved values."""
    applications = load_applications()
    for app in applications:
        if app["source"] == source and app["job_id"] == job_id:
            app["status"] = status
            if resume_text is not None:
                app["resume_text"] = resume_text
            if cover_letter_text is not None:
                app["cover_letter_text"] = cover_letter_text
            if skip_reason is not None:
                app["skip_reason"] = skip_reason
            _save_all(applications)
            return

    applications.append({
        "source": source,
        "job_id": job_id,
        "status": status,
        "resume_text": resume_text,
        "cover_letter_text": cover_letter_text,
        "skip_reason": skip_reason,
    })
    _save_all(applications)
