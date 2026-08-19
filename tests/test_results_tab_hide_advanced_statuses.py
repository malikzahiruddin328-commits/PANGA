"""Zahir's explicit ask (2026-08-19): the Results tab is for jobs still
worth a decision - "only nulls or under review should be visible" by
default. He specifically flagged a "rejected" job sitting unfiltered
alongside real open opportunities. APPLIED/NOT_INTERESTED/CLOSED already
had their own hide-by-default toggle; ADVANCED_STATUSES (rejected,
interview scheduled, offer) had none until now."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app_with_mixed_statuses(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([
        {"source": "Dice", "job_id": "rejected1", "title": "Director", "organization": "Acme", "location": "Remote"},
        {"source": "Dice", "job_id": "interviewing1", "title": "VP", "organization": "Acme", "location": "Remote"},
        {"source": "Dice", "job_id": "offer1", "title": "CIO", "organization": "Acme", "location": "Remote"},
        {"source": "Dice", "job_id": "underreview1", "title": "CTO", "organization": "Acme", "location": "Remote"},
        {"source": "Dice", "job_id": "untouched1", "title": "Head of IT", "organization": "Acme", "location": "Remote"},
    ])
    for job_id in ("rejected1", "interviewing1", "offer1", "underreview1", "untouched1"):
        update_job_score("Dice", job_id, 85, "Strong match.")
    upsert_application("Dice", "rejected1", status="rejected")
    upsert_application("Dice", "interviewing1", status="interview scheduled")
    upsert_application("Dice", "offer1", status="offer")
    upsert_application("Dice", "underreview1", status="under review")
    return AppTest.from_file(APP_PATH)


def test_advanced_statuses_hidden_by_default(results_app_with_mixed_statuses):
    at = results_app_with_mixed_statuses
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    checkbox = next(c for c in at.checkbox if c.key == "results_show_advanced")
    assert "Show 3 job(s) marked 'rejected', 'interview scheduled', or 'offer'" in checkbox.label


def test_checking_the_box_reveals_advanced_statuses(results_app_with_mixed_statuses):
    at = results_app_with_mixed_statuses
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    checkbox = next(c for c in at.checkbox if c.key == "results_show_advanced")
    checkbox.set_value(True)
    at.run(timeout=30)

    assert not at.exception
    # Still present, nothing deleted - the count reflects what WOULD be
    # hidden, not what's currently shown, same convention as the other
    # three toggles (applied/not-interested/closed).
    checkbox = next(c for c in at.checkbox if c.key == "results_show_advanced")
    assert "Show 3 job(s)" in checkbox.label


def test_under_review_and_null_status_never_hidden_by_this_toggle(results_app_with_mixed_statuses):
    from ui.app import ADVANCED_STATUSES, APPLIED_STATUSES, CLOSED_STATUSES, NOT_INTERESTED_STATUSES

    assert "under review" not in ADVANCED_STATUSES
    assert set(ADVANCED_STATUSES).isdisjoint(APPLIED_STATUSES)
    assert set(ADVANCED_STATUSES).isdisjoint(CLOSED_STATUSES)
    assert set(ADVANCED_STATUSES).isdisjoint(NOT_INTERESTED_STATUSES)
