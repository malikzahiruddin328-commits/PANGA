"""Shared document-generation helper for the basket build (2026-08-13).

The Results tab's own per-job "Generate documents" button (see ui/app.py,
the "Documents for this application" section inside each job's detail
panel) already existed before this module and is left untouched - it has
its own live progress-bar/toast wiring that's risky to disturb. This
module exists so the basket's NEW bulk "generate for everything in the
basket" action persists results the exact same way (same
upsert_application()/sync_workspace_documents() calls, same generation-
lock discipline) without duplicating that logic ad hoc inside the basket
UI code, and so both the per-job path and this one stay behaviorally
identical if either ever needs to change.

generate_for_job() is also reused directly for the basket's per-item
"Generate" action (one job at a time, from inside the basket panel) - so
this module is the one place backing BOTH the per-job and bulk basket
actions the spec asked for, not two separate implementations.
"""

from tailoring.applications import (
    get_application,
    release_generation_lock,
    try_acquire_generation_lock,
    upsert_application,
)
from tailoring.dossier import sync_workspace_documents
from tailoring.drafting import DraftingFailed, DraftingNotConfigured, _report_drafting_failure, generate_documents


def generate_for_job(job: dict, profile: dict, doc_keys: list[str]) -> dict:
    """Drafts `doc_keys` for one job and persists the results (upserts the
    application record, syncs the per-application workspace .docx files) -
    the same two calls the Results tab's own inline "Generate documents"
    handler makes after a successful draft.

    Returns one of:
    - {"ok": True, "errors": {}}
    - {"ok": False, "locked": True, "errors": {}} - another generation
      (a second tab, a fast double-click, or - for the bulk basket
      caller - simply this same job already mid-draft from a concurrent
      per-job click) was already in progress for this (source, job_id);
      nothing was drafted or overwritten.
    - {"ok": False, "locked": False, "errors": {doc_key: message, ...}} -
      drafting was attempted and at least one doc_key failed. For a
      single-doc_key request, generate_documents() raises directly rather
      than returning a per-key "_errors" entry (see its own docstring) -
      normalized to the same shape here so every caller only has to
      handle one failure shape, not two.

    Acquires and releases applications.try_acquire_generation_lock()/
    release_generation_lock() itself so callers (including
    generate_for_basket() below) never need to manage the lock directly -
    matches the existing per-(source, job_id) concurrent-generate guard
    the Results tab's own button already relies on (2026-08-11)."""
    source, job_id = job.get("source"), job.get("job_id")
    if not try_acquire_generation_lock(source, job_id):
        return {"ok": False, "locked": True, "errors": {}}
    try:
        app_record = get_application(source, job_id) or {}
        try:
            drafted = generate_documents(
                job, profile, doc_keys,
                existing_resume_text=app_record.get("resume_text") if "resume" not in doc_keys else None,
            )
        except (DraftingNotConfigured, DraftingFailed) as exc:
            key = doc_keys[0] if len(doc_keys) == 1 else "generate"
            return {"ok": False, "locked": False, "errors": {key: str(exc)}}
        except Exception as exc:  # noqa: BLE001 - only reachable for a single-doc_key request, see generate_documents()'s own docstring
            _report_drafting_failure(job, doc_keys[0], exc)
            return {"ok": False, "locked": False, "errors": {doc_keys[0]: "Something went wrong while drafting this document. It's been logged - try again in a moment."}}

        resume_draft = drafted.get("resume")
        resume_is_scored = isinstance(resume_draft, dict)
        upsert_application(
            source, job_id, status=app_record.get("status", "under review"),
            documents_requested=doc_keys,
            resume_text=resume_draft["text"] if resume_is_scored else resume_draft,
            resume_ats_score=resume_draft["ats_score"] if resume_is_scored else None,
            resume_ats_rationale=resume_draft["ats_rationale"] if resume_is_scored else None,
            resume_ats_next_actions=resume_draft["ats_next_actions"] if resume_is_scored else None,
            resume_clarifying_questions=resume_draft["clarifying_questions"] if resume_is_scored else None,
            suggested_strategy_tag=resume_draft["suggested_strategy_tag"] if resume_is_scored else None,
            resume_unconfirmed_claims_ai_reported=resume_draft.get("unconfirmed_claims", []) if resume_is_scored else None,
            cover_letter_text=drafted.get("cover_letter"),
            exec_bio_text=drafted.get("exec_bio"),
            leadership_summary_text=drafted.get("leadership_summary"),
            apply_answers=drafted.get("apply_answers"),
        )
        sync_workspace_documents(source, job_id, doc_keys, drafted, profile, job)
        errors = drafted.get("_errors") or {}
        return {"ok": not errors, "locked": False, "errors": errors}
    finally:
        release_generation_lock(source, job_id)


def generate_for_basket(jobs: list[dict], profile: dict, doc_keys: list[str], on_progress=None) -> dict:
    """Runs generate_for_job() across every job currently in the basket,
    continuing past any individual job's failure/lock-collision so one bad
    draft doesn't block the rest of the batch (CLAUDE.md's general
    "one item's failure shouldn't stop the rest" pattern, already used by
    every per-source loop in scripts/run_search.py).

    on_progress(i, total, job), if given, is called once per job right
    before it starts drafting - the basket UI uses this to show real
    "i of N: <title>" progress instead of an opaque spinner, same as every
    other multi-step operation in this app.

    Returns a dict keyed by (source, job_id) -> generate_for_job()'s own
    result dict, so the caller can report exactly which jobs succeeded,
    failed, or were skipped as locked."""
    results = {}
    for i, job in enumerate(jobs, start=1):
        if on_progress:
            on_progress(i, len(jobs), job)
        key = (job.get("source"), job.get("job_id"))
        results[key] = generate_for_job(job, profile, doc_keys)
    return results
