"""Covers the 2026-08-06 Call to Action status-card batch (Zahir's
live-testing feedback, relayed via hub, found on the production instance):

1. Compact the whole row (icon/headline/last-synced/button) onto one line -
   was a big H2-sized icon in its own column next to two stacked text lines.
2. Fix the "Send and receive" vs "Reload view" button-size mismatch - the
   former forced width="stretch", the latter (right below it) already
   defaults to content-hugging width. Not testable via AppTest (ButtonProto
   has no width field in this Streamlit version - same class of layout-only
   limitation as the Results-tab ButtonColumn canvas-click gap already
   documented in test_results_tab_batch.py), so this one was verified live
   in the browser instead: both buttons' stElementContainer now report the
   same width="fit-content" attribute (see hub report).
3. Add a staleness warning on "Last synced" once it exceeds
   CTA_SCAN_STALE_AFTER_HOURS (double the real 4x/day scan cadence -
   docs/email-monitoring-task.md - so one slightly-late run doesn't
   false-positive)."""

import pytest
from streamlit.testing.v1 import AppTest

import fulfillment
from security.crypto_store import write_json

APP_PATH = "src/ui/app.py"


def _set_last_synced(hours_ago):
    from datetime import datetime, timedelta, timezone

    synced_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    write_json(fulfillment.SYNC_STATUS_PATH, {"last_synced_at": synced_at.isoformat()})


def _status_line(at):
    for m in at.markdown:
        if "caught up" in m.value or ("update" in m.value and "to send" in m.value):
            return m.value
    return None


@pytest.fixture
def cta_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


def test_fresh_sync_has_no_staleness_warning(cta_app):
    _set_last_synced(hours_ago=0.1)
    at = cta_app
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)

    assert not at.exception
    line = _status_line(at)
    assert line is not None
    assert "All caught up" in line
    assert ":orange[" not in line
    assert "overdue" not in line


def test_stale_sync_shows_amber_warning(cta_app):
    _set_last_synced(hours_ago=20)
    at = cta_app
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)

    assert not at.exception
    line = _status_line(at)
    assert line is not None
    assert ":orange[" in line
    assert "may be overdue" in line
    assert "20 hour" in line


def test_staleness_threshold_is_double_the_real_scan_cadence():
    # docs/email-monitoring-task.md: panga-gmail-cta-scan runs 4x/day.
    import ui.app as app_module

    assert app_module.CTA_SCAN_INTERVAL_HOURS == 4
    assert app_module.CTA_SCAN_STALE_AFTER_HOURS == 8


def test_never_synced_counts_as_stale(cta_app):
    at = cta_app
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)

    assert not at.exception
    line = _status_line(at)
    assert line is not None
    assert ":orange[" in line
    assert "Never synced yet" in line
