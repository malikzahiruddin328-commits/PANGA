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

import itertools
import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from concurrency.adaptive_throttle import effective_worker_count
from tailoring.applications import (
    get_application,
    release_generation_lock,
    try_acquire_generation_lock,
    upsert_application,
)
from tailoring.dossier import sync_workspace_documents
from tailoring.drafting import DraftingFailed, DraftingNotConfigured, _report_drafting_failure, generate_documents

logger = logging.getLogger(__name__)

# Same shape/spirit as ui/app.py's BASKET_DRAFTALL_MAX_WORKERS and
# gap_answer_drafting.GAP_ANSWER_DRAFTALL_MAX_WORKERS (both 2026-08-17/19
# precedents for a multi-item real-cost concurrent AI-call batch in this
# codebase) - a ceiling, backed off downward by concurrency.adaptive_
# throttle.effective_worker_count() right before the pool starts, never a
# guarantee every run gets exactly this many.
BASKET_BULK_GENERATE_MAX_WORKERS = 6

# Early-stop safety check (2026-08-19, added after Zahir + the hub flagged
# that parallelizing this PAID step loses a quiet safety property the old
# sequential loop had for free: if job 1 fails for a systemic reason - bad
# profile data, a real bug, the reasoner misconfigured - sequential
# execution would surface that before spending on jobs 2-10, but firing
# everything at once spends on most of the batch before anyone would
# notice the pattern. This does NOT stop on one unlucky failure (a single
# real per-job problem - a bad JD, a flaky web search - is exactly what
# CLAUDE.md's "one item's failure shouldn't stop the rest" rule already
# covers, and generate_for_basket() still honors that for anything short
# of this threshold). It stops once the first EARLY_STOP_CHECK_COUNT
# completions - out of the whole batch, not "in a row" partway through -
# are ALL genuine failures (ok=False and NOT locked=True; a "locked"
# result means some other in-progress generation, not a broken batch, so
# it's excluded from this check same as it's excluded from every other
# failure count in this module). 3 is small enough to catch a genuinely
# broken batch within a couple of jobs' real spend, and large enough that
# one unlucky job (a transient web-search failure, one bad JD) can't
# trigger it alone.
EARLY_STOP_CHECK_COUNT = 3


def generate_for_job(job: dict, profile: dict, doc_keys: list[str], on_progress=None) -> dict:
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
    the Results tab's own button already relies on (2026-08-11).

    on_progress, if given, is passed straight through to generate_documents()
    (2026-08-13, needed by the in-app subscription Q&A loop's "Fire final
    API build" button - the one real paid call in that flow still needs
    real "Drafting final version..." progress visibility, not a silent
    multi-minute wait, same standing requirement every other call in this
    app already meets)."""
    source, job_id = job.get("source"), job.get("job_id")
    if not try_acquire_generation_lock(source, job_id):
        return {"ok": False, "locked": True, "errors": {}}
    try:
        app_record = get_application(source, job_id) or {}
        try:
            drafted = generate_documents(
                job, profile, doc_keys,
                existing_resume_text=app_record.get("resume_text") if "resume" not in doc_keys else None,
                on_progress=on_progress,
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
            resume_draft_source="paid" if "resume" in doc_keys else None,
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


def generate_for_basket(
    jobs: list[dict], profile: dict, doc_keys: list[str], on_progress=None, max_workers: int | None = None,
) -> dict:
    """Runs generate_for_job() across every job currently in the basket
    CONCURRENTLY (2026-08-19, replacing the original sequential `for` loop -
    generate_for_job() is the expensive, real-money-and-time paid final-build
    call, so running 10 of them one at a time was a real, avoidable
    wall-clock cost). Reuses the SAME ThreadPoolExecutor + concurrency.
    adaptive_throttle.effective_worker_count pattern already established for
    exactly this shape of work by ui/app.py's basket-wide "Draft & score all"
    button (BASKET_DRAFTALL_MAX_WORKERS) and gap_answer_drafting.
    draft_gap_answers_concurrently() - a worker-count ceiling
    (BASKET_BULK_GENERATE_MAX_WORKERS), backed off downward once right
    before the pool starts if this machine is already under real CPU load,
    never a fixed worker count.

    Safe to run several jobs' generate_for_job() calls concurrently: each
    job is an independent record, generate_for_job() acquires/releases its
    own per-(source, job_id) generation lock internally (so two DIFFERENT
    jobs never contend with each other, and the SAME job can never double-
    generate even if it somehow appeared twice), and every read-modify-write
    against the shared applications.json store goes through security.
    file_lock.locked("applications") - a real cross-process file lock held
    only for the brief duration of one read+merge+write, not for the whole
    multi-minute draft - so concurrent workers touching that store safely
    serialize around each other rather than corrupting or dropping writes.

    Continues past any individual job's failure/lock-collision so one bad
    draft doesn't block the rest of the batch (CLAUDE.md's general "one
    item's failure shouldn't stop the rest" pattern) - an unexpected
    exception from generate_for_job() itself (defense in depth; it's not
    documented to raise) is caught per-worker and reported as a normal
    failure result rather than taking the whole batch down, same as
    ui/app.py's _draftall_worker and gap_answer_drafting's own worker.

    on_progress(completed, total, job), if given, is called once per job as
    each one FINISHES (not before it starts, and not in basket order) -
    updated from the prior sequential contract because completion order is
    no longer 1, 2, 3... under concurrency; `completed` is the number of
    jobs done so far (1..total) and `job` is the job that just finished, so
    the basket UI can still show a live "i of N" progress bar, just driven
    by real completions instead of a fixed position. See ui/app.py's
    _update_bulk_progress for the caller that was updated to match.

    Jobs are only ever submitted to the pool up to `worker_count` at a
    time, not all at once (see EARLY_STOP_CHECK_COUNT's module-level
    docstring above for why) - this makes the early-stop check below
    actually effective: a not-yet-submitted job never starts its real paid
    generate_for_job() call once the batch is judged broken, rather than
    already being queued in the pool where stopping would be too late.

    Returns a dict keyed by (source, job_id) -> generate_for_job()'s own
    result dict, for every job that was actually run, regardless of
    completion order. If the early-stop check above triggers, the dict
    also carries one extra, non-tuple key - results["_stopped_early"] -
    a dict describing why (see its own contents below); this can never
    collide with a real per-job key since every real key is a (source,
    job_id) tuple, same "extra string key alongside the real per-item
    entries" convention generate_documents() already uses for its own
    "_errors" key. ui/app.py pops this key before iterating results as
    per-job outcomes, and surfaces it as its own distinct message so a
    stopped-early batch reads as "3 failed, batch stopped - 6 never ran"
    rather than silently looking like "6 succeeded, 4 mysteriously never
    ran"."""
    if not jobs:
        return {}

    def _worker(job: dict) -> tuple[tuple, dict, dict]:
        key = (job.get("source"), job.get("job_id"))
        try:
            result = generate_for_job(job, profile, doc_keys)
        except Exception as exc:  # noqa: BLE001 - defense in depth, matching _draftall_worker/draft_gap_answers_concurrently's own worker
            logger.exception("bulk_generate: unexpected failure generating documents for job %r", key)
            result = {"ok": False, "locked": False, "errors": {"generate": str(exc)}}
        return key, job, result

    worker_count = effective_worker_count(min(len(jobs), max_workers or BASKET_BULK_GENERATE_MAX_WORKERS))
    total = len(jobs)
    results: dict = {}
    completed = 0
    stopped_early = False
    early_completion_outcomes: list[bool] = []  # True = genuine failure, for the first EARLY_STOP_CHECK_COUNT completions only

    job_iter = iter(jobs)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        pending = set()
        for job in itertools.islice(job_iter, worker_count):
            pending.add(pool.submit(_worker, job))

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                key, job, result = future.result()
                results[key] = result
                completed += 1
                if on_progress:
                    on_progress(completed, total, job)

                if not stopped_early and len(early_completion_outcomes) < EARLY_STOP_CHECK_COUNT:
                    genuine_failure = not result.get("ok") and not result.get("locked")
                    early_completion_outcomes.append(genuine_failure)
                    if len(early_completion_outcomes) == EARLY_STOP_CHECK_COUNT and all(early_completion_outcomes):
                        stopped_early = True
                        logger.warning(
                            "bulk_generate: first %d completions all failed - stopping the rest of a %d-job batch "
                            "before spending on more of it.", EARLY_STOP_CHECK_COUNT, total,
                        )

            if not stopped_early:
                # Refill the pool up to worker_count with whatever jobs are
                # still left to start - keeps the same steady-state
                # concurrency the old submit-everything-up-front version
                # had, just submitted lazily so a stop can actually take
                # effect on not-yet-started jobs.
                for job in itertools.islice(job_iter, len(done)):
                    pending.add(pool.submit(_worker, job))

        if stopped_early:
            skipped_jobs = list(job_iter)  # whatever never got submitted
            results["_stopped_early"] = {
                "reason": (
                    f"Stopped after the first {EARLY_STOP_CHECK_COUNT} completed jobs all failed - "
                    "this looks like a systemic problem (bad data or a bug), not one unlucky job, so "
                    "the rest of the batch was never started."
                ),
                "ran": completed,
                "total": total,
                "skipped_jobs": skipped_jobs,
            }
    return results
