import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backfill_jd_text  # noqa: E402
from search import company_sites, job_store  # noqa: E402


def test_backfill_updates_workday_job_missing_description(isolated_data, monkeypatch):
    job_store.save_jobs([{"source": "Eisai", "job_id": "/job/x", "title": "Director"}])
    monkeypatch.setattr(company_sites, "_fetch_workday_job_description", lambda *a, **k: "Real Eisai JD text.")

    count = backfill_jd_text.backfill()

    assert count == 1
    job = job_store.load_jobs()[0]
    assert job["description"] == "Real Eisai JD text."


def test_backfill_updates_smartrecruiters_job_missing_description(isolated_data, monkeypatch):
    job_store.save_jobs([{"source": "AbbVie", "job_id": "12345", "title": "Director"}])
    monkeypatch.setattr(company_sites, "_fetch_smartrecruiters_job_description", lambda *a, **k: "Real AbbVie JD text.")

    count = backfill_jd_text.backfill()

    assert count == 1
    job = job_store.load_jobs()[0]
    assert job["description"] == "Real AbbVie JD text."


def test_backfill_skips_jobs_that_already_have_a_description(isolated_data, monkeypatch):
    job_store.save_jobs([{"source": "Eisai", "job_id": "/job/x", "title": "Director", "description": "Already here."}])
    monkeypatch.setattr(company_sites, "_fetch_workday_job_description", lambda *a, **k: "Should never be called.")

    count = backfill_jd_text.backfill()

    assert count == 0
    job = job_store.load_jobs()[0]
    assert job["description"] == "Already here."


def test_backfill_skips_sources_it_does_not_cover(isolated_data, monkeypatch):
    # Dice/ZipRecruiter/Indeed/industry boards - not this script's job.
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    count = backfill_jd_text.backfill()
    assert count == 0
    assert "description" not in job_store.load_jobs()[0]


def test_backfill_does_not_fail_when_fetch_returns_none(isolated_data, monkeypatch):
    job_store.save_jobs([{"source": "Eisai", "job_id": "/job/x", "title": "Director"}])
    monkeypatch.setattr(company_sites, "_fetch_workday_job_description", lambda *a, **k: None)

    count = backfill_jd_text.backfill()

    assert count == 0
    assert "description" not in job_store.load_jobs()[0]
