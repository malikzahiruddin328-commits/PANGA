"""2026-08-11: score_unscored_jobs() now runs every unscored job through
fit_score_prefilter before the real paid score_job() call. Covers the
wiring itself (prefiltered jobs never reach score_job, non-prefiltered
jobs still do, every skip gets logged, the job record itself is never
touched) - the prefilter module's own logic is covered in
test_fit_score_prefilter.py.

2026-08-12: these fixtures use "Firefighter" (not "Registered Nurse") as
the deterministic-layer example. search.exclusion_filter now runs inside
job_store.save_jobs() itself, earlier and cheaper than this prefilter, and
its clinical_domain rule catches "Registered Nurse" before the job ever
reaches the store - so a job with that title never gets this far to
exercise fit_score_prefilter's own deterministic layer at all. "Firefighter"
still matches fit_score_prefilter's own emergency-services exclusion
pattern (layer="deterministic") while not tripping either of
exclusion_filter's rules (not an IC-tier noun, not in its clinical
pattern), so it reaches score_unscored_jobs() and stays a meaningful,
independent test of this layer.

Every save_jobs() call below also passes review_required=False (2026-08-13
review-gate build) - these tests are about the prefilter/scoring wiring
specifically, not the review gate itself (see test_run_search_review_
gate.py for that), so every job here is set up already-"accepted" the
same way it always implicitly was before review_status existed at all.
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


def test_prefiltered_job_never_reaches_score_job(scoring_enabled, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "1", "title": "Firefighter", "organization": "Big Health"},
    ], review_required=False)
    score_calls = []
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: score_calls.append(job) or {"fit_score": 99, "fit_rationale": "x"})

    run_search.score_unscored_jobs(PROFILE)

    assert score_calls == []
    jobs = job_store.load_jobs()
    assert "fit_score" not in jobs[0]  # left exactly as unscored, not dropped or faked


def test_prefiltered_job_gets_logged_for_spot_check(scoring_enabled, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "1", "title": "Firefighter", "organization": "Big Health"},
    ], review_required=False)
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: {"fit_score": 99, "fit_rationale": "x"})

    run_search.score_unscored_jobs(PROFILE)

    entries = read_json(fit_score_prefilter.PREFILTER_LOG_PATH, default=[])
    assert len(entries) == 1
    assert entries[0]["job_id"] == "1"
    assert entries[0]["layer"] == "deterministic"


def test_non_prefiltered_job_still_gets_scored_normally(scoring_enabled, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "2", "title": "VP Information Technology", "organization": "Acme"},
    ], review_required=False)
    monkeypatch.setattr(fit_score_prefilter, "should_skip_scoring", lambda job, profile: None)
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: {"fit_score": 77, "fit_rationale": "solid match"})

    run_search.score_unscored_jobs(PROFILE)

    jobs = job_store.load_jobs()
    assert jobs[0]["fit_score"] == 77
    entries = read_json(fit_score_prefilter.PREFILTER_LOG_PATH, default=[])
    assert entries == []


def test_mixed_batch_only_skips_the_prefiltered_ones(scoring_enabled, monkeypatch):
    job_store.save_jobs([
        {"source": "linkedin", "job_id": "1", "title": "Firefighter", "organization": "Big Health"},
        {"source": "linkedin", "job_id": "2", "title": "VP Information Technology", "organization": "Acme"},
    ], review_required=False)
    scored_ids = []

    def _fake_score_job(job, profile):
        scored_ids.append(job["job_id"])
        return {"fit_score": 85, "fit_rationale": "strong"}

    monkeypatch.setattr(run_search, "score_job", _fake_score_job)

    run_search.score_unscored_jobs(PROFILE)

    assert scored_ids == ["2"]
    jobs = {j["job_id"]: j for j in job_store.load_jobs()}
    assert "fit_score" not in jobs["1"]
    assert jobs["2"]["fit_score"] == 85
