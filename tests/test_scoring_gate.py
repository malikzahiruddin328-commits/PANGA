"""2026-08-18: score_unscored_jobs() must be a real no-op while scoring is
paused, independent of review_status - see scoring_gate.py's own module
docstring for the real incident this closes (a direct script run spent
$3.14 on fit_score the same night scoring was believed "on ice" purely
because the scheduled task happened to be off, with no gate of its own)."""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_search  # noqa: E402

import scoring_gate  # noqa: E402
from search import job_store  # noqa: E402

PROFILE = {"target_title_framings": [{"summary": "IT/data/cybersecurity executive."}]}


def test_paused_by_default_when_settings_file_is_missing(isolated_data):
    # isolated_data points scoring_gate.SETTINGS_PATH at a tmp_path file
    # that doesn't exist - fail-safe default must be paused, not open.
    assert scoring_gate.is_scoring_paused() is True


def test_scoring_paused_false_in_settings_unpauses(isolated_data):
    scoring_gate.SETTINGS_PATH.write_text("scoring_paused: false\n", encoding="utf-8")
    assert scoring_gate.is_scoring_paused() is False


def test_scoring_paused_true_explicitly_in_settings_stays_paused(isolated_data):
    scoring_gate.SETTINGS_PATH.write_text("scoring_paused: true\n", encoding="utf-8")
    assert scoring_gate.is_scoring_paused() is True


def test_missing_key_in_an_existing_settings_file_defaults_paused(isolated_data):
    # A settings.yaml that exists but predates this key (every real one
    # right now) must still default to paused, not unpaused-by-omission.
    scoring_gate.SETTINGS_PATH.write_text("target_roles: []\n", encoding="utf-8")
    assert scoring_gate.is_scoring_paused() is True


def test_score_unscored_jobs_is_a_true_noop_while_paused(isolated_data, monkeypatch):
    # Default isolated_data (no scoring_enabled opt-in) - fail-safe paused.
    job_store.save_jobs([{"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}])
    job_store.set_review_status("linkedin", "1", "accepted")
    score_calls = []
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: score_calls.append(job) or {"fit_score": 90, "fit_rationale": "x"})

    result = run_search.score_unscored_jobs(PROFILE)

    assert result == []
    assert score_calls == []
    assert "fit_score" not in job_store.load_jobs()[0]


def test_score_unscored_jobs_runs_normally_when_explicitly_unpaused(scoring_enabled, monkeypatch):
    job_store.save_jobs([{"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}])
    job_store.set_review_status("linkedin", "1", "accepted")
    monkeypatch.setattr(run_search, "score_job", lambda job, profile: {"fit_score": 90, "fit_rationale": "strong"})

    run_search.score_unscored_jobs(PROFILE)

    assert job_store.load_jobs()[0]["fit_score"] == 90


def test_paused_gate_is_independent_of_review_status(isolated_data):
    # An auto-accepted job (e.g. job_alert_scan.py's redesigned
    # review_required=False) must still not be scored while paused -
    # accepted and scorable are deliberately separate concepts now.
    job_store.save_jobs([{"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}], review_required=False)
    assert job_store.load_jobs()[0]["review_status"] == "accepted"
    assert scoring_gate.is_scoring_paused() is True
