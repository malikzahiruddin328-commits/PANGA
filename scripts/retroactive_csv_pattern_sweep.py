"""One-off retroactive sweep (2026-08-17, feature/exclusion-filter-csv-
patterns): applies this branch's four new exclusion layers/extensions
(organization-level exclusion / GForce Life Sciences, big-bank VP/SVP/
Director title inflation, marketing/economic-development commercial
extension, radiology/nutritional-services/nursing-services clinical
extension) against jobs already sitting in the PENDING review queue -
these titles reached the queue before the new patterns existed, so they
were never checked against them at save time.

Same shape as scripts/retroactive_pharma_regulatory_sweep.py (archive-not-
delete, backs up both jobs.json and jobs-archive.json first, checks
applications.json for prior application/basket/resume engagement before
archiving and prints a prominent WARNING if found, dry_run=True by
default).

Usage (from repo root, project venv):
    PANGA_DATA_ROOT="<main checkout>" "<venv>/Scripts/python.exe" scripts/retroactive_csv_pattern_sweep.py           # dry run
    PANGA_DATA_ROOT="<main checkout>" "<venv>/Scripts/python.exe" scripts/retroactive_csv_pattern_sweep.py --execute  # writes
"""

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from search import exclusion_filter  # noqa: E402
from search.job_store import PASSED_APPLICATION_STATUSES, PROTECTED_APPLICATION_STATUSES  # noqa: E402
from security.crypto_store import read_json, write_json  # noqa: E402
from security.file_lock import locked  # noqa: E402
from tailoring.applications import load_applications  # noqa: E402

# DATA_ROOT overridable via PANGA_DATA_ROOT env var - see the same caution in
# retroactive_pharma_regulatory_sweep.py: this worktree's own data/ dir is
# gitignored/empty, the real jobs.json/jobs-archive.json live only in the
# main checkout.
DATA_ROOT = Path(os.environ.get("PANGA_DATA_ROOT", str(Path(__file__).resolve().parents[1])))
JOBS_PATH = DATA_ROOT / "data" / "jobs" / "jobs.json"
ARCHIVE_PATH = DATA_ROOT / "data" / "jobs" / "jobs-archive.json"

# Same caution as retroactive_pharma_regulatory_sweep.py: exclusion_filter's
# own EXCLUSION_LOG_PATH is computed from that module's __file__ location,
# which resolves to THIS worktree's own (real) data/ dir, not DATA_ROOT -
# patched here so the audit log this sweep writes lands in the same real
# DATA_ROOT the rest of this script uses.
exclusion_filter.EXCLUSION_LOG_PATH = DATA_ROOT / "data" / "jobs" / "search_exclusion_log.json"
# Deliberately NOT overriding exclusion_filter.SETTINGS_PATH here (unlike
# EXCLUSION_LOG_PATH above): this branch's new "custom_organization_
# exclusions" seed (GForce Life Sciences) lives in THIS WORKTREE's own
# config/settings.yaml, not yet in DATA_ROOT's (pre-merge) - the sweep
# should apply the settings this branch is about to ship, so
# load_custom_organization_exclusions() is left to resolve against the
# worktree's own settings.yaml (its default, module-computed path).

NEW_RULES = (
    "custom_organization_exclusion",
    "big_bank_title_inflation",
    "non_it_commercial_role",
    "clinical_domain",
)

# Explicit protection (found live during this sweep's own dry run,
# 2026-08-17): Zahir's hand-marked CSV shows "Vice President of Sales" got
# BOTH verdicts on the identical bare title - Excluded at AFB Floors, KEPT
# at Attala Steel Industries (source SimplyHired, job_id b9d7ec457a0d04c5,
# confirmed against the real live store). This is a genuine inconsistency
# in his own marks (documented in exclusion_filter.py's own header comment,
# layer 14) that the pre-existing bare "\bsales\b" pattern (not new to this
# branch) cannot distinguish on title alone - it would otherwise silently
# archive a job he explicitly marked Keep. Skipped here rather than guessed
# at; flagged prominently in the sweep's own output below.
PROTECTED_KEYS = {
    ("SimplyHired", "b9d7ec457a0d04c5"),  # "Vice President of Sales" @ Attala Steel Industries - real Zahir Keep mark
}


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
        custom_org_exclusions = exclusion_filter.load_custom_organization_exclusions()
        print(f"custom_organization_exclusions loaded: {custom_org_exclusions}")

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

            key = (job.get("source"), job.get("job_id"))
            if key in PROTECTED_KEYS:
                print(
                    f"PROTECTED (skipped, not archived): {job.get('title')!r} @ "
                    f"{job.get('organization')} - real Zahir Keep mark on this exact "
                    f"job conflicts with the pre-existing sales pattern; see PROTECTED_KEYS comment above."
                )
                remaining.append(job)
                continue

            exclusion = exclusion_filter.check_exclusion(job, custom_exclusions, custom_org_exclusions)
            # Only rules this branch newly adds/extends count as "newly
            # matched" for this sweep - non_it_commercial_role and
            # clinical_domain are pre-existing rule NAMES this branch only
            # extends the underlying pattern of, so a pending job already
            # excluded under the OLD pattern shape would have been caught
            # by an earlier sweep already; this sweep still re-checks every
            # pending job against the current (extended) pattern, which is
            # exactly the coverage gap this sweep exists to close.
            if exclusion is None or exclusion["rule"] not in NEW_RULES:
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

        jobs_backup, archive_backup = backup_both("csv-pattern-sweep")
        print(f"\nBacked up jobs.json -> {jobs_backup}")
        print(f"Backed up jobs-archive.json -> {archive_backup}")

        archived = read_json(ARCHIVE_PATH, default=[])
        archived_before = len(archived)
        archived.extend(to_archive)
        write_json(ARCHIVE_PATH, archived)
        write_json(JOBS_PATH, remaining)
        exclusion_filter.log_exclusions(to_log)

        pending_after = [j for j in remaining if j.get("review_status") == "pending"]
        print(f"\nWrote {len(remaining)} jobs.json records (was {len(jobs)}).")
        print(f"Wrote {len(archived)} jobs-archive.json records (was {archived_before}).")
        print(f"Pending-review queue after sweep: {len(pending_after)} (was {len(pending_before)}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
