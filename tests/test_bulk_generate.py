"""tailoring.bulk_generate (2026-08-13 basket build) - the shared
generate-and-persist helper backing both the basket's per-item "Generate"
button and its bulk "Generate for all N" action. Everything it calls
(generate_documents, upsert_application, sync_workspace_documents, the
generation lock) is already covered by its own test suite elsewhere -
these tests are about the orchestration this module adds on top: lock
handling, normalizing generate_documents()'s two different failure
shapes into one, and generate_for_basket()'s concurrent, continue-past-
failure, early-stop-on-systemic-failure batch behavior.
"""

import tailoring.bulk_generate as bulk_generate
from tailoring.drafting import DraftingFailed


JOB = {"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}
PROFILE = {"target_title_framings": []}


def _patch_persist(monkeypatch, upserts=None, syncs=None):
    monkeypatch.setattr(bulk_generate, "get_application", lambda source, job_id: {})
    monkeypatch.setattr(bulk_generate, "upsert_application", lambda *a, **k: (upserts.append((a, k)) if upserts is not None else None))
    monkeypatch.setattr(bulk_generate, "sync_workspace_documents", lambda *a, **k: (syncs.append((a, k)) if syncs is not None else None))


def test_generate_for_job_returns_locked_when_lock_unavailable(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: False)
    released = []
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["resume"])

    assert result == {"ok": False, "locked": True, "errors": {}}
    assert released == []  # never acquired, so never released either


def test_generate_for_job_success_persists_and_releases_lock(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    released = []
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))
    monkeypatch.setattr(bulk_generate, "generate_documents", lambda *a, **k: {
        "resume": {"text": "resume text", "ats_score": 80, "ats_rationale": "r", "ats_next_actions": [], "clarifying_questions": [], "suggested_strategy_tag": "tag"},
        "_errors": {},
    })
    upserts, syncs = [], []
    _patch_persist(monkeypatch, upserts, syncs)

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["resume"])

    assert result == {"ok": True, "locked": False, "errors": {}}
    assert len(upserts) == 1
    assert len(syncs) == 1
    assert released == [("linkedin", "1")]  # lock released even on success


def test_generate_for_job_releases_lock_even_on_exception(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    released = []
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))

    def _raise(*a, **k):
        raise DraftingFailed("simulated refusal")
    monkeypatch.setattr(bulk_generate, "generate_documents", _raise)

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["resume"])

    assert result["ok"] is False
    assert result["locked"] is False
    assert "resume" in result["errors"]
    assert released == [("linkedin", "1")]


def test_generate_for_job_normalizes_single_doc_key_exception_into_errors_dict(monkeypatch):
    # generate_documents() raises directly (not via "_errors") for a
    # single-doc_key request per its own docstring - callers of this
    # module should never have to special-case that; both failure shapes
    # must land in the same {"ok": False, "errors": {...}} form.
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    monkeypatch.setattr(bulk_generate, "_report_drafting_failure", lambda job, doc_key, exc: None)

    def _raise(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(bulk_generate, "generate_documents", _raise)

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["cover_letter"])

    assert result["ok"] is False
    assert result["locked"] is False
    assert "cover_letter" in result["errors"]


def test_generate_for_job_surfaces_per_doc_errors_from_multi_doc_request(monkeypatch):
    # A 2+ doc_key request never raises - failures land in drafted["_errors"]
    # instead (generate_documents()'s own documented behavior).
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    monkeypatch.setattr(bulk_generate, "generate_documents", lambda *a, **k: {
        "resume": {"text": "t", "ats_score": 50, "ats_rationale": "r", "ats_next_actions": [], "clarifying_questions": [], "suggested_strategy_tag": "tag"},
        "_errors": {"cover_letter": "web search failed"},
    })
    _patch_persist(monkeypatch)

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["resume", "cover_letter"])

    assert result["ok"] is False
    assert result["errors"] == {"cover_letter": "web search failed"}


def test_generate_for_basket_continues_past_a_locked_job(monkeypatch):
    job_a = {"source": "linkedin", "job_id": "a", "title": "A", "organization": "X"}
    job_b = {"source": "linkedin", "job_id": "b", "title": "B", "organization": "Y"}

    def _fake_generate_for_job(job, profile, doc_keys):
        if job["job_id"] == "a":
            return {"ok": False, "locked": True, "errors": {}}
        return {"ok": True, "locked": False, "errors": {}}
    monkeypatch.setattr(bulk_generate, "generate_for_job", _fake_generate_for_job)

    # Completion order is no longer guaranteed under concurrency, so this
    # only checks the set of progress calls and the final results dict -
    # not a fixed 1,2,3... sequence (see test_generate_for_basket_on_
    # progress_fires_once_per_job_as_completed_count below for the shape
    # of the new contract).
    progress_calls = []
    results = bulk_generate.generate_for_basket(
        [job_a, job_b], PROFILE, ["resume"],
        on_progress=lambda completed, total, job: progress_calls.append((completed, total, job["job_id"])),
    )

    assert results[("linkedin", "a")]["locked"] is True
    assert results[("linkedin", "b")]["ok"] is True
    assert {job_id for _, _, job_id in progress_calls} == {"a", "b"}
    assert sorted(completed for completed, _, _ in progress_calls) == [1, 2]
    assert all(total == 2 for _, total, _ in progress_calls)


def test_generate_for_basket_empty_basket_returns_empty_results(monkeypatch):
    assert bulk_generate.generate_for_basket([], PROFILE, ["resume"]) == {}


def test_generate_for_basket_runs_jobs_with_real_concurrent_overlap(monkeypatch):
    # Real overlap, not just "the code technically uses a pool" - same
    # style of check as test_generate_documents_never_exceeds_max_concurrent_
    # drafts in tests/test_drafting.py.
    import threading
    import time

    lock = threading.Lock()
    concurrent_count = 0
    max_seen = 0

    def _fake_generate_for_job(job, profile, doc_keys):
        nonlocal concurrent_count, max_seen
        with lock:
            concurrent_count += 1
            max_seen = max(max_seen, concurrent_count)
        time.sleep(0.05)
        with lock:
            concurrent_count -= 1
        return {"ok": True, "locked": False, "errors": {}}
    monkeypatch.setattr(bulk_generate, "generate_for_job", _fake_generate_for_job)
    monkeypatch.setattr(bulk_generate, "effective_worker_count", lambda target_max, *a, **k: target_max)

    jobs = [{"source": "linkedin", "job_id": str(i), "title": f"Job {i}", "organization": "X"} for i in range(5)]

    results = bulk_generate.generate_for_basket(jobs, PROFILE, ["resume"], max_workers=5)

    assert len(results) == 5
    assert max_seen > 1


def test_generate_for_basket_never_exceeds_the_worker_ceiling(monkeypatch):
    captured_target_max = {}

    def _fake_effective_worker_count(target_max, *args, **kwargs):
        captured_target_max["value"] = target_max
        return target_max
    monkeypatch.setattr(bulk_generate, "effective_worker_count", _fake_effective_worker_count)
    monkeypatch.setattr(bulk_generate, "generate_for_job", lambda job, profile, doc_keys: {"ok": True, "locked": False, "errors": {}})

    jobs = [{"source": "linkedin", "job_id": str(i), "title": f"Job {i}", "organization": "X"} for i in range(10)]

    bulk_generate.generate_for_basket(jobs, PROFILE, ["resume"])

    assert captured_target_max["value"] == bulk_generate.BASKET_BULK_GENERATE_MAX_WORKERS


def test_generate_for_basket_one_jobs_unexpected_exception_does_not_take_down_the_rest(monkeypatch):
    job_a = {"source": "linkedin", "job_id": "a", "title": "A", "organization": "X"}
    job_b = {"source": "linkedin", "job_id": "b", "title": "B", "organization": "Y"}

    def _fake_generate_for_job(job, profile, doc_keys):
        if job["job_id"] == "a":
            raise RuntimeError("simulated unexpected failure")
        return {"ok": True, "locked": False, "errors": {}}
    monkeypatch.setattr(bulk_generate, "generate_for_job", _fake_generate_for_job)

    results = bulk_generate.generate_for_basket([job_a, job_b], PROFILE, ["resume"])

    assert results[("linkedin", "a")]["ok"] is False
    assert "generate" in results[("linkedin", "a")]["errors"]
    assert results[("linkedin", "b")]["ok"] is True


def test_generate_for_basket_stops_early_after_first_n_failures(monkeypatch):
    # Force strictly one-at-a-time execution (max_workers=1) so completion
    # order is deterministic: job 0, then 1, then 2, then (if not stopped)
    # 3... The first EARLY_STOP_CHECK_COUNT (3) jobs all fail -> the batch
    # must stop before job 3 (or any later job) is ever submitted.
    called_job_ids = []

    def _fake_generate_for_job(job, profile, doc_keys):
        called_job_ids.append(job["job_id"])
        return {"ok": False, "locked": False, "errors": {"generate": "boom"}}
    monkeypatch.setattr(bulk_generate, "generate_for_job", _fake_generate_for_job)

    jobs = [{"source": "linkedin", "job_id": str(i), "title": f"Job {i}", "organization": "X"} for i in range(10)]

    results = bulk_generate.generate_for_basket(jobs, PROFILE, ["resume"], max_workers=1)

    assert called_job_ids == ["0", "1", "2"]  # never reached job 3+
    assert "_stopped_early" in results
    stopped = results["_stopped_early"]
    assert stopped["ran"] == 3
    assert stopped["total"] == 10
    assert [j["job_id"] for j in stopped["skipped_jobs"]] == [str(i) for i in range(3, 10)]
    # The 3 jobs that DID run still have their real per-job results present.
    for i in range(3):
        assert results[("linkedin", str(i))]["ok"] is False


def test_generate_for_basket_does_not_stop_early_when_a_locked_result_breaks_the_failure_run(monkeypatch):
    # A "locked" result (another generation already in progress) is not a
    # genuine failure and must not count toward the early-stop threshold -
    # otherwise a batch that happens to hit a couple of already-in-progress
    # jobs would falsely trip the safety check.
    def _fake_generate_for_job(job, profile, doc_keys):
        if job["job_id"] == "1":
            return {"ok": False, "locked": True, "errors": {}}
        return {"ok": False, "locked": False, "errors": {"generate": "boom"}}
    monkeypatch.setattr(bulk_generate, "generate_for_job", _fake_generate_for_job)

    jobs = [{"source": "linkedin", "job_id": str(i), "title": f"Job {i}", "organization": "X"} for i in range(5)]

    results = bulk_generate.generate_for_basket(jobs, PROFILE, ["resume"], max_workers=1)

    assert "_stopped_early" not in results
    assert len(results) == 5


def test_generate_for_basket_does_not_stop_early_when_one_of_the_first_three_succeeds(monkeypatch):
    def _fake_generate_for_job(job, profile, doc_keys):
        if job["job_id"] == "1":
            return {"ok": True, "locked": False, "errors": {}}
        return {"ok": False, "locked": False, "errors": {"generate": "boom"}}
    monkeypatch.setattr(bulk_generate, "generate_for_job", _fake_generate_for_job)

    jobs = [{"source": "linkedin", "job_id": str(i), "title": f"Job {i}", "organization": "X"} for i in range(5)]

    results = bulk_generate.generate_for_basket(jobs, PROFILE, ["resume"], max_workers=1)

    assert "_stopped_early" not in results
    assert len(results) == 5


def test_generate_for_basket_on_progress_fires_once_per_job_as_completed_count(monkeypatch):
    monkeypatch.setattr(bulk_generate, "generate_for_job", lambda job, profile, doc_keys: {"ok": True, "locked": False, "errors": {}})
    jobs = [{"source": "linkedin", "job_id": str(i), "title": f"Job {i}", "organization": "X"} for i in range(4)]

    progress_calls = []
    bulk_generate.generate_for_basket(
        jobs, PROFILE, ["resume"],
        on_progress=lambda completed, total, job: progress_calls.append((completed, total)),
    )

    # Fires exactly once per job, with a monotonically increasing
    # completed-count from 1..total (order among jobs isn't fixed, but the
    # count itself always is - one call per completion, never a gap or
    # duplicate).
    assert len(progress_calls) == 4
    assert sorted(c for c, _ in progress_calls) == [1, 2, 3, 4]
    assert all(total == 4 for _, total in progress_calls)


# --- 2026-08-13 additions (feature/in-app-subscription-qa): on_progress
# passthrough (needed for the "Fire final API build" button's real
# progress) and the resume_draft_source="paid" stamp that distinguishes
# this pipeline's output from the new subscription path's. ---


def test_generate_for_job_passes_on_progress_through_to_generate_documents(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    seen_progress = []

    def _fake_generate_documents(job, profile, doc_keys, existing_resume_text=None, on_progress=None):
        seen_progress.append(on_progress)
        if on_progress:
            on_progress(1, 1, "resume", "thinking...")
        return {
            "resume": {"text": "t", "ats_score": 50, "ats_rationale": "r", "ats_next_actions": [], "clarifying_questions": [], "suggested_strategy_tag": "tag"},
            "_errors": {},
        }
    monkeypatch.setattr(bulk_generate, "generate_documents", _fake_generate_documents)
    _patch_persist(monkeypatch)

    fired = []
    bulk_generate.generate_for_job(JOB, PROFILE, ["resume"], on_progress=lambda *a: fired.append(a))

    assert seen_progress[0] is not None
    assert fired == [(1, 1, "resume", "thinking...")]


def test_generate_for_job_stamps_resume_draft_source_paid(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    monkeypatch.setattr(bulk_generate, "generate_documents", lambda *a, **k: {
        "resume": {"text": "t", "ats_score": 50, "ats_rationale": "r", "ats_next_actions": [], "clarifying_questions": [], "suggested_strategy_tag": "tag"},
        "_errors": {},
    })
    upserts = []
    _patch_persist(monkeypatch, upserts=upserts)

    bulk_generate.generate_for_job(JOB, PROFILE, ["resume"])

    assert upserts[0][1]["resume_draft_source"] == "paid"


def test_generate_for_job_no_resume_draft_source_when_resume_not_requested(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    monkeypatch.setattr(bulk_generate, "generate_documents", lambda *a, **k: {"cover_letter": "text", "_errors": {}})
    upserts = []
    _patch_persist(monkeypatch, upserts=upserts)

    bulk_generate.generate_for_job(JOB, PROFILE, ["cover_letter"])

    assert upserts[0][1]["resume_draft_source"] is None


# --- 2026-08-19 additions: don't redraft a doc_key that already has real,
# complete output - the "10-job basket, 6 hit the spend cap after the
# resume succeeded but before the cover letter did" incident. ---


ALREADY_BUILT_RESUME_APP_RECORD = {
    "status": "under review",
    "resume_draft_source": "paid",
    "resume_text": "existing real resume text",
    "resume_ats_score": 77,
    "resume_ats_rationale": "existing rationale",
    "resume_ats_next_actions": ["existing action"],
    "resume_clarifying_questions": [],
    "strategy_tag_suggestion": "existing-tag",
    "resume_unconfirmed_claims_ai_reported": [],
}


def test_generate_for_job_with_resume_already_built_only_drafts_the_missing_doc_key(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    monkeypatch.setattr(bulk_generate, "get_application", lambda source, job_id: dict(ALREADY_BUILT_RESUME_APP_RECORD))

    seen_doc_keys = []

    def _fake_generate_documents(job, profile, doc_keys, existing_resume_text=None, on_progress=None):
        seen_doc_keys.append(doc_keys)
        return {"cover_letter": "fresh cover letter text", "_errors": {}}
    monkeypatch.setattr(bulk_generate, "generate_documents", _fake_generate_documents)

    upserts, syncs = [], []
    monkeypatch.setattr(bulk_generate, "upsert_application", lambda *a, **k: upserts.append((a, k)))
    monkeypatch.setattr(bulk_generate, "sync_workspace_documents", lambda *a, **k: syncs.append((a, k)))

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["resume", "cover_letter"])

    assert result == {"ok": True, "locked": False, "errors": {}}
    # generate_documents() was only ever asked for the missing doc type -
    # the already-built resume was never sent back through the paid path.
    assert seen_doc_keys == [["cover_letter"]]
    # The persisted (merged) result still has real content for BOTH the
    # reused resume and the freshly drafted cover letter - nothing already-
    # good was dropped.
    upsert_kwargs = upserts[0][1]
    assert upsert_kwargs["resume_text"] == "existing real resume text"
    assert upsert_kwargs["cover_letter_text"] == "fresh cover letter text"
    # Resume wasn't freshly drafted this call, so its already-correct
    # "paid" stamp must be left untouched (None here means "don't touch"),
    # not redundantly re-stamped.
    assert upsert_kwargs["resume_draft_source"] is None


def test_generate_for_job_everything_already_built_skips_generate_documents_entirely(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    fully_built = dict(ALREADY_BUILT_RESUME_APP_RECORD)
    fully_built["cover_letter_text"] = "existing cover letter text"
    monkeypatch.setattr(bulk_generate, "get_application", lambda source, job_id: fully_built)

    called = []
    monkeypatch.setattr(bulk_generate, "generate_documents", lambda *a, **k: called.append(1) or {})
    upserts, syncs = [], []
    monkeypatch.setattr(bulk_generate, "upsert_application", lambda *a, **k: upserts.append((a, k)))
    monkeypatch.setattr(bulk_generate, "sync_workspace_documents", lambda *a, **k: syncs.append((a, k)))

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["resume", "cover_letter"])

    assert result == {"ok": True, "locked": False, "errors": {}}
    assert called == []  # generate_documents() never called - no wasted paid call
    assert upserts == []  # nothing changed, nothing to persist
    assert syncs == []


def test_generate_for_job_resume_reused_but_cover_letter_drafted_passes_existing_resume_text_through(monkeypatch):
    # Cross-document consistency contract: when resume is reused (not in
    # the batch actually sent to generate_documents()), the EXISTING resume
    # text must still be passed as existing_resume_text so cover_letter can
    # be checked for factual consistency against it.
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    monkeypatch.setattr(bulk_generate, "get_application", lambda source, job_id: dict(ALREADY_BUILT_RESUME_APP_RECORD))

    captured = {}

    def _fake_generate_documents(job, profile, doc_keys, existing_resume_text=None, on_progress=None):
        captured["doc_keys"] = doc_keys
        captured["existing_resume_text"] = existing_resume_text
        return {"cover_letter": "fresh cover letter text", "_errors": {}}
    monkeypatch.setattr(bulk_generate, "generate_documents", _fake_generate_documents)
    _patch_persist(monkeypatch, upserts=[], syncs=[])
    monkeypatch.setattr(bulk_generate, "get_application", lambda source, job_id: dict(ALREADY_BUILT_RESUME_APP_RECORD))

    bulk_generate.generate_for_job(JOB, PROFILE, ["resume", "cover_letter"])

    assert captured["doc_keys"] == ["cover_letter"]
    assert captured["existing_resume_text"] == "existing real resume text"


def test_generate_for_job_normal_case_nothing_already_built_drafts_everything_unchanged(monkeypatch):
    monkeypatch.setattr(bulk_generate, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(bulk_generate, "release_generation_lock", lambda source, job_id: None)
    monkeypatch.setattr(bulk_generate, "get_application", lambda source, job_id: {})

    seen_doc_keys = []

    def _fake_generate_documents(job, profile, doc_keys, existing_resume_text=None, on_progress=None):
        seen_doc_keys.append(doc_keys)
        assert existing_resume_text is None  # resume IS in this batch
        return {
            "resume": {"text": "brand new resume", "ats_score": 90, "ats_rationale": "r", "ats_next_actions": [], "clarifying_questions": [], "suggested_strategy_tag": "tag"},
            "cover_letter": "brand new cover letter",
            "_errors": {},
        }
    monkeypatch.setattr(bulk_generate, "generate_documents", _fake_generate_documents)
    upserts, syncs = [], []
    monkeypatch.setattr(bulk_generate, "upsert_application", lambda *a, **k: upserts.append((a, k)))
    monkeypatch.setattr(bulk_generate, "sync_workspace_documents", lambda *a, **k: syncs.append((a, k)))

    result = bulk_generate.generate_for_job(JOB, PROFILE, ["resume", "cover_letter"])

    assert result == {"ok": True, "locked": False, "errors": {}}
    assert seen_doc_keys == [["resume", "cover_letter"]]  # full set drafted, nothing excluded
    assert upserts[0][1]["resume_text"] == "brand new resume"
    assert upserts[0][1]["cover_letter_text"] == "brand new cover letter"
    assert upserts[0][1]["resume_draft_source"] == "paid"


def test_already_built_doc_keys_ignores_subscription_only_resume():
    # A subscription-path-only resume (resume_draft_source == "subscription")
    # must never count as "already built" for the paid path - it's a
    # different, $0 mechanism, not a real paid final build.
    app_record = {"resume_draft_source": "subscription", "resume_text": "free draft text"}
    assert bulk_generate._already_built_doc_keys(app_record, ["resume"]) == []


def test_already_built_doc_keys_treats_unknown_doc_key_as_not_built():
    assert bulk_generate._already_built_doc_keys({}, ["some_future_doc_type"]) == []
