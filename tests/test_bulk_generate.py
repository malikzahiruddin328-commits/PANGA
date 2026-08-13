"""tailoring.bulk_generate (2026-08-13 basket build) - the shared
generate-and-persist helper backing both the basket's per-item "Generate"
button and its bulk "Generate for all N" action. Everything it calls
(generate_documents, upsert_application, sync_workspace_documents, the
generation lock) is already covered by its own test suite elsewhere -
these tests are about the orchestration this module adds on top: lock
handling, normalizing generate_documents()'s two different failure
shapes into one, and generate_for_basket()'s continue-past-failure loop.
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

    progress_calls = []
    results = bulk_generate.generate_for_basket(
        [job_a, job_b], PROFILE, ["resume"],
        on_progress=lambda i, total, job: progress_calls.append((i, total, job["job_id"])),
    )

    assert results[("linkedin", "a")]["locked"] is True
    assert results[("linkedin", "b")]["ok"] is True
    assert progress_calls == [(1, 2, "a"), (2, 2, "b")]


def test_generate_for_basket_empty_basket_returns_empty_results(monkeypatch):
    assert bulk_generate.generate_for_basket([], PROFILE, ["resume"]) == {}
