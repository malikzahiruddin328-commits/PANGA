"""Task Monitor (2026-08-17) - real answer to a problem hit live twice
today: with a background subscription draft/redraft call or the paid
final-build lock in flight, `subscription_qa_status` (or the generation
lock) only ever said a string like "drafting" - there was no way to check
whether that was still genuinely working or actually hung. A manual
investigation had to run `Get-Process claude` and got back 16 unlabeled
processes with no way to tell which belonged to which job.

This module is the real backend for ui/app.py's Task Monitor tab: it reads
every application record currently in a non-idle state, cross-checks the
REAL stored PID (see tailoring.applications.set_qa_status()'s pid/
started_at params, stamped from reasoner_cli.run_claude_cli()'s on_start
callback) against whether that OS process is ACTUALLY still alive right
now - not just trusting the stored status string, which is the entire
point. A genuinely-stuck job (status says "drafting" but the process
exited or was killed) shows up as stalled, not as "still working".

Two real, distinct classes of in-flight work, deliberately not conflated:

1. Subscription rounds (subscription_qa_status in QA_STATUSES_WITH_PROCESS)
   - a real, separately-launched `claude` CLI OS subprocess exists, with
   its own PID this module can independently check with is_pid_alive().

2. The one paid final-build call (bulk_generate.generate_for_job() ->
   tailoring.drafting.generate_documents()) - a direct in-process
   `anthropic` SDK call, not a subprocess. There is no separate OS PID to
   check for this path; the only "process" is this Streamlit app's own
   Python interpreter. Reported here using the SAME generation lock
   (applications.try_acquire_generation_lock/release_generation_lock) the
   paid path already uses, with os.getpid() shown as an explicit "this is
   the app process, not an independently-verifiable subprocess" label
   rather than something a liveness check can meaningfully confirm/deny -
   its "is it alive" answer is only ever "yes, because this code is
   running", which is a different, weaker guarantee than checking an
   independent subprocess and this view says so rather than papering over
   the distinction.
"""

import os
import subprocess
from datetime import datetime, timezone

from search.job_store import load_jobs
from tailoring.applications import (
    QA_STATUSES_WITH_PROCESS,
    load_applications,
    release_generation_lock,
    set_qa_status,
)

# Real ceiling, not a target: reasoner_cli.DEFAULT_TIMEOUT_SECONDS (300s) is
# the CLI subprocess's own hard timeout, after which run_claude_cli() itself
# raises and the caller marks the round "failed" - a round should never
# still show "drafting" much past that under normal operation. Generous
# buffer on top (not the bare 300s) because on_start() fires as soon as the
# subprocess launches, slightly before the timeout clock inside
# subprocess.communicate() effectively starts counting, and because a
# legitimately slow real call already measured 39-63s on real full-profile
# drafts elsewhere in this app - this is a "call this stale even if the PID
# somehow still shows alive" backstop, not the primary detection mechanism
# (is_pid_alive() below is that).
SUBSCRIPTION_STALE_AFTER_SECONDS = 360

# Real race found live-verifying this module (2026-08-17): once the
# `claude` subprocess itself exits (success OR failure), there's a real,
# normally-brief window where run_subscription_round() is still doing its
# own Python-side work - parse_json_reply(), _finalize_resume_draft()'s
# real ATS scoring, upsert_application(), sync_workspace_documents() -
# before it clears subscription_qa_status/pid. During that window
# is_pid_alive(pid) correctly reports False (the subprocess really has
# exited), but the job is finishing up normally, not stuck. Without a
# grace period, a fast, fully successful real round observed live (~11s)
# was misreported as "stalled" the instant the underlying process exited.
# A genuinely hung job stays dead far longer than this - this only
# absorbs the normal handoff window, it doesn't mask a real hang (that's
# still caught, just a few seconds later than the instant of process
# exit).
PID_DEAD_GRACE_SECONDS = 15

# Mirrors applications._GENERATION_LOCK_STALE_AFTER_MINUTES (20 min) - kept
# as a separate constant here (not imported) since it's read-only display
# logic for this view, not the lock's own enforcement, which still lives in
# try_acquire_generation_lock() itself.
PAID_BUILD_STALE_AFTER_MINUTES = 20


def is_pid_alive(pid: int | None) -> bool:
    """Real, live check of whether an OS process with this PID currently
    exists - not inferred from the stored status string. No extra
    dependency (psutil isn't installed in this project's venv) - uses
    `tasklist` on Windows (this app's only real deployment target per
    CLAUDE.md's Windows-specific guidance elsewhere) with a POSIX
    os.kill(pid, 0) fallback for any other platform this ever runs on.
    Returns False for None/invalid input rather than raising - a job with
    no recorded PID yet (a subscription round that hasn't reached
    on_start() yet) is correctly treated as "nothing to check", not an
    error."""
    if not pid:
        return False
    if os.name == "nt":
        try:
            # /NH = no column header, /FI filters server-side so the
            # output is either exactly one matching line or none - cheap
            # even called once per row in the monitor view (this app's
            # applications.json is nowhere near large enough for a
            # per-render tasklist call to matter, and the Task Monitor
            # view only ever has a handful of non-idle rows at once).
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return str(pid) in result.stdout
        except (OSError, subprocess.SubprocessError):
            # tasklist itself failing to run is a real "can't verify"
            # case, not evidence the process is dead - a false "alive"
            # here just means the monitor falls back to the timeout-based
            # staleness check instead of the PID check, never the reverse
            # (never silently reports a genuinely-running job as stalled
            # just because the verification tool itself hiccuped).
            return True
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but owned by another user - still alive.
            return True


def _elapsed_seconds(started_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - started).total_seconds()


def format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def get_active_tasks() -> list[dict]:
    """Every application record currently in a non-idle state - a real
    subscription round in flight, or the paid-build generation lock held -
    with real PID/elapsed/liveness computed fresh on every call (never
    cached; this is exactly the "not a black box" state this view exists
    to show). One row per active job, each shaped:

    {"source", "job_id", "title", "organization", "kind": "subscription" |
     "paid_build", "status": str, "pid": int | None, "started_at": str |
     None, "elapsed_seconds": float | None, "elapsed_label": str,
     "pid_alive": bool, "stalled": bool, "stall_reason": str | None}

    stalled=True means the status string claims this job is active but the
    real evidence says otherwise (PID confirmed dead, or - for the paid
    path, which has no independently-checkable PID - the lock has been
    held well past PAID_BUILD_STALE_AFTER_MINUTES). This is the whole
    point of the feature: a genuinely-stuck job surfaces as stalled here
    even while its stored status string still says "drafting"."""
    jobs_by_key = {(j.get("source"), j.get("job_id")): j for j in load_jobs()}
    rows = []
    for app in load_applications():
        source, job_id = app.get("source"), app.get("job_id")
        job = jobs_by_key.get((source, job_id), {})
        title = job.get("title") or app.get("title") or "(untitled job)"
        organization = job.get("organization") or "(unknown org)"

        qa_status = app.get("subscription_qa_status")
        if qa_status in QA_STATUSES_WITH_PROCESS:
            pid = app.get("subscription_qa_pid")
            started_at = app.get("subscription_qa_started_at")
            elapsed = _elapsed_seconds(started_at)
            alive = is_pid_alive(pid)
            # PID_DEAD_GRACE_SECONDS: a dead PID only counts as stalled
            # once it's stayed dead-and-unfinished for a bit - see that
            # constant's own comment for the real race this closes (the
            # normal, brief window between the subprocess exiting and
            # run_subscription_round()'s own Python-side wrap-up clearing
            # this status).
            pid_confirmed_stalled = pid is not None and not alive and (
                elapsed is not None and elapsed > PID_DEAD_GRACE_SECONDS
            )
            stalled = pid_confirmed_stalled or (
                elapsed is not None and elapsed > SUBSCRIPTION_STALE_AFTER_SECONDS
            )
            stall_reason = None
            if pid_confirmed_stalled:
                stall_reason = f"PID {pid} is no longer running"
            elif elapsed is not None and elapsed > SUBSCRIPTION_STALE_AFTER_SECONDS:
                stall_reason = f"running longer than the {SUBSCRIPTION_STALE_AFTER_SECONDS}s reasoner timeout"
            rows.append({
                "source": source, "job_id": job_id, "title": title, "organization": organization,
                "kind": "subscription", "status": qa_status, "pid": pid, "started_at": started_at,
                "elapsed_seconds": elapsed, "elapsed_label": format_elapsed(elapsed),
                "pid_alive": alive if pid is not None else None, "stalled": stalled, "stall_reason": stall_reason,
            })

        # Real bug caught live-verifying this module (2026-08-17): the
        # generation lock is NOT paid-build-specific - subscription_
        # resume_qa.run_subscription_round() acquires the exact SAME
        # per-(source, job_id) lock (applications.try_acquire_generation_
        # lock) the paid path uses, by design (see that module's own top
        # docstring - it's the same shared "don't let two drafts race to
        # write resume_text" guard, deliberately reused rather than a
        # second lock). Without this check, a subscription round in
        # flight showed up as BOTH a "subscription" row above AND a
        # separate, misleading "paid_build" row here for the exact same
        # single operation - the lock only genuinely represents a paid
        # build when it's held WITHOUT a subscription round also active
        # for this job right now.
        lock_held_since = app.get("generation_lock_acquired_at")
        if lock_held_since and qa_status not in QA_STATUSES_WITH_PROCESS:
            elapsed = _elapsed_seconds(lock_held_since)
            stalled = elapsed is not None and elapsed > PAID_BUILD_STALE_AFTER_MINUTES * 60
            rows.append({
                "source": source, "job_id": job_id, "title": title, "organization": organization,
                "kind": "paid_build", "status": "generating (paid, final build)",
                # No independently-checkable subprocess for this path - see
                # this module's own top docstring. os.getpid() is THIS
                # Streamlit process, shown as context, not a liveness
                # signal (it's always "alive" by definition, since this
                # code is what's running it).
                "pid": os.getpid(), "started_at": lock_held_since,
                "elapsed_seconds": elapsed, "elapsed_label": format_elapsed(elapsed),
                "pid_alive": None, "stalled": stalled,
                "stall_reason": f"generation lock held longer than {PAID_BUILD_STALE_AFTER_MINUTES} minutes" if stalled else None,
            })
    return rows


def reset_stalled_task(source: str, job_id: str, kind: str) -> None:
    """The "stalled - reset & retry" action: clears whichever real stuck
    state this row represents so the job is unblocked and can be retried
    from the UI, same recovery an already-stale lock would eventually
    self-heal into on its own (try_acquire_generation_lock()'s own 20-
    minute staleness check, set_qa_status(None) for the subscription
    path) - this just lets Zahir trigger it immediately once the Task
    Monitor has confirmed the process is really dead, rather than waiting
    out the ceiling.

    Real bug caught live-verifying this module (2026-08-17): resetting a
    stalled "subscription" round used to clear ONLY subscription_qa_status,
    leaving the SAME generation lock that round also acquired (see get_
    active_tasks()'s own comment on why one lock backs both paths) still
    held - "reset & retry" would clear the stalled row from view but a
    genuine retry click right after would still fail as "locked" for up to
    PAID_BUILD_STALE_AFTER_MINUTES more minutes, silently defeating the
    entire point of an immediate reset. Both kinds now always release the
    lock too - release_generation_lock() is a no-op if it isn't actually
    held (see that function's own docstring), so this is safe even when
    the lock genuinely belongs to an unrelated, still-active operation on
    a DIFFERENT job (this only ever touches this one (source, job_id))."""
    if kind == "subscription":
        set_qa_status(source, job_id, None)
        release_generation_lock(source, job_id)
    elif kind == "paid_build":
        release_generation_lock(source, job_id)
    else:
        raise ValueError(f"unknown task kind {kind!r}")
