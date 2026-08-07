"""Covers the 2026-08-07 CTA header consolidation (Zahir's follow-up on the
status-card work, relayed via hub with a screenshot): the status
card, the standalone "Reload view" row, and the category stat-pill strip
were 3 separate stacked rows/containers - his words: "the send receive and
the offer, interview ... rejection should all be in the same line else it
looks NASTY." Merged into one `st.container(key="cta_header_row")` with a
scoped CSS flex-row rule (same .st-key-{container} pattern already used
elsewhere in this file) applied across every child instead of per-section.

Visual grouping/line-wrapping is CSS-only and not AppTest-testable (AppTest
doesn't run a real layout engine - same class of limitation as the
Send-and-receive/Reload-view width fix in test_cta_status_card.py). Verified
live instead: at a normal 1280px browser width, all 8 elements (status text,
2 buttons, 5 badges) land on one line in the common (non-stale) case; the
longest case (staleness warning present) needs a second line for the last 2
badges - a real, unavoidable content-length constraint, not a layout bug,
and still reads as one cohesive flex-wrapped group rather than 3 separate
boxes. Confirmed via real DOM coordinates (not text extraction alone) after
the Browser pane's page-visibility state initially returned bogus
zero-width layout metrics; re-verified once real viewport dimensions came
back. See hub report for the numbers.

This file only covers what AppTest CAN meaningfully check: that all 8
pieces still render together without error after the restructuring, and
that the pre-existing per-section content (covered in depth by
test_cta_status_card.py and test_cta_stat_strip.py, both still passing
unmodified) survived the merge intact."""

import pytest
from streamlit.testing.v1 import AppTest

from tailoring.cta_emails import add_cta_email

APP_PATH = "src/ui/app.py"


@pytest.fixture
def cta_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    add_cta_email("t-offer", "An offer", "hr@acme.com", "", "2026-08-01", "offer")
    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)
    return at


def test_status_text_buttons_and_all_five_badges_coexist_without_error(cta_app):
    at = cta_app
    assert not at.exception

    button_labels = {b.label for b in at.button}
    assert "Send and receive" in button_labels
    assert "Reload view" in button_labels

    badge_lines = [m.value for m in at.markdown if "-badge[" in m.value]
    for label in ("Offer", "Interview request", "Assessment / take-home task", "Recruiter question", "Rejection"):
        assert any(label in b for b in badge_lines), f"missing badge for {label}"

    status_lines = [m.value for m in at.markdown if "caught up" in m.value or "update" in m.value]
    assert len(status_lines) == 1
