"""One-off validation script (not part of the app) for
search.exclusion_filter - runs the new title-based exclusion logic against
every real job currently in data/jobs/jobs.json and reports how many each
rule would exclude, plus checks the specific known-good/known-bad examples
Zahir's request named explicitly. Read-only: never writes to jobs.json or
any other store.

Usage: run from the repo root with the project venv:
    "<venv>/Scripts/python.exe" scripts/validate_exclusion_filter.py [path-to-jobs.json]
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from search import exclusion_filter  # noqa: E402
from security.crypto_store import read_json  # noqa: E402

KNOWN_BAD = [
    ("Senior Medical Director, Hematology Clinical Development", "AbbVie"),
    ("Sr. Systems Engineer", "AbbVie"),
]
KNOWN_GOOD = [
    ("Director, IT Service Continuity", "AbbVie"),
]


def main():
    jobs_path = Path(sys.argv[1]) if len(sys.argv) > 1 else (Path(__file__).resolve().parents[1] / "data" / "jobs" / "jobs.json")
    jobs = read_json(jobs_path, default=[])
    print(f"Loaded {len(jobs)} real job records from {jobs_path}")

    counts = {"seniority_mismatch": 0, "clinical_domain": 0, "custom_user_exclusion": 0}
    total_excluded = 0
    examples = {"seniority_mismatch": [], "clinical_domain": [], "custom_user_exclusion": []}
    # Loaded once here (2026-08-13, custom title exclusions build) rather
    # than once per job - same "load once per batch" pattern
    # job_store.save_jobs() uses, applied here for the same reason (this
    # script runs against the full jobs.json, thousands of records).
    custom_exclusions = exclusion_filter.load_custom_title_exclusions()
    if custom_exclusions:
        print(f"Custom title exclusions configured: {custom_exclusions}")
    else:
        print("No custom title exclusions configured (config/settings.yaml has no custom_title_exclusions key/empty list).")

    for job in jobs:
        exclusion = exclusion_filter.check_exclusion(job, custom_exclusions)
        if exclusion:
            total_excluded += 1
            rule = exclusion["rule"]
            counts[rule] = counts.get(rule, 0) + 1
            if rule not in examples:
                examples[rule] = []
            if len(examples[rule]) < 5:
                examples[rule].append(f"{job.get('title')} @ {job.get('organization')} -> {exclusion['reason']}")

    print()
    print(f"Total jobs: {len(jobs)}")
    print(f"Total excluded: {total_excluded} ({total_excluded / len(jobs) * 100:.1f}%)")
    for rule, count in counts.items():
        print(f"  {rule}: {count} ({count / len(jobs) * 100:.1f}%)")
        for ex in examples[rule]:
            print(f"    - {ex}")

    print()
    print("Known-bad examples (must be EXCLUDED):")
    ok = True
    for title, org in KNOWN_BAD:
        matches = [j for j in jobs if j.get("title") == title and j.get("organization") == org]
        if not matches:
            print(f"  [NOT FOUND IN STORE] {title} @ {org}")
            continue
        for job in matches:
            result = exclusion_filter.check_exclusion(job)
            status = "EXCLUDED" if result else "KEPT (WRONG)"
            if not result:
                ok = False
            print(f"  [{status}] {title} @ {org}" + (f" -> {result}" if result else ""))

    print()
    print("Known-good examples (must be KEPT):")
    for title, org in KNOWN_GOOD:
        matches = [j for j in jobs if j.get("title") == title and j.get("organization") == org]
        if not matches:
            print(f"  [NOT FOUND IN STORE] {title} @ {org}")
            continue
        for job in matches:
            result = exclusion_filter.check_exclusion(job)
            status = "KEPT" if not result else "EXCLUDED (WRONG)"
            if result:
                ok = False
            print(f"  [{status}] {title} @ {org}" + (f" -> {result}" if result else ""))

    # Also: any Director+/VP+/Chief/President/Head-titled job kept sanity check
    print()
    exec_kept = 0
    exec_excluded = 0
    for job in jobs:
        title = job.get("title") or ""
        if exclusion_filter._EXEC_QUALIFIER_PATTERN.search(title):
            if exclusion_filter.check_exclusion(job):
                exec_excluded += 1
            else:
                exec_kept += 1
    print(f"Director+/VP+/Chief/President/Head-titled jobs: {exec_kept} kept, {exec_excluded} excluded (excluded ones are clinical-domain execs, e.g. Medical Director)")

    print()
    print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
