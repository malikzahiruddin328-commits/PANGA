import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import dedupe_boards_jobs  # noqa: E402
from search import job_store  # noqa: E402
from search.boards import _stable_job_id  # noqa: E402
from tailoring import applications, dossier  # noqa: E402


def _dice_job(job_id, **overrides):
    job = {
        "source": "Dice", "job_id": job_id, "title": "CIO", "organization": "Acme Corp",
        "location": "Remote", "posting_url": f"https://www.dice.com/job-detail/{job_id}",
    }
    job.update(overrides)
    return job


def test_no_duplicates_is_a_no_op(isolated_data):
    job_store.save_jobs([_dice_job("guid-1"), {"source": "Dice", "job_id": "guid-2", "title": "VP Eng", "organization": "Acme Corp", "location": "Remote"}])
    result = dedupe_boards_jobs.dedupe(apply=True)
    assert result == {"groups_merged": 0, "records_removed": 0, "applications_migrated": 0, "dossiers_moved": 0}
    assert len(job_store.load_jobs()) == 2


def test_dry_run_reports_but_does_not_write(isolated_data):
    job_store.save_jobs([_dice_job("guid-1"), _dice_job("guid-2")])
    result = dedupe_boards_jobs.dedupe(apply=False)
    assert result["groups_merged"] == 1
    assert result["records_removed"] == 1
    # nothing actually written
    assert len(job_store.load_jobs()) == 2


def test_apply_merges_duplicate_group_and_rewrites_stable_id(isolated_data):
    job_store.save_jobs([_dice_job("guid-1"), _dice_job("guid-2")])
    result = dedupe_boards_jobs.dedupe(apply=True)

    assert result["groups_merged"] == 1
    assert result["records_removed"] == 1
    remaining = job_store.load_jobs()
    assert len(remaining) == 1
    expected_id = _stable_job_id("Dice", "CIO", "Acme Corp", "Remote")
    assert remaining[0]["job_id"] == expected_id


def test_survivor_preference_keeps_the_copy_with_an_application(isolated_data):
    job_store.save_jobs([_dice_job("guid-1"), _dice_job("guid-2")])
    applications.upsert_application("Dice", "guid-2", status="applied")

    dedupe_boards_jobs.dedupe(apply=True)

    remaining = job_store.load_jobs()
    assert len(remaining) == 1
    new_id = remaining[0]["job_id"]
    # the application followed the survivor to its new stable job_id
    app = applications.get_application("Dice", new_id)
    assert app is not None
    assert app["status"] == "applied"
    # the old job_id no longer has an orphaned application record
    assert applications.get_application("Dice", "guid-2") is None


def test_dossier_folder_moves_with_the_survivor(isolated_data):
    job_store.save_jobs([_dice_job("guid-1"), _dice_job("guid-2")])
    applications.upsert_application("Dice", "guid-2", status="applied")

    old_dir = dossier.dossier_dir("Dice", "guid-2", "Acme Corp", "CIO")
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "Zahir_Uddin_Resume_CIO_Acme.edited-2026-08-06T120000.docx").write_text("hand-edited content")

    dedupe_boards_jobs.dedupe(apply=True)

    new_id = job_store.load_jobs()[0]["job_id"]
    new_dir = dossier.dossier_dir("Dice", new_id, "Acme Corp", "CIO")
    assert not old_dir.exists()
    assert new_dir.exists()
    assert (new_dir / "Zahir_Uddin_Resume_CIO_Acme.edited-2026-08-06T120000.docx").read_text() == "hand-edited content"


def test_backs_up_jobs_and_applications_before_writing(isolated_data):
    job_store.save_jobs([_dice_job("guid-1"), _dice_job("guid-2")])
    applications.upsert_application("Dice", "guid-1", status="applied")

    dedupe_boards_jobs.dedupe(apply=True)

    backups = list(job_store.JOBS_PATH.parent.glob("jobs.bak-*.json"))
    assert len(backups) == 1
    app_backups = list(applications.APPLICATIONS_PATH.parent.glob("applications.bak-*.json"))
    assert len(app_backups) == 1


def test_ignores_sources_outside_the_affected_three(isolated_data):
    job_store.save_jobs([
        {"source": "Eisai", "job_id": "/job/1", "title": "CIO", "organization": "Eisai", "location": "Remote"},
        {"source": "Eisai", "job_id": "/job/2", "title": "CIO", "organization": "Eisai", "location": "Remote"},
    ])
    result = dedupe_boards_jobs.dedupe(apply=True)
    assert result["groups_merged"] == 0
    assert len(job_store.load_jobs()) == 2
