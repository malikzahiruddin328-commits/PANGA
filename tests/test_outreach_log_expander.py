"""Real bug (Mirror's proactive sweep, 2026-08-08): render_outreach_section()'s
"Log new outreach" expander had no key= at all - not "key without
on_change", genuinely no persistence mechanism. Typing into any field or
changing the channel dropdown reruns the script (any widget losing focus
does) and the box visually snapped shut mid-entry, even though the typed
field values themselves survived (separately keyed) - same class of "did I
just lose that" confusion as the Results-tab expander bug already fixed.

Fixed with the same key= + on_change="rerun" + explicit
expanded=st.session_state.get(key, False) pattern proven for the other
three expander instances in this same sweep. AppTest limitation found
while writing this test (reproduced identically against the unrelated
"previously_answered_expander" key too, so it's a framework quirk, not
specific to this fix): a programmatically-injected st.session_state[key]
for a keyed st.expander does not reliably survive a SUBSEQUENT .run() on
this Streamlit/AppTest version, even with on_change="rerun" set and even
when that .run() is the rerun triggered by a real widget interaction
elsewhere on the page. There is also no .set_value() on AppTest's
Expander element - no native way to simulate a real header click/toggle.
The underlying mechanism (key= + on_change="rerun") was independently,
definitively verified against a REAL browser click for the sibling
channel-expander fix earlier this session, and is re-verified live for
this exact outreach panel as part of this task (see the UI refinement
session's live-verification notes). Same documented-limitation treatment
as the canvas st.dataframe ButtonColumns elsewhere in this test suite:
AppTest checks what it reliably can, live browser is the definitive
check for the rest."""

import pytest
from streamlit.testing.v1 import AppTest

from prospector.target_accounts import add_signal

APP_PATH = "src/ui/app.py"


@pytest.fixture
def prospector_app_with_target_account(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    add_signal(
        "Acme Corp", "commercial_hiring", "LinkedIn", "Hiring a VP of Engineering.",
        date_observed="2026-08-01T00:00:00+00:00",
    )
    return AppTest.from_file(APP_PATH)


def test_log_outreach_expander_starts_collapsed_and_opens_on_state_write(prospector_app_with_target_account):
    at = prospector_app_with_target_account
    at.session_state["active_tab"] = "prospector"
    # Same as driving a real row click on the target-accounts table (a
    # ButtonColumn rendered to canvas - can't be click-simulated here,
    # same pre-existing limitation as the Results-tab Role/Pass columns).
    at.session_state["ta_selected_idx"] = 0
    at.run(timeout=30)

    expander = next(e for e in at.expander if e.label == "Log new outreach")
    assert not expander.proto.expanded  # starts collapsed

    at.session_state["ta_Acme Corp_log_outreach_expander"] = True  # opened by hand
    at.run(timeout=30)

    expander = next(e for e in at.expander if e.label == "Log new outreach")
    assert expander.proto.expanded
    assert not at.exception


def test_log_outreach_form_fields_survive_their_own_rerun(prospector_app_with_target_account):
    # What AppTest CAN reliably confirm about "don't lose in-progress
    # entry": the field values themselves survive the rerun a widget
    # change triggers (they're independently keyed, so this part was
    # never actually broken). Whether the expander's OWN visual state
    # also survives that same rerun is confirmed by live browser
    # verification instead - see this file's module docstring.
    #
    # Key carries a "_0" generation suffix (2026-08-10, PRD S16b #21) -
    # see test_outreach_form_dedup_and_clear.py's module docstring for why.
    at = prospector_app_with_target_account
    at.session_state["active_tab"] = "prospector"
    at.session_state["ta_selected_idx"] = 0
    at.session_state["ta_Acme Corp_log_outreach_expander"] = True
    at.run(timeout=30)

    name_box = next(t for t in at.text_input if t.key == "ta_Acme Corp_new_contact_name_0")
    name_box.set_value("Jane Doe")
    at.run(timeout=30)  # the rerun the text_input's own change triggers

    assert not at.exception
    name_box = next(t for t in at.text_input if t.key == "ta_Acme Corp_new_contact_name_0")
    assert name_box.value == "Jane Doe"
