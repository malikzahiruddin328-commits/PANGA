from datetime import datetime, timedelta, timezone

import search.freshness_check as freshness_check
import search.job_store as job_store
import tailoring.applications as applications

_JOB = {
    "source": "Eisai",
    "job_id": "/job/Massachusetts-Cambridge/Some-Role_R1",
    "title": "Some Role",
    "organization": "Eisai",
    "fit_score": 80,
}


def _seed_job(**overrides):
    job = {**_JOB, **overrides}
    job_store.save_jobs([job])
    return job


def _patch_result(monkeypatch, value):
    """value is the bool|None check_posting_open() should return for every
    job this run - or a list to return a different value per call, in call
    order."""
    if isinstance(value, list):
        values = iter(value)
        monkeypatch.setattr(freshness_check, "check_posting_open", lambda job, api_sources: next(values))
    else:
        monkeypatch.setattr(freshness_check, "check_posting_open", lambda job, api_sources: value)


def test_below_min_score_never_checked(isolated_data, monkeypatch):
    _seed_job(fit_score=50)
    _patch_result(monkeypatch, False)

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()

    assert (checked, marked, pending, reopened) == (0, 0, 0, 0)


def test_decided_status_never_checked_or_touched(isolated_data, monkeypatch):
    job = _seed_job()
    applications.upsert_application(job["source"], job["job_id"], status="applied")
    _patch_result(monkeypatch, False)

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()

    assert checked == 0
    assert applications.get_application(job["source"], job["job_id"])["status"] == "applied"


def test_first_closed_observation_only_stages_pending(isolated_data, monkeypatch):
    job = _seed_job()
    _patch_result(monkeypatch, False)

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()

    assert (checked, marked, pending, reopened) == (1, 0, 1, 0)
    # A single observation must NOT touch applications.json at all yet.
    assert applications.get_application(job["source"], job["job_id"]) is None
    state = freshness_check._load_state()
    assert len(state) == 1
    assert state[0]["pending_since"] is not None
    assert state[0]["closed_confirmed_at"] is None


def test_second_consecutive_closed_observation_commits(isolated_data, monkeypatch):
    job = _seed_job()
    _patch_result(monkeypatch, False)
    freshness_check.check_and_mark_closed_postings()  # day 1 - stages pending

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()  # day 2

    assert (checked, marked, pending, reopened) == (1, 1, 0, 0)
    app = applications.get_application(job["source"], job["job_id"])
    assert app["status"] == freshness_check.CLOSED_STATUS
    state = freshness_check._load_state()
    assert state[0]["pending_since"] is None
    assert state[0]["closed_confirmed_at"] is not None


def test_open_after_pending_is_a_false_alarm_and_never_commits(isolated_data, monkeypatch):
    job = _seed_job()
    _patch_result(monkeypatch, False)
    freshness_check.check_and_mark_closed_postings()  # day 1 - stages pending

    _patch_result(monkeypatch, True)
    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()  # day 2 - open again

    assert marked == 0
    assert applications.get_application(job["source"], job["job_id"]) is None
    assert freshness_check._load_state() == []


def test_none_result_leaves_pending_state_untouched(isolated_data, monkeypatch):
    job = _seed_job()
    _patch_result(monkeypatch, False)
    freshness_check.check_and_mark_closed_postings()  # day 1 - stages pending
    state_after_day1 = freshness_check._load_state()

    _patch_result(monkeypatch, None)
    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()  # day 2 - ambiguous

    assert (marked, pending, reopened) == (0, 0, 0)
    assert freshness_check._load_state() == state_after_day1  # untouched, not reset and not committed

    _patch_result(monkeypatch, False)
    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()  # day 3 - closed again
    assert marked == 1  # the day-1 observation still counted - ambiguous didn't erase it


def test_already_closed_not_due_for_recheck_is_skipped(isolated_data, monkeypatch):
    job = _seed_job()
    applications.upsert_application(job["source"], job["job_id"], status=freshness_check.CLOSED_STATUS)
    freshness_check._commit_closed(job["source"], job["job_id"])  # closed_confirmed_at = now
    _patch_result(monkeypatch, True)  # would reopen it if actually checked

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()

    assert checked == 0
    assert applications.get_application(job["source"], job["job_id"])["status"] == freshness_check.CLOSED_STATUS


def test_already_closed_due_for_recheck_and_still_closed_refreshes_clock(isolated_data, monkeypatch):
    job = _seed_job()
    applications.upsert_application(job["source"], job["job_id"], status=freshness_check.CLOSED_STATUS)
    stale = (datetime.now(timezone.utc) - timedelta(days=freshness_check.RECHECK_CLOSED_AFTER_DAYS + 1)).isoformat()
    freshness_check._STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    from security.crypto_store import write_json
    write_json(freshness_check._STATE_PATH, [{
        "source": job["source"], "job_id": job["job_id"],
        "pending_since": None, "prior_status": "under review", "closed_confirmed_at": stale,
    }])
    _patch_result(monkeypatch, False)

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()

    assert checked == 1
    assert marked == 0  # still closed, not a NEW closure
    assert applications.get_application(job["source"], job["job_id"])["status"] == freshness_check.CLOSED_STATUS
    state = freshness_check._load_state()
    assert state[0]["closed_confirmed_at"] != stale  # clock refreshed


def test_reopened_posting_restores_prior_status(isolated_data, monkeypatch):
    job = _seed_job()
    applications.upsert_application(job["source"], job["job_id"], status="save for later")
    applications.upsert_application(job["source"], job["job_id"], status=freshness_check.CLOSED_STATUS)
    stale = (datetime.now(timezone.utc) - timedelta(days=freshness_check.RECHECK_CLOSED_AFTER_DAYS + 1)).isoformat()
    from security.crypto_store import write_json
    write_json(freshness_check._STATE_PATH, [{
        "source": job["source"], "job_id": job["job_id"],
        "pending_since": None, "prior_status": "save for later", "closed_confirmed_at": stale,
    }])
    _patch_result(monkeypatch, True)

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()

    assert (checked, reopened) == (1, 1)
    assert applications.get_application(job["source"], job["job_id"])["status"] == "save for later"
    assert freshness_check._load_state() == []


def test_legacy_closed_job_with_no_state_entry_is_rechecked_and_defaults_on_reopen(isolated_data, monkeypatch):
    """Simulates a job closed by the pre-2026-08-11 code, which never wrote
    a state entry at all - the exact ratchet bug being fixed."""
    job = _seed_job()
    applications.upsert_application(job["source"], job["job_id"], status=freshness_check.CLOSED_STATUS)
    assert freshness_check._load_state() == []  # no state entry, matching pre-fix data
    _patch_result(monkeypatch, True)

    checked, marked, pending, reopened = freshness_check.check_and_mark_closed_postings()

    assert (checked, reopened) == (1, 1)
    # No prior_status was ever recorded - falls back to "under review".
    assert applications.get_application(job["source"], job["job_id"])["status"] == "under review"
