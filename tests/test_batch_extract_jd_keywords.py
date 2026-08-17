"""scripts/batch_extract_jd_keywords.py (2026-08-17, feature/jd-keyword-
taxonomy-gaps, Phase 1) - the resumable/capped batch runner. No live
subprocess/network calls - extract_keywords_via_subscription is always
mocked. PROGRESS_PATH is redirected into tmp_path for every test so no
test ever touches the real data/jobs/jd_keyword_extraction_progress.json."""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import batch_extract_jd_keywords as batch  # noqa: E402
from search import job_store  # noqa: E402
from tailoring.reasoner_cli import ReasonerUnavailable  # noqa: E402


LONG_JD = "Real posting text. " * 20  # > 200 chars


def _job(source, job_id, description=LONG_JD, **extra):
    return {"source": source, "job_id": job_id, "title": f"Role {job_id}", "organization": "Acme", "description": description, **extra}


def _isolate_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "PROGRESS_PATH", tmp_path / "progress.json")


def test_eligible_backlog_excludes_jobs_with_short_or_no_description():
    jobs = [
        _job("a", "1", description="too short"),
        _job("a", "2", description=""),
        _job("a", "3", description=LONG_JD),
    ]
    backlog = batch._eligible_backlog(jobs)
    assert [j["job_id"] for j in backlog] == ["3"]


def test_eligible_backlog_excludes_jobs_that_already_have_keywords():
    jobs = [
        _job("a", "1", ats_required_keywords=["SQL"], ats_preferred_keywords=[]),
        _job("a", "2"),
    ]
    backlog = batch._eligible_backlog(jobs)
    assert [j["job_id"] for j in backlog] == ["2"]


def test_run_batch_processes_and_persists_real_keywords(isolated_data, tmp_path, monkeypatch):
    _isolate_progress(tmp_path, monkeypatch)
    job_store.save_jobs([_job("linkedin", "1"), _job("linkedin", "2")], apply_exclusion=False, review_required=False)

    calls = []

    def _fake_extract(job):
        calls.append(job["job_id"])
        return (["SQL"], ["AWS"])

    monkeypatch.setattr(batch, "extract_keywords_via_subscription", _fake_extract)

    result = batch.run_batch(max_jobs=10, max_minutes=25)

    assert result["succeeded"] == 2
    assert result["remaining"] == 0
    jobs = job_store.load_jobs()
    assert all(j["ats_required_keywords"] == ["SQL"] for j in jobs)
    assert all(j["ats_preferred_keywords"] == ["AWS"] for j in jobs)
    assert batch.PROGRESS_PATH.exists()


def test_run_batch_enforces_the_max_jobs_cap(isolated_data, tmp_path, monkeypatch):
    _isolate_progress(tmp_path, monkeypatch)
    job_store.save_jobs([_job("linkedin", str(i)) for i in range(5)], apply_exclusion=False, review_required=False)
    monkeypatch.setattr(batch, "extract_keywords_via_subscription", lambda job: (["X"], []))

    result = batch.run_batch(max_jobs=2, max_minutes=25)

    assert result["succeeded"] == 2
    assert result["remaining"] == 3


def test_run_batch_stops_the_whole_run_on_reasoner_unavailable(isolated_data, tmp_path, monkeypatch):
    _isolate_progress(tmp_path, monkeypatch)
    job_store.save_jobs([_job("linkedin", str(i)) for i in range(3)], apply_exclusion=False, review_required=False)

    def _fake_extract(job):
        raise ReasonerUnavailable("claude CLI not installed")

    monkeypatch.setattr(batch, "extract_keywords_via_subscription", _fake_extract)

    result = batch.run_batch(max_jobs=10, max_minutes=25)

    # Only the first job is even attempted - a systemic failure stops the
    # whole run rather than burning through every remaining job on a
    # guaranteed-identical failure.
    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert result["remaining"] == 3


def test_run_batch_continues_past_a_single_job_failure(isolated_data, tmp_path, monkeypatch):
    _isolate_progress(tmp_path, monkeypatch)
    job_store.save_jobs([_job("linkedin", "1"), _job("linkedin", "2")], apply_exclusion=False, review_required=False)

    def _fake_extract(job):
        if job["job_id"] == "1":
            raise RuntimeError("malformed reply")
        return (["SQL"], [])

    monkeypatch.setattr(batch, "extract_keywords_via_subscription", _fake_extract)

    result = batch.run_batch(max_jobs=10, max_minutes=25)

    assert result["succeeded"] == 1
    assert result["failed"] == 1
    jobs = {j["job_id"]: j for j in job_store.load_jobs()}
    assert jobs["1"].get("ats_required_keywords") is None
    assert jobs["2"]["ats_required_keywords"] == ["SQL"]


def test_run_batch_enforces_the_wall_clock_cap(isolated_data, tmp_path, monkeypatch):
    _isolate_progress(tmp_path, monkeypatch)
    job_store.save_jobs([_job("linkedin", str(i)) for i in range(3)], apply_exclusion=False, review_required=False)

    def _never_called(job):
        raise AssertionError("should never be called - the wall-clock budget is already exhausted")

    monkeypatch.setattr(batch, "extract_keywords_via_subscription", _never_called)

    # max_minutes=0 means the deadline is already in the past the instant
    # the loop checks it - real circuit breaker, not a per-job cap.
    result = batch.run_batch(max_jobs=10, max_minutes=0)

    assert result["succeeded"] == 0
    assert result["remaining"] == 3


def test_run_batch_is_a_true_no_op_when_backlog_already_empty(isolated_data, tmp_path, monkeypatch):
    _isolate_progress(tmp_path, monkeypatch)
    job_store.save_jobs([_job("linkedin", "1", ats_required_keywords=["SQL"], ats_preferred_keywords=[])], apply_exclusion=False, review_required=False)

    def _never_called(job):
        raise AssertionError("should never be called - backlog is already empty")

    monkeypatch.setattr(batch, "extract_keywords_via_subscription", _never_called)

    result = batch.run_batch(max_jobs=10, max_minutes=25)

    assert result == {"processed": 0, "succeeded": 0, "failed": 0, "remaining": 0}
