"""2026-08-17: score_unscored_jobs(profile, prefilter_only=True) and the
run_search.py --prefilter-only CLI flag - Zahir's explicit ask to observe
the cheap Haiku domain-relevance filter's real behavior WITHOUT incurring
the far pricier Opus score_job() call it normally gates.

Covers the wiring only (a passing job never reaches score_job, a skipped
job is still logged/left untouched exactly like the default mode, and
default (prefilter_only=False, the implicit default for every existing
caller) behavior is completely unchanged) - the prefilter module's own
domain-check logic is covered in test_fit_score_prefilter.py, and the
default-mode wiring is covered in test_run_search_prefilter.py.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_search  # noqa: E402

from search import job_store  # noqa: E402
from security.crypto_store import read_json  # noqa: E402
from tailoring import fit_score_prefilter  # noqa: E402

PROFILE = {"target_title_framings": [{"summary": "IT/data/cybersecurity executive."}]}


def test_passing_job_never_reaches_score_job_in_prefilter_only_mode(isolated_data, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "2", "title": "VP Information Technology", "organization": "Acme"},
    ], review_required=False)
    monkeypatch.setattr(fit_score_prefilter, "should_skip_scoring", lambda job, profile: None)
    score_calls = []
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: score_calls.append(job) or {"fit_score": 99, "fit_rationale": "x"})

    result = run_search.score_unscored_jobs(PROFILE, prefilter_only=True)

    assert score_calls == []
    assert result == []
    jobs = job_store.load_jobs()
    assert "fit_score" not in jobs[0]  # left exactly as unscored - still eligible for a later full-scoring run


def test_skipped_job_still_logged_in_prefilter_only_mode(isolated_data, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "1", "title": "Firefighter", "organization": "Big Health"},
    ], review_required=False)
    score_calls = []
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: score_calls.append(job) or {"fit_score": 99, "fit_rationale": "x"})

    run_search.score_unscored_jobs(PROFILE, prefilter_only=True)

    assert score_calls == []
    entries = read_json(fit_score_prefilter.PREFILTER_LOG_PATH, default=[])
    assert len(entries) == 1
    assert entries[0]["job_id"] == "1"
    assert entries[0]["layer"] == "deterministic"
    jobs = job_store.load_jobs()
    assert "fit_score" not in jobs[0]


def test_default_mode_unaffected_by_new_parameter(isolated_data, monkeypatch):
    """prefilter_only defaults to False - existing callers (run_search.run()
    without the flag, and any other caller that doesn't pass it) must keep
    calling score_job normally, exactly as before this change."""
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "2", "title": "VP Information Technology", "organization": "Acme"},
    ], review_required=False)
    monkeypatch.setattr(fit_score_prefilter, "should_skip_scoring", lambda job, profile: None)
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: {"fit_score": 77, "fit_rationale": "solid match"})

    result = run_search.score_unscored_jobs(PROFILE)  # no prefilter_only arg at all

    jobs = job_store.load_jobs()
    assert jobs[0]["fit_score"] == 77
    assert result[0]["fit_score"] == 77


def test_mixed_batch_in_prefilter_only_mode(isolated_data, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "1", "title": "Firefighter", "organization": "Big Health"},
        {"source": "linkedin", "job_id": "2", "title": "VP Information Technology", "organization": "Acme"},
    ], review_required=False)

    def _should_skip(job, profile):
        return None if job["job_id"] == "2" else {"layer": "deterministic", "reason": "emergency services role"}
    monkeypatch.setattr(fit_score_prefilter, "should_skip_scoring", _should_skip)
    score_calls = []
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: score_calls.append(job) or {"fit_score": 99, "fit_rationale": "x"})

    run_search.score_unscored_jobs(PROFILE, prefilter_only=True)

    assert score_calls == []  # neither job reaches score_job in this mode
    jobs = {j["job_id"]: j for j in job_store.load_jobs()}
    assert "fit_score" not in jobs["1"]
    assert "fit_score" not in jobs["2"]
