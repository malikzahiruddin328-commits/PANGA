"""Covers the 2026-08-06 Call to Action stat-strip compaction (Zahir's
live-testing feedback, relayed via hub): the 5 category counters
(Offer/Interview request/Assessment/Recruiter question/Rejection) used to be
5 full-width st.columns() each holding an st.metric() - mostly empty space
around a single digit, since st.columns() always spans the full container
width regardless of how little a column's content needs. Replaced with a
single wrapping horizontal row of st.badge() widgets, label+count folded
into one string.

AppTest has no layout/CSS assertions (it only sees the element tree, not
computed styles), so what's testable here is the *content* - all 5
categories present, in the same label+count text form, with counts that
track the real data - not the one-line rendering itself (verified live in
the browser instead, see hub report).

st.badge has no dedicated AppTest element collection in this Streamlit
version (confirmed empirically - `at.badge` doesn't exist); it's
implemented internally via the `:color-badge[...]` markdown directive
syntax, so it surfaces through `at.markdown` as that raw source string."""

import re

import pytest
from streamlit.testing.v1 import AppTest

from tailoring.cta_emails import add_cta_email

APP_PATH = "src/ui/app.py"


def _stat_strip_counts(at):
    """Maps each badge's visible label text to its count, by scanning
    at.markdown for the `:*-badge[Label **N**]` directives the stat strip
    renders - doesn't hardcode which color each category uses, so it stays
    correct if CATEGORY_COLORS ever changes."""
    counts = {}
    pattern = re.compile(r":\S+-badge\[(.+?) \*\*(\d+)\*\*\]")
    for m in at.markdown:
        match = pattern.fullmatch(m.value)
        if match:
            counts[match.group(1)] = int(match.group(2))
    return counts


@pytest.fixture
def cta_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    add_cta_email("t-offer", "An offer", "hr@acme.com", "", "2026-08-01", "offer")
    add_cta_email("t-interview-1", "Interview 1", "hr@acme.com", "", "2026-08-01", "interview_request")
    add_cta_email("t-interview-2", "Interview 2", "hr@acme.com", "", "2026-08-01", "interview_request")
    add_cta_email("t-reject-1", "No thanks", "hr@acme.com", "", "2026-08-01", "rejection")
    add_cta_email("t-reject-2", "Also no", "hr@acme.com", "", "2026-08-01", "rejection")
    add_cta_email("t-reject-3", "Still no", "hr@acme.com", "", "2026-08-01", "rejection")

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "cta"
    at.run(timeout=30)
    return at


def test_stat_strip_shows_all_five_categories_with_correct_counts(cta_app):
    at = cta_app
    assert not at.exception

    counts = _stat_strip_counts(at)
    assert counts == {
        "Offer": 1,
        "Interview request": 2,
        "Assessment / take-home task": 0,
        "Recruiter question": 0,
        "Rejection": 3,
    }


def test_stat_strip_counts_update_after_new_email_lands(cta_app):
    at = cta_app
    add_cta_email("t-offer-2", "Second offer", "hr@acme.com", "", "2026-08-02", "offer")
    at.run(timeout=30)

    assert not at.exception
    counts = _stat_strip_counts(at)
    assert counts["Offer"] == 2
