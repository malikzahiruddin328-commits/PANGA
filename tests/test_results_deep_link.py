"""Deep-link from the browser extension's post-capture notification
(2026-08-08, coordinated with the "Chrome extension for LinkedIn/Dice job
capture" session): after "Send to Panga" succeeds, a real Chrome
notification's action opens Panga to the job that was just sent instead of
making Zahir hunt for it in the Results table. Contract: ?job_url=<url-
encoded posting URL>, matched against jobs' own posting_url via the SAME
normalization extension_bridge._normalize_url() already uses for capture-
matching (strip query/fragment/trailing slash, lowercase) - not a fresh
regex, so this reuses one real definition of "same posting" rather than
inventing a second one.

Lands on the Results tab with that job's channel expander force-opened and
the row selected/scrolled-to, reusing the exact same selected_idx_{channel}/
scroll_pending_{channel}/channel_expander_{channel} session_state keys the
existing Role-click ButtonColumn handler already sets - not a new
selection mechanism. One-shot and self-cleaning: the query param is always
cleared, and any deep_link_target that never found a match (ambiguous
posting_url match, or a real job that's currently hidden by the Results-
tab score/closed/applied filters) is popped rather than left to linger and
surprise a later, unrelated rerun."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score

APP_PATH = "src/ui/app.py"


@pytest.fixture
def jobs_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([
        {
            "source": "Dice", "job_id": "d1", "title": "Platform Engineer", "organization": "Acme Corp",
            "location": "Remote", "posting_url": "https://www.dice.com/job-detail/abc123",
        },
        {
            "source": "LinkedIn", "job_id": "l1", "title": "Staff Engineer", "organization": "Beta Inc",
            "location": "Remote", "posting_url": "https://www.linkedin.com/jobs/view/999888777",
        },
    ])
    update_job_score("Dice", "d1", 85, "Strong match.")
    update_job_score("LinkedIn", "l1", 80, "Good match.")
    return AppTest.from_file(APP_PATH)


def test_matching_job_url_lands_on_results_with_the_row_selected(jobs_app):
    at = jobs_app
    at.query_params["job_url"] = "https://www.dice.com/job-detail/abc123"
    at.run(timeout=30)

    assert not at.exception
    assert at.session_state["active_tab"] == "results"
    assert st_get(at, "channel_expander_Dice") is True
    assert st_get(at, "selected_idx_Dice") == 0
    dice_expander = next(e for e in at.expander if e.label.startswith("Dice"))
    assert dice_expander.proto.expanded


def test_job_url_normalization_matches_a_tracking_param_variant(jobs_app):
    # Same posting, different query string/trailing slash/case - same
    # normalization extension_bridge._normalize_url() already applies.
    at = jobs_app
    at.query_params["job_url"] = "HTTPS://WWW.DICE.COM/job-detail/abc123/?utm_source=email&ref=xyz"
    at.run(timeout=30)

    assert not at.exception
    assert st_get(at, "selected_idx_Dice") == 0


def test_query_param_is_cleared_after_being_consumed(jobs_app):
    at = jobs_app
    at.query_params["job_url"] = "https://www.dice.com/job-detail/abc123"
    at.run(timeout=30)

    assert not at.exception
    assert "job_url" not in dict(at.query_params)


def test_no_matching_job_lands_on_default_tab_without_error(jobs_app):
    at = jobs_app
    at.query_params["job_url"] = "https://www.dice.com/job-detail/does-not-exist"
    at.run(timeout=30)

    assert not at.exception
    assert at.session_state["active_tab"] == "cta"  # unchanged from the default
    assert "deep_link_target" not in at.session_state
    assert "job_url" not in dict(at.query_params)


def test_ambiguous_match_no_ops_rather_than_guessing(jobs_app):
    # Two jobs sharing the same normalized posting_url (a genuine re-scrape/
    # duplicate) - must not silently pick one and force it open.
    save_jobs([
        {
            "source": "Dice", "job_id": "d1", "title": "Platform Engineer", "organization": "Acme Corp",
            "location": "Remote", "posting_url": "https://www.dice.com/job-detail/abc123",
        },
        {
            "source": "Dice", "job_id": "d2", "title": "Platform Engineer (re-posted)", "organization": "Acme Corp",
            "location": "Remote", "posting_url": "https://www.dice.com/job-detail/abc123/",
        },
        {
            "source": "LinkedIn", "job_id": "l1", "title": "Staff Engineer", "organization": "Beta Inc",
            "location": "Remote", "posting_url": "https://www.linkedin.com/jobs/view/999888777",
        },
    ])
    update_job_score("Dice", "d1", 85, "Strong match.")
    update_job_score("Dice", "d2", 85, "Strong match.")
    update_job_score("LinkedIn", "l1", 80, "Good match.")

    at = jobs_app
    at.query_params["job_url"] = "https://www.dice.com/job-detail/abc123"
    at.run(timeout=30)

    assert not at.exception
    assert at.session_state["active_tab"] == "cta"
    assert "deep_link_target" not in at.session_state
    assert "selected_idx_Dice" not in at.session_state


def test_a_real_match_hidden_by_the_score_filter_does_not_linger(jobs_app):
    # Dice's job (score 85) is above the default 70 threshold and matches;
    # this covers the OTHER real case - a job that exists and uniquely
    # matches, but wouldn't currently be shown at all (below-threshold
    # score) - the target must not be left dangling in session_state to
    # surprise a later, unrelated rerun once the filter changes.
    save_jobs([
        {
            "source": "Dice", "job_id": "d1", "title": "Platform Engineer", "organization": "Acme Corp",
            "location": "Remote", "posting_url": "https://www.dice.com/job-detail/abc123",
        },
    ])
    update_job_score("Dice", "d1", 40, "Below threshold.")

    at = AppTest.from_file(APP_PATH)
    at.query_params["job_url"] = "https://www.dice.com/job-detail/abc123"
    at.run(timeout=30)

    assert not at.exception
    assert at.session_state["active_tab"] == "results"  # still routed there - just nothing forced open
    assert "deep_link_target" not in at.session_state
    assert "selected_idx_Dice" not in at.session_state


def st_get(at, key):
    try:
        return at.session_state[key]
    except Exception:
        return None
