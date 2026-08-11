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
from security.file_lock import locked

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
    resume_clarifying_questions: list[dict] | None = None,
    suggested_strategy_tag: str | None = None,
    documents_requested: list[str] | None = None,
    skip_reason: str | None = None,
    apply_answers: list[dict] | None = None,
    resume_unconfirmed_claims_ai_reported: list[dict] | None = None,
    resume_gap_scan_fingerprint: str | None = None,
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
    Prospector Score and LinkedIn's profile-strength score.
    resume_clarifying_questions is the subset of those gaps that hinge on a
    real fact Claude doesn't have rather than wording/structure - answering
    them (Results tab UI) feeds tailoring.drafting.save_gap_answers(), which
    writes into the master profile's own gap_interview_answers (per
    profile/interview.py) so the fact helps every future job, not just this
    one. Pass [] explicitly to clear once answered - a fresh generation
    always sets this field, so an empty list here means "nothing left to
    ask", distinct from None ("don't touch what's stored"). apply_answers is
    the "Apply Assist" packet (PRD-adjacent, 2026-07-31): a list of
    {"label": ..., "value": ...} ready-to-paste answers for an ATS form's
    recurring fields, drafted the same way as the other documents - Zahir
    still opens the real application and pastes/submits it himself, this
    only removes the retyping. resume_unconfirmed_claims_ai_reported
    (2026-08-09) is the AI's own self-reported list of hedged, "?"-marked
    guesses it wrote directly into resume_text this draft - a stored
    snapshot only for friendly skill labels; the actual safety gate
    (tailoring.unconfirmed_claims.find_unconfirmed_markers) always
    recomputes fresh from the current resume_text, never trusts this
    snapshot alone. suggested_strategy_tag (2026-07-31) is a
    short label Claude proposes describing what's distinctive about this
    specific resume draft (e.g. "concise-2-page-ats-focused") - stored
    separately from the real strategy_tag field (set_strategy_tag()) so a
    fresh regenerate's suggestion never silently overwrites a tag Zahir
    already saved himself; the Results tab prefills the strategy-tag input
    with this only when strategy_tag is still empty. resume_gap_scan_
    fingerprint (2026-08-09) is a stable fingerprint of whatever resume
    text the free-form gap scan (tailoring.drafting.
    request_additional_gap_questions) last ran against - app.py's auto-
    fire wrapper compares this to the CURRENT baseline text's fingerprint
    before firing again, so opening Analyze Fit repeatedly for the same
    resume version doesn't re-trigger the AI call every render/scroll,
    only when the resume text actually changed (a fresh Generate) or
    this is the first time it's ever run for this job."""
    with locked("applications"):
        applications = load_applications()
        for app in applications:
            if app["source"] == source and app["job_id"] == job_id:
                if app.get("status") != status:
                    app["status_updated_at"] = datetime.now(timezone.utc).isoformat()
                app["status"] = status
                if any(t is not None for t in (resume_text, cover_letter_text, exec_bio_text, leadership_summary_text)):
                    app["documents_drafted_at"] = datetime.now(timezone.utc).isoformat()
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
                if resume_clarifying_questions is not None:
                    app["resume_clarifying_questions"] = resume_clarifying_questions
                if suggested_strategy_tag is not None:
                    app["strategy_tag_suggestion"] = suggested_strategy_tag
                if documents_requested is not None:
                    app["documents_requested"] = documents_requested
                if skip_reason is not None:
                    app["skip_reason"] = skip_reason
                    app["skip_reason_reviewed"] = False
                if apply_answers is not None:
                    app["apply_answers"] = apply_answers
                if resume_unconfirmed_claims_ai_reported is not None:
                    app["resume_unconfirmed_claims_ai_reported"] = resume_unconfirmed_claims_ai_reported
                if resume_gap_scan_fingerprint is not None:
                    app["resume_gap_scan_fingerprint"] = resume_gap_scan_fingerprint
                _save_all(applications)
                _write_dossier(source, job_id)
                return

        applications.append({
            "source": source,
            "job_id": job_id,
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status_updated_at": datetime.now(timezone.utc).isoformat(),
            "documents_drafted_at": (
                datetime.now(timezone.utc).isoformat()
                if any(t is not None for t in (resume_text, cover_letter_text, exec_bio_text, leadership_summary_text))
                else None
            ),
            "document_edit_review": None,
            "resume_text": resume_text,
            "cover_letter_text": cover_letter_text,
            "exec_bio_text": exec_bio_text,
            "leadership_summary_text": leadership_summary_text,
            "resume_ats_score": resume_ats_score,
            "resume_ats_rationale": resume_ats_rationale,
            "resume_ats_next_actions": resume_ats_next_actions if resume_ats_next_actions is not None else [],
            "resume_clarifying_questions": resume_clarifying_questions if resume_clarifying_questions is not None else [],
            "strategy_tag_suggestion": suggested_strategy_tag,
            "documents_requested": documents_requested if documents_requested is not None else [],
            "skip_reason": skip_reason,
            "skip_reason_reviewed": False if skip_reason is not None else None,
            "apply_answers": apply_answers if apply_answers is not None else [],
            "resume_unconfirmed_claims_ai_reported": resume_unconfirmed_claims_ai_reported if resume_unconfirmed_claims_ai_reported is not None else [],
            "resume_gap_scan_fingerprint": resume_gap_scan_fingerprint,
        })
        _save_all(applications)
        _write_dossier(source, job_id)


# Concurrent-Generate guard (2026-08-11, PRD §13 gap flagged by Panga-
# Documentor): two Generate clicks on the SAME job close together - two
# browser tabs open on the same Results page, or a fast double-click - are
# a real, reachable race, not just theoretical. Reviewed the actual damage
# first: upsert_application()'s own read-modify-write is already safe
# against file corruption (it re-reads applications.json fresh under
# locked("applications") right before merging), so two concurrent
# generate_documents() calls can't corrupt the store. The real damage is
# semantic: two independent AI drafts for the same doc type race to be
# "the" saved version - whichever upsert_application() call happens to run
# last silently wins, with no error, no warning, and the OTHER draft's real
# API cost spent for nothing (worse for "resume" specifically, since
# generate_documents' self-correction loop can itself burn 3 real calls
# for a single request that then gets silently discarded). Held for the
# realistic duration of a real draft (a multi-doc Generate with self-
# correction retries can run several minutes) rather than the short
# critical sections security.file_lock.locked() is designed for -
# try_acquire_generation_lock() is deliberately try-once, not blocking:
# a second click should be told immediately, not left waiting on a lock
# that could hold for minutes.
_GENERATION_LOCK_STALE_AFTER_MINUTES = 20


def try_acquire_generation_lock(source: str, job_id: str) -> bool:
    """True if this call acquired the lock (safe to proceed with
    generate_documents()); False if another Generate is already genuinely
    in progress for this exact job. Always pair with release_generation_
    lock() in a try/finally - see that function's docstring for why a
    stuck lock isn't fatal even if the caller crashes before releasing."""
    now = datetime.now(timezone.utc)
    with locked("applications"):
        applications = load_applications()
        for app in applications:
            if app["source"] == source and app["job_id"] == job_id:
                held_since = app.get("generation_lock_acquired_at")
                if held_since:
                    held_at = datetime.fromisoformat(held_since)
                    age_minutes = (now - held_at).total_seconds() / 60
                    if age_minutes < _GENERATION_LOCK_STALE_AFTER_MINUTES:
                        return False
                    # Stale - the process that acquired this almost
                    # certainly crashed, was killed, or lost its
                    # connection before reaching the release_generation_
                    # lock() in its own finally block (CLAUDE.md's own
                    # "check for locking errors...unhandled exceptions
                    # that could leave a lock held" concern, applied here)
                    # - treat it as abandoned rather than blocking Generate
                    # on this job forever.
                app["generation_lock_acquired_at"] = now.isoformat()
                _save_all(applications)
                return True
        # No application record yet for this job - create one holding the
        # lock, same "under review" default upsert_application() itself
        # uses for a brand-new record.
        applications.append({
            "source": source,
            "job_id": job_id,
            "status": "under review",
            "created_at": now.isoformat(),
            "status_updated_at": now.isoformat(),
            "generation_lock_acquired_at": now.isoformat(),
        })
        _save_all(applications)
        return True


def release_generation_lock(source: str, job_id: str) -> None:
    """Always call from a finally block around the generate_documents()
    call the matching try_acquire_generation_lock() guarded - on success
    AND on failure, so a real drafting error never leaves this job
    permanently locked out of Generate until the 20-minute staleness
    ceiling above kicks in."""
    with locked("applications"):
        applications = load_applications()
        for app in applications:
            if app["source"] == source and app["job_id"] == job_id:
                app.pop("generation_lock_acquired_at", None)
                _save_all(applications)
                return


def record_document_edit_review(source: str, job_id: str, documents: dict, reason: str) -> None:
    """"Apply Assist edit review" (Zahir's request 2026-07-31): once he's
    opened a drafted document's real .docx file (dossier.sync_workspace_
    documents()) and possibly edited it, dossier.check_for_edits() diffs the
    on-disk file against what's stored here and this function saves that
    result plus his own required note on WHY he changed anything - so a
    future Learn Engine pass can eventually reason about what edits Zahir
    tends to make and why, not just whether he applied. `documents` is
    whatever dossier.check_for_edits() returned (doc_key -> {"changed",
    "diff"}); `reason` is required non-empty text - the Results tab only
    calls this once Zahir has actually typed something, matching the same
    "never save an invented/blank fact" convention as skip reasons and gap
    answers elsewhere in this module. checked_at is a fresh timestamp,
    always after documents_drafted_at at the moment this is called - that
    ordering (not just presence) is what needs_edit_review() below checks,
    so regenerating a document invalidates a stale review instead of
    silently keeping stale reasoning attached to newly re-drafted text."""
    with locked("applications"):
        applications = load_applications()
        for app in applications:
            if app["source"] == source and app["job_id"] == job_id:
                app["document_edit_review"] = {
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "documents": documents,
                    "reason": reason,
                }
                _save_all(applications)
                _write_dossier(source, job_id)
                return


def needs_edit_review(app_record: dict) -> bool:
    """True if this application has at least one drafted prose document
    (resume/cover letter/exec bio/leadership summary) and no completed edit
    review since the last time any of them was (re)drafted - i.e. Zahir
    hasn't yet confirmed whether he changed anything and why. Pure/no store
    I/O, so the Results tab can call it on an already-loaded record without
    an extra read. Used to gate marking a job "applied" (Zahir's explicit
    request: a hard block, not just a nag)."""
    has_any_document = any(
        app_record.get(field) for field in ("resume_text", "cover_letter_text", "exec_bio_text", "leadership_summary_text")
    )
    if not has_any_document:
        return False
    drafted_at = app_record.get("documents_drafted_at")
    review = app_record.get("document_edit_review")
    if not review or not (review.get("reason") or "").strip():
        return True
    if drafted_at and review.get("checked_at", "") < drafted_at:
        return True
    return False


def set_strategy_tag(source: str, job_id: str, strategy_tag: str) -> None:
    """PRD §16d/§17: a short tag describing what's different about this
    application's approach (e.g. "concise-1-page", "leadership-narrative-
    focus") - set at drafting time so the Learn Engine can later correlate
    tags with outcomes. No fixed taxonomy - Claude suggests one based on
    what's actually different about this draft, Zahir confirms/edits."""
    with locked("applications"):
        applications = load_applications()
        for app in applications:
            if app["source"] == source and app["job_id"] == job_id:
                app["strategy_tag"] = strategy_tag
                _save_all(applications)
                _write_dossier(source, job_id)
                return


def mark_skip_reason_reviewed(source: str, job_id: str) -> None:
    with locked("applications"):
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
    with locked("applications"):
        applications = load_applications()
        for app in applications:
            if app["source"] == source and app["job_id"] == job_id:
                app["suggested_status"] = suggested_status
                app["suggested_status_reason"] = reason
                _save_all(applications)
                return


def get_pending_status_suggestions() -> list[dict]:
    return [a for a in load_applications() if a.get("suggested_status")]


def get_applications_with_open_clarifying_questions() -> list[dict]:
    """Every application whose most recent resume draft still has unanswered
    clarifying_questions - backs the Profile Gaps tab (app.py), which
    consolidates these across all jobs into one place rather than each
    living inline on its own job's Results entry (moved 2026-08-04, Zahir:
    too much clutter mixed into per-job review). A job drops out of this
    list on its own once its questions are answered and its resume
    regenerated - either it scores 100 and clarifying_questions comes back
    empty (see tailoring/drafting.py's _questions_worth_asking()), or a new,
    smaller set of genuine gaps replaces the old one."""
    return [a for a in load_applications() if a.get("resume_clarifying_questions")]


def confirm_status_suggestion(source: str, job_id: str, accept: bool) -> None:
    """accept=True applies the suggested_status as the real status; either
    way, clears the suggestion so it isn't asked about again."""
    with locked("applications"):
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
