"""Local JSON-backed store for the `applications` table (PRD §4): one record
per job the user has started tailoring or set a status on. Status values in
practice: "under review" (set automatically by "Start tailoring" - the app
has no way to know a job was actually submitted), "applied" (the user
confirms this themselves, or accepts a suggestion below), "not interested",
"save for later".
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
    Fields left as None don't overwrite previously saved values. Setting a
    skip_reason marks it unreviewed (skip_reason_reviewed=False) - Claude
    evaluates unreviewed reasons for what they imply about future searches
    (per PRD §13's non-applied-job feedback loop) and marks them reviewed
    via mark_skip_reason_reviewed()."""
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
                app["skip_reason_reviewed"] = False
            _save_all(applications)
            return

    applications.append({
        "source": source,
        "job_id": job_id,
        "status": status,
        "resume_text": resume_text,
        "cover_letter_text": cover_letter_text,
        "skip_reason": skip_reason,
        "skip_reason_reviewed": False if skip_reason is not None else None,
    })
    _save_all(applications)


def mark_skip_reason_reviewed(source: str, job_id: str) -> None:
    applications = load_applications()
    for app in applications:
        if app["source"] == source and app["job_id"] == job_id:
            app["skip_reason_reviewed"] = True
            _save_all(applications)
            return


def get_unreviewed_skip_reasons() -> list[dict]:
    return [a for a in load_applications() if a.get("skip_reason") and a.get("skip_reason_reviewed") is False]


def suggest_status(source: str, job_id: str, suggested_status: str, reason: str) -> None:
    """Claude calls this (from the Gmail scan) when an email looks like an
    application-confirmation match for a job currently "under review" - it
    does NOT change the real status. The user confirms or rejects it
    (confirm_status_suggestion), since matching an email to the right job
    record is a best guess, not a certainty (e.g. duplicate-titled postings)."""
    applications = load_applications()
    for app in applications:
        if app["source"] == source and app["job_id"] == job_id:
            app["suggested_status"] = suggested_status
            app["suggested_status_reason"] = reason
            _save_all(applications)
            return


def get_pending_status_suggestions() -> list[dict]:
    return [a for a in load_applications() if a.get("suggested_status")]


def confirm_status_suggestion(source: str, job_id: str, accept: bool) -> None:
    """accept=True applies the suggested_status as the real status; either
    way, clears the suggestion so it isn't asked about again."""
    applications = load_applications()
    for app in applications:
        if app["source"] == source and app["job_id"] == job_id:
            if accept and app.get("suggested_status"):
                app["status"] = app["suggested_status"]
            app["suggested_status"] = None
            app["suggested_status_reason"] = None
            _save_all(applications)
            return
