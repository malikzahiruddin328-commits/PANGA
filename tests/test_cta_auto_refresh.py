"""Two changes to the Call to Action header, both mockup-approved by Zahir
via hub, 2026-08-08 (Mirror's proactive sweep flagged both):

1. "Send and receive" now gives visible feedback the instant it's clicked -
   disabled, relabeled "Syncing...", spinner icon - not just the st.spinner
   overlay that was already there. This is a two-phase click: the click's
   own rerun only flips a session_state flag and re-renders the button in
   its disabled/"Syncing..." state; the real run_full_fulfillment() call
   and the flag's reset happen on the very next rerun. AppTest limitation
   found while writing this: at.run() resolves an st.rerun() chain
   synchronously in one call rather than pausing after each hop, so the
   intermediate disabled/"Syncing..." frame is never independently
   observable through AppTest's element tree - by the time any .run() call
   returns, the script has already run to a rerun-free steady state. In a
   real browser each st.rerun() is its own network round-trip with its own
   visible paint, so the intermediate state IS what a real user sees; that
   part is confirmed by live browser verification instead (see this
   worktree's live-verification notes), matching the documented-limitation
   treatment already used for canvas ButtonColumns and AppTest expander
   force-opens elsewhere in this suite. What AppTest CAN reliably confirm
   is covered below: the button's steady (non-syncing) state, and that a
   full click-to-resolution cycle actually calls the real fulfillment
   function and ends up back in a clean, non-stuck state.

2. "Reload view" (plain st.rerun(), no real purpose beyond catching a
   background-scheduled-task update Zahir hadn't otherwise triggered a
   rerun for) is removed entirely, replaced by _cta_auto_refresh_watcher()
   - an st.fragment(run_every="45s") that polls a cheap on-disk signature
   and forces a full rerun only when something genuinely changed. AppTest
   doesn't fire fragment timers, so the fragment's own ticking isn't
   testable here (same class of limitation as canvas ButtonColumns
   elsewhere in this suite) - what's covered instead is the pure signature
   function it polls, which is what actually decides whether to refresh."""

import pytest
from streamlit.testing.v1 import AppTest

from tailoring.cta_emails import add_cta_email

APP_PATH = "src/ui/app.py"


@pytest.fixture
def cta_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


def _fake_run_full_fulfillment():
    return {"archived": 2, "cta_drafts": 1, "outreach_drafts": 0, "failures": 0}


def test_sync_button_starts_enabled_with_its_normal_label(cta_app):
    at = cta_app
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)

    assert not at.exception
    sync_btn = next(b for b in at.button if b.key == "manual_sync_button")
    assert sync_btn.label == "Send and receive"
    assert not sync_btn.proto.disabled


def test_clicking_sync_calls_real_fulfillment_and_resolves_cleanly(cta_app, monkeypatch):
    # What AppTest CAN reliably confirm about the click (see module
    # docstring for why the intermediate disabled/"Syncing..." frame
    # itself isn't independently observable here): it actually invokes
    # run_full_fulfillment (not a no-op), and settles back into a clean,
    # non-stuck steady state rather than staying disabled forever.
    import fulfillment

    calls = []
    monkeypatch.setattr(fulfillment, "run_full_fulfillment", lambda: (calls.append(1), _fake_run_full_fulfillment())[1])

    at = cta_app
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)

    at.button(key="manual_sync_button").click().run(timeout=30)

    assert not at.exception
    assert calls == [1]
    sync_btn = next(b for b in at.button if b.key == "manual_sync_button")
    assert sync_btn.label == "Send and receive"
    assert not sync_btn.proto.disabled
    assert not st_session_state_true(at, "cta_sync_in_progress")
    assert any("Synced" in t.value for t in at.toast)


def test_reload_view_button_is_gone(cta_app):
    at = cta_app
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)

    assert not at.exception
    assert not any(b.label == "Reload view" for b in at.button)


def test_watch_signature_changes_when_a_new_cta_email_appears(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import ui.app as app_module

    before = app_module._cta_watch_signature()
    add_cta_email("t-offer", "An offer", "hr@acme.com", "", "2026-08-01", "offer")
    after = app_module._cta_watch_signature()

    assert before != after


def test_watch_signature_is_stable_when_nothing_changed(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import ui.app as app_module

    add_cta_email("t-offer", "An offer", "hr@acme.com", "", "2026-08-01", "offer")
    first = app_module._cta_watch_signature()
    second = app_module._cta_watch_signature()

    assert first == second


def st_session_state_true(at, key) -> bool:
    try:
        return bool(at.session_state[key])
    except Exception:
        return False
