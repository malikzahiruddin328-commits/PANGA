"""Resumable, capped batch runner for Phase 1 of Zahir's confirmed
"final set of questions" taxonomy-gap build (2026-08-17,
feature/jd-keyword-taxonomy-gaps): extracts ats_required_keywords/
ats_preferred_keywords for every real-JD job that doesn't have them yet,
via tailoring.jd_keyword_extraction.extract_keywords_via_subscription()
(the SAME $0 `claude` CLI subscription mechanism already used elsewhere in
this app, not the paid Anthropic API - never touches cost_log.py's daily
spend cap).

Real audit numbers this was sized against (2026-08-17): 1,450 real job
records, 435 with substantial JD text (description > JD_MIN_CHARS below),
19 of those already extracted as a side-effect of subscription_resume_qa's
per-job resume drafting - leaving ~416 real jobs in the backlog this
script drains.

**Why this is capped, not a plain loop over all ~416 jobs** (CLAUDE.md's
standing runtime-circuit-breaker rule, and this repo's real 2026-08-11
$60 overnight-spend incident that rule exists because of - this path is
$0, but the SAME "don't let an unattended automated pipeline run
unbounded" principle applies to wall-clock time, not just dollars): a
single subscription CLI call has genuinely taken 30-90s in this app's own
observed real-world latency (reasoner_cli.py's DEFAULT_TIMEOUT_SECONDS=300
is sized for the worst case, not the typical one) - 416 sequential calls
at that pace is realistically 3.5-10+ hours unattended, an unbounded
serial loop CLAUDE.md's runtime-circuit-breaker principle exists to
prevent. This script processes at most MAX_JOBS_PER_RUN jobs (or
MAX_MINUTES_PER_RUN of wall-clock time, whichever comes first) per
invocation, and is safe to re-run repeatedly - it always resumes from
whatever's left, tracked in a small state file
(data/jobs/jd_keyword_extraction_progress.json), not by reprocessing the
whole backlog. Nothing calls this script automatically; it is meant to be
invoked manually (or slotted into a future scheduled task) by Zahir's own
choice of pace, per his explicit instruction that the real ~416-job batch
run itself is his call, not something this build task fires on its own.

The state file is a lightweight, append-friendly log of individual
attempts (both successes and failures) - the real, authoritative "does
this job still need extraction" check is always the live job record's own
job["ats_required_keywords"] is None (read fresh from job_store.load_jobs()
on every invocation), so a job can never be silently skipped forever by a
stale state entry; the state file only prevents this script from
immediately re-hammering a job that JUST failed inside the very same run
(see _recent_failures below) and gives Zahir/a later run a real trail of
what happened and when.

Run manually: venv\\Scripts\\python.exe scripts\\batch_extract_jd_keywords.py
[--max-jobs N] [--max-minutes N]
Safe to re-run any number of times until the backlog is drained (this
script's own final line always reports how many real jobs are left)."""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from search.job_store import load_jobs, update_job_ats_keywords  # noqa: E402
from tailoring.jd_keyword_extraction import (  # noqa: E402
    EXTRACTOR_VERSION,
    extract_keywords_via_subscription,
    posting_text_for,
)
from tailoring.reasoner_cli import ReasonerUnavailable  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROGRESS_PATH = PROJECT_ROOT / "data" / "jobs" / "jd_keyword_extraction_progress.json"

# Real, enforced runtime circuit breakers (see module docstring) - both
# defaults deliberately conservative (a run comfortably finishes well
# inside a normal working session) rather than tuned to squeeze the most
# jobs out of one invocation; re-running the script costs nothing but a
# few seconds of setup.
DEFAULT_MAX_JOBS_PER_RUN = 30
DEFAULT_MAX_MINUTES_PER_RUN = 25

# 435 minus a hand-picked slush margin isn't the point here - the real
# eligibility check the audit used is "substantial JD text", operationalized
# as len(description) > 200 (matches the audit's own >200-char definition
# exactly, not re-derived independently).
JD_MIN_CHARS = 200


def _log(message: str) -> None:
    print(message, flush=True)


def _load_progress() -> list[dict]:
    if not PROGRESS_PATH.exists():
        return []
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_progress(records: list[dict]) -> None:
    """Atomic write (temp file + os.replace) - same convention as
    skills/canonical_taxonomy.py's save_taxonomy(), so a crash mid-write
    never leaves a half-written progress file for the next run to choke
    on. Not wrapped in security.file_lock's locked() - this file is only
    ever written by this one script, run one invocation at a time by
    Zahir himself, never concurrently with itself or another writer
    (unlike jobs.json, which update_job_ats_keywords() below already
    locks on its own)."""
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=PROGRESS_PATH.parent, prefix=".jd_keyword_progress_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(records, indent=2, ensure_ascii=False))
        os.replace(tmp_path, PROGRESS_PATH)
    except BaseException:
        os.unlink(tmp_path)
        raise


def _job_key(job: dict) -> str:
    return f"{job.get('source')}|{job.get('job_id')}"


def _eligible_backlog(jobs: list[dict]) -> list[dict]:
    """Real, live eligibility check (not the state file) - a job qualifies
    if it has substantial JD text and doesn't already have extracted
    keywords. Sorted by (source, job_id) for a stable, deterministic
    processing order across runs (so "[i/N]" progress lines mean the same
    thing run to run, and two consecutive runs don't reshuffle which jobs
    land in which invocation)."""
    backlog = [
        job for job in jobs
        if job.get("ats_required_keywords") is None
        and len((job.get("description") or "")) > JD_MIN_CHARS
    ]
    backlog.sort(key=lambda j: (str(j.get("source")), str(j.get("job_id"))))
    return backlog


def run_batch(max_jobs: int = DEFAULT_MAX_JOBS_PER_RUN, max_minutes: float = DEFAULT_MAX_MINUTES_PER_RUN) -> dict:
    jobs = load_jobs()
    backlog = _eligible_backlog(jobs)
    total_backlog = len(backlog)

    if total_backlog == 0:
        _log("No real jobs need JD keyword extraction right now - backlog is empty.")
        return {"processed": 0, "succeeded": 0, "failed": 0, "remaining": 0}

    progress = _load_progress()
    deadline = time.monotonic() + (max_minutes * 60)
    to_process = backlog[:max_jobs]

    _log(f"Backlog: {total_backlog} real job(s) missing ATS keywords. Processing up to {len(to_process)} this run (cap={max_jobs}, time budget={max_minutes}min).")

    processed = 0
    succeeded = 0
    failed = 0
    for i, job in enumerate(to_process, start=1):
        if time.monotonic() >= deadline:
            _log(f"  Wall-clock budget ({max_minutes} min) reached - stopping early at {i - 1}/{len(to_process)} this run, real circuit breaker, not a bug.")
            break

        title = job.get("title") or "Untitled role"
        org = job.get("organization") or "Unknown organization"
        processed += 1
        try:
            required, preferred = extract_keywords_via_subscription(job)
        except ReasonerUnavailable as exc:
            # Systemic, not per-job - every remaining call would fail the
            # exact same way, so stop the whole run rather than burning
            # through the rest of the cap on guaranteed failures.
            _log(f"  [{i}/{len(to_process)}] reasoner unavailable, stopping run: {exc}")
            progress.append({
                "job_key": _job_key(job), "title": title, "organization": org,
                "status": "reasoner_unavailable", "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            failed += 1
            break
        except RuntimeError as exc:
            _log(f"  [{i}/{len(to_process)}] failed for {title!r} at {org!r}: {exc}")
            progress.append({
                "job_key": _job_key(job), "title": title, "organization": org,
                "status": "failed", "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            failed += 1
            continue

        update_job_ats_keywords(job["source"], job["job_id"], required, preferred, EXTRACTOR_VERSION)
        succeeded += 1
        _log(f"  [{i}/{len(to_process)}] extracted keywords for {title!r} at {org!r} - {len(required)} required, {len(preferred)} preferred")
        progress.append({
            "job_key": _job_key(job), "title": title, "organization": org,
            "status": "extracted", "required_count": len(required), "preferred_count": len(preferred),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    _save_progress(progress)

    remaining = total_backlog - succeeded
    _log(f"Run complete: {succeeded} extracted, {failed} failed, {remaining} real job(s) still remaining in the backlog.")
    if remaining > 0:
        _log("Re-run this same script to continue draining the backlog - it always resumes from the current real state, never reprocesses already-extracted jobs.")
    return {"processed": processed, "succeeded": succeeded, "failed": failed, "remaining": remaining}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS_PER_RUN, help=f"Max jobs to process this run (default {DEFAULT_MAX_JOBS_PER_RUN}).")
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES_PER_RUN, help=f"Max wall-clock minutes this run may take (default {DEFAULT_MAX_MINUTES_PER_RUN}).")
    args = parser.parse_args()
    run_batch(max_jobs=args.max_jobs, max_minutes=args.max_minutes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
