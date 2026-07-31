"""Local JSON-backed store for the `applications` table (PRD §4): one record
per job the user has started tailoring or set a status on. Status values in
practice: "under review" (set automatically by "Start tailoring" - the app
has no way to know a job was actually submitted), "applied" (the user
confirms this themselves, or accepts a suggestion below), "not interested",
"save for later", plus three added 2026-07-30 (PRD §16c/§17 - needed so the
Prospector KPI dashboard and Learn Engine have real outcomes to compute
rates from, not just applied/not-interested): "interview scheduled",
"offer", "rejected". All are free-form strings, not an enforced enum -
suggest_status()/confirm_status_suggestion() below accept any status value,
so adding these required no code change here, only in the Gmail scan
(panga-gmail-cta-scan) that proposes them and the Streamlit dropdown that
lets Zahir set them manually. Records also carry created_at (set once) and
status_updated_at (bumped only when status actually changes, not on every
upsert - added 2026-07-30, PRD §16c) so the Prospector KPI dashboard has
timestamps to slice "activity" by; records created before that date have
neither field. Encrypted at rest (PRD §7) via security.crypto_store.
"""

from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS_PATH = PROJECT_ROOT / "data" / "applications" / "applications.json"


def load_applications() -> list[dict]:
    return read_json(APPLICATIONS_PATH, default=[])


def _save_all(applications: list[dict]) -> None:
    write_json(APPLICATIONS_PATH, applications)


def _write_dossier(source: str, job_id: str) -> None:
    # Lazy import - dossier.py reads from this module, so importing it at
    # the top would be circular. Safe here since both modules are fully
    # loaded by the time any of these functions actually runs.
    from tailoring.dossier import write_dossier
    write_dossier(source, job_id)


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
    exec_bio_text: str | None = None,
    leadership_summary_text: str | None = None,
    resume_ats_score: int | None = None,
    resume_ats_rationale: str | None = None,
    resume_ats_next_actions: list[str] | None = None,
    documents_requested: list[str] | None = None,
    skip_reason: str | None = None,
) -> None:
    """Creates or updates the application record for (source, job_id).
    Fields left as None don't overwrite previously saved values -
    documents_requested is the full desired set each time it's passed (the
    Results tab checkboxes always submit the complete current selection, not
    a delta), so it's replaced rather than merged, same as status. Setting a
    skip_reason marks it unreviewed (skip_reason_reviewed=False) - Claude
    evaluates unreviewed reasons for what they imply about future searches
    (per PRD §13's non-applied-job feedback loop) and marks them reviewed
    via mark_skip_reason_reviewed(). resume_ats_score/rationale/next_actions
    are set together whenever the resume is (re)drafted - how well that
    exact resume text would score in a real ATS match against this job, and
    concrete ways to raise it, same "score + why + how to raise it" shape as
    Prospector Score and LinkedIn's profile-strength score."""
    applications = load_applications()
    for app in applications:
        if app["source"] == source and app["job_id"] == job_id:
            if app.get("status") != status:
                app["status_updated_at"] = datetime.now(timezone.utc).isoformat()
            app["status"] = status
            if resume_text is not None:
                app["resume_text"] = resume_text
            if cover_letter_text is not None:
                app["cover_letter_text"] = cover_letter_text
            if exec_bio_text is not None:
                app["exec_bio_text"] = exec_bio_text
            if leadership_summary_text is not None:
                app["leadership_summary_text"] = leadership_summary_text
            if resume_ats_score is not None:
                app["resume_ats_score"] = resume_ats_score
            if resume_ats_rationale is not None:
                app["resume_ats_rationale"] = resume_ats_rationale
            if resume_ats_next_actions is not None:
                app["resume_ats_next_actions"] = resume_ats_next_actions
            if documents_requested is not None:
                app["documents_requested"] = documents_requested
            if skip_reason is not None:
                app["skip_reason"] = skip_reason
                app["skip_reason_reviewed"] = False
            _save_all(applications)
            _write_dossier(source, job_id)
            return

    applications.append({
        "source": source,
        "job_id": job_id,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status_updated_at": datetime.now(timezone.utc).isoformat(),
        "resume_text": resume_text,
        "cover_letter_text": cover_letter_text,
        "exec_bio_text": exec_bio_text,
        "leadership_summary_text": leadership_summary_text,
        "resume_ats_score": resume_ats_score,
        "resume_ats_rationale": resume_ats_rationale,
        "resume_ats_next_actions": resume_ats_next_actions if resume_ats_next_actions is not None else [],
        "documents_requested": documents_requested if documents_requested is not None else [],
        "skip_reason": skip_reason,
        "skip_reason_reviewed": False if skip_reason is not None else None,
    })
    _save_all(applications)
    _write_dossier(source, job_id)


def set_strategy_tag(source: str, job_id: str, strategy_tag: str) -> None:
    """PRD §16d/§17: a short tag describing what's different about this
    application's approach (e.g. "concise-1-page", "leadership-narrative-
    focus") - set at drafting time so the Learn Engine can later correlate
    tags with outcomes. No fixed taxonomy - Claude suggests one based on
    what's actually different about this draft, Zahir confirms/edits."""
    applications = load_applications()
    for app in applications:
        if app["source"] == source and app["job_id"] == job_id:
            app["strategy_tag"] = strategy_tag
            _save_all(applications)
            _write_dossier(source, job_id)
            return


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
                if app.get("status") != app["suggested_status"]:
                    app["status_updated_at"] = datetime.now(timezone.utc).isoformat()
                app["status"] = app["suggested_status"]
            app["suggested_status"] = None
            app["suggested_status_reason"] = None
            _save_all(applications)
            _write_dossier(source, job_id)
            return
