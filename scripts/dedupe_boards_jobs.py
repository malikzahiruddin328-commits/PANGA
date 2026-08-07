"""One-off cleanup (2026-08-06): merges duplicate Indeed/ZipRecruiter/Dice
job records that accumulated before search/boards.py's job_id fix (see
_stable_job_id() there) - those three sources used an unstable identifier
as job_id (Indeed/ZipRecruiter: a redirect URL/signed token reissued on
every search; Dice's MCP-path "guid": looked like a stable database id but
rotated too), so the exact same real posting silently re-entered
job_store.py as "new" on every search run. Confirmed live against
production data 2026-08-06: 38 duplicate groups, 54 excess records (47
Indeed, 4 ZipRecruiter, 3 Dice) - grouped by (source, normalized title +
organization + location), not just title+organization, which overcounts
(USAJOBS/AbbVie's apparent "duplicates" checked separately turned out to
be mostly genuinely distinct postings at the same agency, not this bug).

MERGE POLICY per duplicate group:
1. Prefer the copy that already has an application record (tailoring/
   applications.py) - status/edit history must never be the copy that
   gets silently deleted.
2. Otherwise prefer the copy with more populated fields (fit_score,
   description already extracted) - the more "worked" copy.
3. Otherwise the earliest date_added.
Live-verified in production data: of the 38 groups, 5 had exactly one copy
with an application, 0 had more than one - so there is no real multi-
application merge-conflict case here, only pick-the-survivor-and-delete-
the-rest.

The survivor's job_id is rewritten to the new _stable_job_id() value (so
future scrapes recognize it as the same job and don't re-add it). If the
survivor had an application under its OLD job_id, that application record
is migrated to the new job_id - AND, live-checked against the real store,
3 of those 5 already have a real dossier folder under data/applications/
dossiers/ with genuinely hand-edited resume/cover-letter .docx files
(multiple timestamped .edited-*.docx versions - real work, not
regeneratable from scratch). dossier_dir()'s path is derived from
hash(source, job_id), so a job_id rename orphans that folder unless it's
moved too - this script moves it (shutil.move, not copy+delete-original,
so it's a single atomic rename on the same volume) to the new job_id's
path as part of the same operation, never leaving the two out of sync.

SAFETY: backs up jobs.json and applications.json (timestamped copies,
alongside the originals) before writing anything - this codebase has a
documented near-data-loss incident from a careless write to these real
stores (see tests/conftest.py's isolated_data fixture docstring), so this
script does not repeat that risk. Defaults to --dry-run behavior (prints
exactly what it would do, writes nothing, moves nothing) - pass --apply to
actually perform the merge. Safe to re-run after --apply: a second run
finds no more duplicate groups once the first run's already merged them
(_stable_job_id() is deterministic, so re-grouping the fixed data finds
nothing new).

Run: venv\\Scripts\\python.exe scripts\\dedupe_boards_jobs.py           (dry run, default)
     venv\\Scripts\\python.exe scripts\\dedupe_boards_jobs.py --apply   (actually merge)
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from search import job_store  # noqa: E402
from search.boards import _normalize_for_hash, _stable_job_id  # noqa: E402
from security.crypto_store import write_json  # noqa: E402
from security.file_lock import locked  # noqa: E402
from tailoring import applications, dossier  # noqa: E402

_AFFECTED_SOURCES = {"Indeed", "ZipRecruiter", "Dice"}


def _log(message: str) -> None:
    print(message, flush=True)


def _group_key(job: dict) -> tuple:
    return (
        job.get("source"),
        _normalize_for_hash(job.get("title")),
        _normalize_for_hash(job.get("organization")),
        _normalize_for_hash(job.get("location")),
    )


def find_duplicate_groups(jobs: list[dict]) -> list[list[dict]]:
    groups: dict[tuple, list[dict]] = {}
    for job in jobs:
        if job.get("source") not in _AFFECTED_SOURCES:
            continue
        groups.setdefault(_group_key(job), []).append(job)
    return [g for g in groups.values() if len(g) > 1]


def _pick_survivor(group: list[dict], app_keys: set) -> dict:
    with_app = [j for j in group if (j["source"], j.get("job_id")) in app_keys]
    if with_app:
        return with_app[0]
    scored = sorted(
        group,
        key=lambda j: (
            0 if j.get("fit_score") is not None else 1,
            0 if j.get("description") else 1,
            j.get("date_added") or "",
        ),
    )
    return scored[0]


def plan_merge(jobs: list[dict], apps: list[dict]) -> dict:
    """Pure planning step (no writes, no filesystem moves) - returns what
    dedupe() would do, so this can be unit-tested and dry-run-previewed
    without touching real data. Keys survivors/removed by object identity
    (id()) against the passed-in `jobs` list, not a copy."""
    app_keys = {(a["source"], a.get("job_id")) for a in apps}
    groups = find_duplicate_groups(jobs)

    renames = []  # (job_obj, old_job_id, new_job_id)
    removed = []  # job_obj
    app_migrations = []  # (source, old_job_id, new_job_id)

    for group in groups:
        survivor = _pick_survivor(group, app_keys)
        new_id = _stable_job_id(
            survivor["source"], survivor.get("title"), survivor.get("organization"), survivor.get("location"),
        )
        old_id = survivor.get("job_id")
        if old_id != new_id:
            renames.append((survivor, old_id, new_id))
            if (survivor["source"], old_id) in app_keys:
                app_migrations.append((survivor["source"], old_id, new_id))
        for job in group:
            if job is not survivor:
                removed.append(job)

    return {"groups": groups, "renames": renames, "removed": removed, "app_migrations": app_migrations}


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}")
    shutil.copy2(path, backup_path)
    return backup_path


def dedupe(apply: bool = False) -> dict:
    """Holds BOTH the "jobs" and "applications" file locks for the entire
    load-plan-write cycle, not just around the final write - added
    2026-08-06 after a proactive self-review caught that the first version
    only locked (in fact, didn't lock at all) the write step, leaving a
    real TOCTOU gap: the plan is computed from a snapshot taken at the
    start, so anything else that wrote jobs.json/applications.json between
    that read and this script's write-back would have been silently
    discarded when this script wrote its own (now-stale) full list back.
    Verified after the fact that the original --apply run against the real
    store didn't actually collide with anything (file mtimes and record
    counts checked immediately after matched exactly what this script
    itself reported, with no other process running at the time) - but the
    gap was real regardless of this one run's luck, so it's closed here,
    not left for next time."""
    with locked("jobs"), locked("applications"):
        jobs = job_store.load_jobs()
        apps = applications.load_applications()
        plan = plan_merge(jobs, apps)

        if not plan["groups"]:
            _log("No duplicate groups found - nothing to do.")
            return {"groups_merged": 0, "records_removed": 0, "applications_migrated": 0, "dossiers_moved": 0}

        _log(f"Found {len(plan['groups'])} duplicate group(s), {len(plan['removed'])} record(s) to remove.")
        for job, old_id, new_id in plan["renames"]:
            _log(f"  [rename] {job['source']} / {job.get('title')!r} at {job.get('organization')!r}: {old_id!r} -> {new_id!r}")
        for job in plan["removed"]:
            _log(f"  [remove] {job['source']} / {job.get('title')!r} at {job.get('organization')!r} ({job.get('job_id')})")

        if not apply:
            _log("\nDry run only - nothing written. Re-run with --apply to perform this merge.")
            return {
                "groups_merged": len(plan["groups"]), "records_removed": len(plan["removed"]),
                "applications_migrated": len(plan["app_migrations"]), "dossiers_moved": 0,
            }

        jobs_backup = _backup(job_store.JOBS_PATH)
        apps_backup = _backup(applications.APPLICATIONS_PATH)
        _log(f"\nBacked up jobs.json -> {jobs_backup}")
        _log(f"Backed up applications.json -> {apps_backup}")

        dossiers_moved = 0
        for job, old_id, new_id in plan["renames"]:
            old_dir = dossier.dossier_dir(job["source"], old_id, job.get("organization"), job.get("title"))
            new_dir = dossier.dossier_dir(job["source"], new_id, job.get("organization"), job.get("title"))
            if old_dir.exists() and old_dir != new_dir:
                shutil.move(str(old_dir), str(new_dir))
                dossiers_moved += 1
                _log(f"  [dossier moved] {old_dir.name} -> {new_dir.name}")
            job["job_id"] = new_id

        if plan["app_migrations"]:
            for app in apps:
                for source, old_id, new_id in plan["app_migrations"]:
                    if app["source"] == source and app.get("job_id") == old_id:
                        app["job_id"] = new_id
            applications._save_all(apps)

        removed_ids = {id(job) for job in plan["removed"]}
        remaining_jobs = [job for job in jobs if id(job) not in removed_ids]
        write_json(job_store.JOBS_PATH, remaining_jobs)

        _log(
            f"\nDone. Merged {len(plan['groups'])} group(s), removed {len(plan['removed'])} duplicate record(s), "
            f"migrated {len(plan['app_migrations'])} application(s), moved {dossiers_moved} dossier folder(s)."
        )
        return {
            "groups_merged": len(plan["groups"]), "records_removed": len(plan["removed"]),
            "applications_migrated": len(plan["app_migrations"]), "dossiers_moved": dossiers_moved,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry run only).")
    args = parser.parse_args()
    dedupe(apply=args.apply)
