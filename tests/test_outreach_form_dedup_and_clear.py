"""Real gap found 2026-08-10 (Zahir's adversarial self-audit request, PRD
S16b #21): the "Log new outreach" form (render_outreach_section() in
src/ui/app.py) had no dedup check and didn't clear itself after a
successful submit - the same filled-in values just sat there in the form,
so an accidental double-click, or a confused re-click on a form that
still visibly showed the same values, created a duplicate outreach
record. Zero real outreach records exist yet, but a real risk the moment
Zahir starts logging real outreach next week.

Two fixes, both exercised here via AppTest driving the real form:
1. Every field key carries a "generation" suffix that bumps on successful
   submit (Streamlit forbids writing to a widget's own session_state key
   after it's already been instantiated this run, so fields can't be
   cleared in place - a fresh key forces a fresh, empty widget next
   render, the pattern Streamlit's own docs recommend).
2. A narrow duplicate guard (_is_likely_duplicate_outreach_submit in
   app.py) skips creating a second record only when the same contact/
   channel/notes was logged within the last 10 seconds - deliberately not
   a hard dedup rule, so a genuine follow-up outreach to the same contact
   next week is never silently blocked.
"""

import pytest
from streamlit.testing.v1 import AppTest

from prospector.outreach import load_outreach
from prospector.target_accounts import add_signal

APP_PATH = "src/ui/app.py"


@pytest.fixture
def prospector_app_with_target_account(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    add_signal(
        "Acme Corp", "commercial_hiring", "LinkedIn", "Hiring a VP of Engineering.",
        date_observed="2026-08-01T00:00:00+00:00",
    )
    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "prospector"
    at.session_state["ta_selected_idx"] = 0
    at.session_state["ta_Acme Corp_log_outreach_expander"] = True
    at.run(timeout=30)
    return at


def _log_outreach(at, name, gen):
    """Fills and submits the form at the given generation (field keys
    carry a "_{gen}" suffix - gen 0 the first time, 1 after the first
    successful clear, etc.)."""
    at.text_input(key=f"ta_Acme Corp_new_contact_name_{gen}").set_value(name)
    at.run(timeout=30)
    at.button(key="ta_Acme Corp_new_save").click()
    at.run(timeout=30)
    return at


def test_submitting_creates_one_record_and_clears_the_form(prospector_app_with_target_account):
    at = _log_outreach(prospector_app_with_target_account, "Jane Doe", gen=0)
    assert not at.exception

    records = load_outreach()
    assert len(records) == 1
    assert records[0]["contact_name"] == "Jane Doe"

    # Field cleared: the OLD key ("_0") is gone, a fresh empty one ("_1")
    # exists in its place - proof the generation bump actually took
    # effect, not just that *some* text_input with this label exists.
    keys = {t.key for t in at.text_input}
    assert "ta_Acme Corp_new_contact_name_0" not in keys
    new_box = next(t for t in at.text_input if t.key == "ta_Acme Corp_new_contact_name_1")
    assert new_box.value == ""


def test_immediate_resubmit_of_the_same_contact_does_not_duplicate(prospector_app_with_target_account):
    """The exact accidental-double-click scenario this fix targets: same
    contact/channel/notes, submitted again within seconds."""
    at = _log_outreach(prospector_app_with_target_account, "Jane Doe", gen=0)
    at = _log_outreach(at, "Jane Doe", gen=1)  # form is on gen 1 after the first clear
    assert not at.exception

    records = load_outreach()
    assert len(records) == 1  # still just one - the resubmit was recognized as a duplicate


def test_a_genuinely_different_contact_is_not_blocked(prospector_app_with_target_account):
    """The dedup guard must not overreach - a different contact right
    after the first is a real, distinct outreach attempt."""
    at = _log_outreach(prospector_app_with_target_account, "Jane Doe", gen=0)
    at = _log_outreach(at, "John Smith", gen=1)
    assert not at.exception

    records = load_outreach()
    assert len(records) == 2
    assert {r["contact_name"] for r in records} == {"Jane Doe", "John Smith"}


def test_empty_contact_name_shows_warning_and_creates_nothing(prospector_app_with_target_account):
    at = prospector_app_with_target_account
    at.button(key="ta_Acme Corp_new_save").click()
    at.run(timeout=30)

    assert not at.exception
    assert load_outreach() == []
    assert any("Contact name is required" in w.value for w in at.warning)
