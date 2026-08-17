"""One-off retroactive sweep (2026-08-17, feature/pharma-regulatory-
exclusions): applies the branch's new exclusion patterns (medical writer /
CRA / clinical data manager extension to layer 2, and the new layer 11
pharma Regulatory Affairs domain exclusion) against jobs already sitting in
the PENDING review queue - these titles reached the queue before the new
patterns existed, so they were never checked against them at save time.

Archive-not-delete (Zahir's standing preference, same pattern
job_store.archive_stale_jobs() already uses): a matched job is MOVED (not
copied, not deleted) from jobs.json into jobs-archive.json. Nothing is ever
permanently destroyed - a swept job can always be restored by moving its
record back.

Scope: only jobs with review_status == "pending" are candidates - anything
already reviewed/accepted/rejected, in the basket, or archived is out of
scope for this sweep (it was already judged one way or another).

Safety:
  - Takes a timestamped backup of BOTH jobs.json and jobs-archive.json
    before writing anything (job_store.backup_jobs_file() only backs up
    jobs.json, so this script backs up the archive file itself the same
    way).
  - Before archiving each match, checks applications.json for prior
    application/basket/resume engagement (same PROTECTED_APPLICATION_
    STATUSES / resume_text check job_store.archive_stale_jobs() already
    uses) and the job's own in_basket flag - if any prior work is found,
    the job is still archived (Zahir's standing "get non-relevant roles
    out" instruction takes priority over stale/no-fit work-in-progress),
    but it's printed prominently as a WARNING so the real work-in-progress
    is not silently lost from view.
  - dry_run=True by default; must pass --execute to actually write.

Usage (from repo root, project venv):
    "<venv>/Scripts/python.exe" scripts/retroactive_pharma_regulatory_sweep.py           # dry run
    "<venv>/Scripts/python.exe" scripts/retroactive_pharma_regulatory_sweep.py --execute  # writes
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import shutil  # noqa: E402

from search import exclusion_filter  # noqa: E402
from search.job_store import PASSED_APPLICATION_STATUSES, PROTECTED_APPLICATION_STATUSES  # noqa: E402
from security.crypto_store import read_json, write_json  # noqa: E402
from security.file_lock import locked  # noqa: E402
from tailoring.applications import load_applications  # noqa: E402

import os  # noqa: E402

# DATA_ROOT overridable via PANGA_DATA_ROOT env var: this script's own
# worktree checkout has no data/ dir of its own (gitignored, worktrees
# don't share it) - the real jobs.json/jobs-archive.json live only in the
# main checkout. Running with the new code (this worktree's src/, imported
# above) against the real data (main checkout's data/) needs this override;
# defaults to this checkout's own data/ dir for a self-contained worktree
# that does have one.
DATA_ROOT = Path(os.environ.get("PANGA_DATA_ROOT", str(Path(__file__).resolve().parents[1])))
JOBS_PATH = DATA_ROOT / "data" / "jobs" / "jobs.json"
ARCHIVE_PATH = DATA_ROOT / "data" / "jobs" / "jobs-archive.json"

# CAUTION (found the hard way, 2026-08-17): exclusion_filter.EXCLUSION_LOG_PATH
# is a module-level constant computed from that module's OWN __file__ location
# (Path(__file__).resolve().parents[2]) - when this script imports
# exclusion_filter from a worktree's src/ (to run new/unmerged code) while
# DATA_ROOT points elsewhere (the real data), that constant silently resolves
# to the WORKTREE's own (usually nonexistent) data/ dir, not DATA_ROOT - a
# real exclusion still gets computed and the job still gets archived
# correctly (this script's own JOBS_PATH/ARCHIVE_PATH above are unaffected),
# but the audit-log entry silently goes to the wrong file, creating a stray
# data/ directory inside the worktree. Patched here so log_exclusions()
# writes to the same real DATA_ROOT this script itself uses.
exclusion_filter.EXCLUSION_LOG_PATH = DATA_ROOT / "data" / "jobs" / "search_exclusion_log.json"


def backup_both(suffix: str) -> tuple[Path, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    jobs_backup = JOBS_PATH.parent / f"jobs.bak-{suffix}-{timestamp}.json"
    archive_backup = ARCHIVE_PATH.parent / f"jobs-archive.bak-{suffix}-{timestamp}.json"
    if JOBS_PATH.exists():
        shutil.copy2(JOBS_PATH, jobs_backup)
    if ARCHIVE_PATH.exists():
        shutil.copy2(ARCHIVE_PATH, archive_backup)
    return jobs_backup, archive_backup


def main():
    execute = "--execute" in sys.argv

    with locked("jobs"):
        jobs = read_json(JOBS_PATH, default=[])
        apps = load_applications()
        apps_by_key = {(a.get("source"), a.get("job_id")): a for a in apps}

        custom_exclusions = exclusion_filter.load_custom_title_exclusions()

        pending_before = [j for j in jobs if j.get("review_status") == "pending"]
        print(f"Total jobs.json records: {len(jobs)}")
        print(f"Pending-review queue before sweep: {len(pending_before)}")
        print()

        to_archive: list[dict] = []
        to_log: list[tuple[dict, dict]] = []
        remaining: list[dict] = []
        flagged_prior_work: list[dict] = []

        for job in jobs:
            if job.get("review_status") != "pending":
                remaining.append(job)
                continue

            exclusion = exclusion_filter.check_exclusion(job, custom_exclusions)
            # Only the two new rules this branch adds count as "newly
            # matched" for this sweep - a pending job already excluded by
            # an older rule would have been caught by a prior sweep, not
            # this one; scoping strictly avoids accidentally re-processing
            # unrelated matches.
            if exclusion is None or exclusion["rule"] not in (
                "clinical_domain",
                "regulatory_affairs_domain",
            ):
                remaining.append(job)
                continue

            key = (job.get("source"), job.get("job_id"))
            app = apps_by_key.get(key)
            prior_work = []
            if job.get("in_basket"):
                prior_work.append("in_basket=True")
            if app is not None and app.get("status") in PROTECTED_APPLICATION_STATUSES:
                prior_work.append(f"application status={app.get('status')!r}")
            if app is not None and app.get("status") in PASSED_APPLICATION_STATUSES:
                prior_work.append(f"application status={app.get('status')!r} (explicit pass)")
            if app is not None and app.get("resume_text") and str(app.get("resume_text")).strip():
                prior_work.append("real resume_text generated")

            if prior_work:
                flagged_prior_work.append(job)
                print(
                    f"WARNING: prior work found on a job now matched for exclusion - "
                    f"{job.get('title')!r} @ {job.get('organization')} "
                    f"[{', '.join(prior_work)}] -> archiving anyway per Zahir's standing "
                    f"'get non-relevant roles out' instruction (archive-not-delete, "
                    f"recoverable from jobs-archive.json)"
                )

            to_archive.append(job)
            to_log.append((job, exclusion))

        print()
        print(f"Newly matched pending jobs: {len(to_archive)}")
        by_rule: dict[str, int] = {}
        for _, exclusion in to_log:
            by_rule[exclusion["rule"]] = by_rule.get(exclusion["rule"], 0) + 1
        for rule, count in by_rule.items():
            print(f"  {rule}: {count}")
        if flagged_prior_work:
            print(f"\n{len(flagged_prior_work)} of these had prior application/basket/resume work (see WARNINGs above).")

        print()
        for job, exclusion in to_log:
            print(f"  - {job.get('title')!r} @ {job.get('organization')} ({job.get('source')}) -> {exclusion['rule']}: {exclusion['reason']}")

        if not execute:
            print("\nDRY RUN - no files written. Re-run with --execute to apply.")
            return 0

        if not to_archive:
            print("\nNothing to archive - no files written.")
            return 0

        jobs_backup, archive_backup = backup_both("pharma-regulatory-sweep")
        print(f"\nBacked up jobs.json -> {jobs_backup}")
        print(f"Backed up jobs-archive.json -> {archive_backup}")

        archived = read_json(ARCHIVE_PATH, default=[])
        archived.extend(to_archive)
        write_json(ARCHIVE_PATH, archived)
        write_json(JOBS_PATH, remaining)
        exclusion_filter.log_exclusions(to_log)

        pending_after = [j for j in remaining if j.get("review_status") == "pending"]
        print(f"\nWrote {len(remaining)} jobs.json records (was {len(jobs)}).")
        print(f"Wrote {len(archived)} jobs-archive.json records (was {len(archived) - len(to_archive)}).")
        print(f"Pending-review queue after sweep: {len(pending_after)} (was {len(pending_before)}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
