"""tailoring.task_monitor (2026-08-17) - real backend for the Task Monitor
UI tab. Covers: which application records count as "active", the real
PID-liveness check (mocked - never shells out to a real OS process check
in this suite), staleness detection for both the subscription and paid-
build paths, and reset_stalled_task()'s recovery action."""

import os
from datetime import datetime, timedelta, timezone

import pytest

import search.job_store as job_store
import tailoring.applications as applications
import tailoring.task_monitor as task_monitor
from security.crypto_store import write_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds_ago_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _write_job(source="linkedin", job_id="1", title="VP IT", organization="Acme"):
    write_json(job_store.JOBS_PATH, [
        {"source": source, "job_id": job_id, "title": title, "organization": organization},
    ])


# --- get_active_tasks() ---


def test_get_active_tasks_empty_when_nothing_in_flight(isolated_data):
    assert task_monitor.get_active_tasks() == []


def test_get_active_tasks_includes_a_subscription_round_in_flight(isolated_data, monkeypatch):
    _write_job()
    applications.set_qa_status("linkedin", "1", "drafting", pid=4242, started_at=_now_iso())
    monkeypatch.setattr(task_monitor, "is_pid_alive", lambda pid: True)

    tasks = task_monitor.get_active_tasks()

    assert len(tasks) == 1
    task = tasks[0]
    assert task["kind"] == "subscription"
    assert task["status"] == "drafting"
    assert task["pid"] == 4242
    assert task["pid_alive"] is True
    assert task["stalled"] is False
    assert task["title"] == "VP IT"
    assert task["organization"] == "Acme"


def test_get_active_tasks_ignores_idle_and_awaiting_answers_records(isolated_data):
    _write_job()
    applications.set_qa_status("linkedin", "1", "awaiting_answers")

    assert task_monitor.get_active_tasks() == []


def test_get_active_tasks_flags_stalled_when_pid_is_dead(isolated_data, monkeypatch):
    """The whole point of this feature: a job whose stored status still
    says "drafting" but whose real OS process is confirmed gone must show
    up as stalled, not as "still working"."""
    _write_job()
    applications.set_qa_status("linkedin", "1", "drafting", pid=4242, started_at=_seconds_ago_iso(60))
    monkeypatch.setattr(task_monitor, "is_pid_alive", lambda pid: False)

    tasks = task_monitor.get_active_tasks()

    assert tasks[0]["stalled"] is True
    assert tasks[0]["pid_alive"] is False
    assert "no longer running" in tasks[0]["stall_reason"]


def test_get_active_tasks_does_not_flag_stalled_within_pid_dead_grace_window(isolated_data, monkeypatch):
    """Real race caught live-verifying this module (2026-08-17): a fast,
    fully successful real subscription round (~11s on real data) had
    already exited its `claude` subprocess while run_subscription_round()
    was still doing its own Python-side wrap-up (parsing, ATS scoring,
    persisting) - is_pid_alive() correctly reported False for that brief
    normal window, but the job was finishing, not stuck. pid_alive must
    still report the real, honest liveness check, but "stalled" must not
    flip true until PID_DEAD_GRACE_SECONDS has actually passed."""
    _write_job()
    applications.set_qa_status("linkedin", "1", "drafting", pid=4242, started_at=_now_iso())
    monkeypatch.setattr(task_monitor, "is_pid_alive", lambda pid: False)

    tasks = task_monitor.get_active_tasks()

    assert tasks[0]["pid_alive"] is False  # the real check result, never hidden
    assert tasks[0]["stalled"] is False    # but too soon to call it stalled yet


def test_get_active_tasks_flags_stalled_when_elapsed_exceeds_timeout(isolated_data, monkeypatch):
    """Even a PID that (implausibly) still reports alive is treated as
    stalled once it's run well past the reasoner's own hard timeout - a
    real backstop, not the primary detection path."""
    _write_job()
    applications.set_qa_status("linkedin", "1", "drafting", pid=4242, started_at="2020-01-01T00:00:00+00:00")
    monkeypatch.setattr(task_monitor, "is_pid_alive", lambda pid: True)

    tasks = task_monitor.get_active_tasks()

    assert tasks[0]["stalled"] is True
    assert "reasoner timeout" in tasks[0]["stall_reason"]


def test_get_active_tasks_includes_paid_build_lock(isolated_data):
    _write_job()
    applications.try_acquire_generation_lock("linkedin", "1")

    tasks = task_monitor.get_active_tasks()

    assert len(tasks) == 1
    task = tasks[0]
    assert task["kind"] == "paid_build"
    assert task["pid"] == os.getpid()
    # No independently-checkable subprocess for the paid, in-process API
    # path - pid_alive is explicitly None (not True/False), distinct from
    # the subscription path's real liveness check, not the same weaker
    # guarantee dressed up as equivalent.
    assert task["pid_alive"] is None
    assert task["stalled"] is False


def test_get_active_tasks_flags_stalled_paid_build_past_lock_staleness_window(isolated_data):
    _write_job()
    applications.try_acquire_generation_lock("linkedin", "1")
    app = applications.get_application("linkedin", "1")
    apps = applications.load_applications()
    for a in apps:
        if a["source"] == "linkedin" and a["job_id"] == "1":
            a["generation_lock_acquired_at"] = "2020-01-01T00:00:00+00:00"
    applications._save_all(apps)

    tasks = task_monitor.get_active_tasks()

    assert tasks[0]["stalled"] is True
    assert "20 minutes" in tasks[0]["stall_reason"]


def test_get_active_tasks_does_not_double_report_a_subscription_rounds_own_lock(isolated_data, monkeypatch):
    """Real bug caught live-verifying this module (2026-08-17): run_
    subscription_round() acquires the SAME per-(source, job_id)
    generation lock the paid path uses (deliberately, per subscription_
    resume_qa.py's own docstring - it's the same shared "don't let two
    drafts race" guard, not a second lock). Since only one caller can
    ever hold that single lock at a time, a subscription round and a
    paid build can never BOTH be genuinely active for the same job
    simultaneously - if the lock is held while a subscription round's
    own status is active, that lock IS this round's lock, not evidence
    of a second, independent paid build also running. Must report ONE
    row (subscription), not two rows for the same real operation."""
    _write_job()
    applications.set_qa_status("linkedin", "1", "drafting", pid=4242, started_at=_now_iso())
    monkeypatch.setattr(task_monitor, "is_pid_alive", lambda pid: True)
    apps = applications.load_applications()
    apps[0]["generation_lock_acquired_at"] = _now_iso()
    applications._save_all(apps)

    tasks = task_monitor.get_active_tasks()

    assert len(tasks) == 1
    assert tasks[0]["kind"] == "subscription"


def test_get_active_tasks_falls_back_to_untitled_job_when_no_job_record(isolated_data):
    # No _write_job() call - the application record exists but the job
    # itself isn't in job_store (e.g. archived/removed) - must not crash.
    applications.set_qa_status("linkedin", "999", "drafting", pid=1, started_at=_now_iso())

    tasks = task_monitor.get_active_tasks()

    assert tasks[0]["title"] == "(untitled job)"


# --- is_pid_alive() ---


def test_is_pid_alive_false_for_none():
    assert task_monitor.is_pid_alive(None) is False


def test_is_pid_alive_true_for_own_process():
    # This test process itself is definitely alive - a real, non-mocked
    # liveness check against the current interpreter's own PID.
    assert task_monitor.is_pid_alive(os.getpid()) is True


def test_is_pid_alive_false_for_a_pid_that_almost_certainly_does_not_exist():
    # Not a guaranteed-safe PID on every possible OS, but a very high,
    # round PID number is a reasonable real-world proxy for "does not
    # exist" without needing to actually spawn and kill a real process.
    assert task_monitor.is_pid_alive(999_999_999) is False


# --- reset_stalled_task() ---


def test_reset_stalled_task_clears_subscription_status(isolated_data):
    _write_job()
    applications.set_qa_status("linkedin", "1", "drafting", pid=4242, started_at=_now_iso())

    task_monitor.reset_stalled_task("linkedin", "1", "subscription")

    app = applications.get_application("linkedin", "1")
    assert app["subscription_qa_status"] is None
    assert app["subscription_qa_pid"] is None


def test_reset_stalled_task_also_releases_the_shared_generation_lock_for_subscription(isolated_data):
    """Real bug caught live-verifying this module (2026-08-17): a
    subscription round acquires the SAME generation lock the paid path
    uses (see task_monitor.py's own comments). Resetting only subscription_
    qa_status and leaving that lock held would make "reset & retry"
    clear the stalled row from view while a genuine retry still failed as
    "locked" for up to 20 more minutes - silently defeating the point of
    an immediate reset."""
    _write_job()
    applications.try_acquire_generation_lock("linkedin", "1")
    applications.set_qa_status("linkedin", "1", "drafting", pid=4242, started_at=_now_iso())

    task_monitor.reset_stalled_task("linkedin", "1", "subscription")

    app = applications.get_application("linkedin", "1")
    assert app.get("generation_lock_acquired_at") is None


def test_reset_stalled_task_releases_paid_build_lock(isolated_data):
    _write_job()
    applications.try_acquire_generation_lock("linkedin", "1")

    task_monitor.reset_stalled_task("linkedin", "1", "paid_build")

    app = applications.get_application("linkedin", "1")
    assert app.get("generation_lock_acquired_at") is None


def test_reset_stalled_task_rejects_unknown_kind(isolated_data):
    with pytest.raises(ValueError):
        task_monitor.reset_stalled_task("linkedin", "1", "not_a_real_kind")
