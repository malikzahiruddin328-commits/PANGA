"""Covers the 2026-08-07 "Add a job manually" discoverability fix (Zahir's
request, relayed via hub after Mirror confirmed live on production that he
couldn't find it): it was a plain collapsed st.expander sitting below the
"Run now (USAJOBS)" row, easy to miss as inert prose. Approved direction
(Zahir picked from 3 mockups): a dedicated, equal-weight button right next
to "Run now (USAJOBS)" that force-opens the form.

Doesn't test the manual-add form's own fields/save logic - that's untouched
by this change and already covered by test_job_store.py's
test_add_manual_job_* tests. This file covers only the new entry point:
does the button exist, and does clicking it reveal the form."""

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)
    return at


def test_add_a_job_manually_button_exists_next_to_run_now(results_app):
    at = results_app
    assert not at.exception

    labels = [b.label for b in at.button]
    assert "Run now (USAJOBS)" in labels
    assert "Add a job manually" in labels


def test_expander_starts_collapsed_by_default(results_app):
    at = results_app
    assert not at.exception

    target = [e for e in at.expander if "Add a job manually" in e.label]
    assert len(target) == 1
    assert not target[0].proto.expanded


def test_clicking_the_button_force_opens_the_expander(results_app):
    at = results_app
    reveal_button = next(b for b in at.button if b.label == "Add a job manually")
    reveal_button.click().run(timeout=30)

    assert not at.exception
    target = [e for e in at.expander if "Add a job manually" in e.label]
    assert len(target) == 1
    assert target[0].proto.expanded


def test_the_manual_add_form_fields_are_still_present_once_open(results_app):
    at = results_app
    reveal_button = next(b for b in at.button if b.label == "Add a job manually")
    reveal_button.click().run(timeout=30)

    assert not at.exception
    field_labels = {ti.label for ti in at.text_input}
    assert {"Job title", "Organization", "Location", "Posting URL"} <= field_labels
    save_labels = [b.label for b in at.button]
    assert "Save job" in save_labels
