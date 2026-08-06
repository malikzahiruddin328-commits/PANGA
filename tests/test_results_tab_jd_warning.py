"""Real bug found live 2026-08-06 (Zahir): jobs from sources with no
captured JD text (ZipRecruiter, Indeed, industry boards) were being scored
and drafted as if genuinely tailored against the posting, with no
indication anything was missing. These tests drive the actual Results tab
(via Streamlit's own AppTest, not a browser) to confirm the warning
renders exactly when there's no real posting text, and never when there is.
"""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    save_jobs([
        {"source": "Indeed", "job_id": "nojd1", "title": "Director, No JD", "organization": "Acme Corp", "location": "Remote"},
        {"source": "Dice", "job_id": "withjd1", "title": "Director, With JD", "organization": "Beta Inc", "location": "Remote", "description": "Requirements: Python, SQL."},
    ])
    update_job_score("Indeed", "nojd1", 85, "Strong match.")
    update_job_score("Dice", "withjd1", 85, "Strong match.")
    upsert_application(
        "Indeed", "nojd1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava",
        resume_ats_score=45, resume_ats_rationale="No JD text available.", resume_ats_next_actions=[],
    )
    upsert_application(
        "Dice", "withjd1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava, Python, SQL",
        resume_ats_score=90, resume_ats_rationale="Matched 2/2 keywords.", resume_ats_next_actions=[],
    )
    return AppTest.from_file(APP_PATH)


def test_no_jd_warning_shown_when_posting_has_no_captured_text(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert any("No job description available" in w for w in warnings)


def test_no_warning_shown_when_posting_has_real_jd_text(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert not any("No job description available" in w for w in warnings)
