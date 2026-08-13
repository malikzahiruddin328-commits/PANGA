"""2026-08-13 basket/review build: score_unscored_jobs() must never score a
job still sitting at review_status == "pending" or "rejected" - that's
exactly the auto-scoring the Results tab's new review UI exists to gate.
A job with no review_status at all (saved before this field existed) must
still score normally - the implicit historical default is "accepted", not
"pending", so this change can never silently stop scoring jobs that were
already flowing through the pipeline before today.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_search  # noqa: E402

from search import job_store  # noqa: E402

PROFILE = {"target_title_framings": [{"summary": "IT/data/cybersecurity executive."}]}


def test_pending_review_job_is_never_scored(isolated_data, monkeypatch):
    # Default save_jobs() call - stamps review_status="pending", same as
    # every real source connector (USAJOBS/Dice/company-sites/etc).
    job_store.save_jobs([{"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}])
    score_calls = []
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: score_calls.append(job) or {"fit_score": 90, "fit_rationale": "x"})

    run_search.score_unscored_jobs(PROFILE)

    assert score_calls == []
    assert "fit_score" not in job_store.load_jobs()[0]


def test_rejected_job_is_never_scored(isolated_data, monkeypatch):
    job_store.save_jobs([{"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}])
    job_store.set_review_status("linkedin", "1", "rejected")
    score_calls = []
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: score_calls.append(job) or {"fit_score": 90, "fit_rationale": "x"})

    run_search.score_unscored_jobs(PROFILE)

    assert score_calls == []
    assert "fit_score" not in job_store.load_jobs()[0]


def test_accepted_job_scores_normally(isolated_data, monkeypatch):
    job_store.save_jobs([{"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}])
    job_store.set_review_status("linkedin", "1", "accepted")
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: {"fit_score": 90, "fit_rationale": "strong"})

    run_search.score_unscored_jobs(PROFILE)

    assert job_store.load_jobs()[0]["fit_score"] == 90


def test_job_with_no_review_status_at_all_scores_normally(isolated_data, monkeypatch):
    # Simulates a job saved before this field existed - written directly,
    # bypassing save_jobs()'s own default-stamping, to reproduce that real
    # historical shape exactly.
    job_store.write_json(job_store.JOBS_PATH, [{"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}])
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: {"fit_score": 90, "fit_rationale": "strong"})

    run_search.score_unscored_jobs(PROFILE)

    assert job_store.load_jobs()[0]["fit_score"] == 90


def test_mixed_batch_only_scores_accepted_and_legacy_jobs(isolated_data, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "pending", "title": "VP IT", "organization": "Acme"},
    ])  # left at the default "pending"
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "accepted", "title": "VP IT", "organization": "Acme"},
    ])
    job_store.set_review_status("linkedin", "accepted", "accepted")
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "rejected", "title": "VP IT", "organization": "Acme"},
    ])
    job_store.set_review_status("linkedin", "rejected", "rejected")

    scored_ids = []

    def _fake_score_job(job, profile):
        scored_ids.append(job["job_id"])
        return {"fit_score": 90, "fit_rationale": "strong"}

    monkeypatch.setattr(run_search, "score_job", _fake_score_job)

    run_search.score_unscored_jobs(PROFILE)

    assert scored_ids == ["accepted"]
