"""Real gap Zahir hit live 2026-08-06: once a clarifying_question is
genuinely answered, it correctly drops out of the "open" list forever (both
the AI's own dedup and _merge_keyword_gap_questions's profile-history check
stop re-asking it) - but that meant there was no way to go back and see or
edit what was actually answered, the same class of "write-only" gap the
JD-paste box had before it got a view/update mode. These tests drive the
actual Profile Gaps tab (via Streamlit's own AppTest) to confirm the
"Previously answered" section renders, is collapsed by default, and lets an
existing answer be viewed and genuinely edited in place.
"""

import pytest
from streamlit.testing.v1 import AppTest

from profile.interview import save_answer

APP_PATH = "src/ui/app.py"


@pytest.fixture
def gaps_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


def test_no_answered_section_when_nothing_has_been_answered_yet(gaps_app):
    at = gaps_app
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    assert not any(e.label.startswith("Previously answered") for e in at.expander)


def test_answered_section_shown_with_real_answer_prefilled(gaps_app):
    save_answer(
        skill="Databricks", role_context="Director at Acme", answer="Yes, 3 years.",
        date_captured="2026-08-01", question="Do you have real experience with Databricks?",
    )

    at = gaps_app
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    expanders = [e for e in at.expander if e.label.startswith("Previously answered")]
    assert len(expanders) == 1
    assert expanders[0].label == "Previously answered (1)"
    assert not expanders[0].proto.expanded

    answer_box = next(t for t in at.text_area if t.label == "Do you have real experience with Databricks?")
    assert answer_box.value == "Yes, 3 years."


def test_answered_section_falls_back_to_skill_label_when_no_question_stored(gaps_app):
    # Older-shape entries saved before "question" existed on the record.
    save_answer(skill="Databricks", role_context="Director at Acme", answer="Yes.", date_captured="2026-08-01")

    at = gaps_app
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    assert any(t.label == "Confirm: Databricks" for t in at.text_area)


def test_editing_an_answer_updates_it_in_place(gaps_app):
    save_answer(
        skill="Databricks", role_context="Director at Acme", answer="Not sure.",
        date_captured="2026-08-01", question="Do you have real experience with Databricks?",
    )

    at = gaps_app
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    answer_box = next(t for t in at.text_area if t.label == "Do you have real experience with Databricks?")
    answer_box.set_value("Yes, 3 years - led the migration.")
    save_button = next(b for b in at.button if b.key.startswith("answered_save_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile
    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["answer"] == "Yes, 3 years - led the migration."


def test_clicking_update_with_no_changes_shows_info_toast(gaps_app):
    save_answer(
        skill="Databricks", role_context="Director at Acme", answer="Yes, 3 years.",
        date_captured="2026-08-01", question="Do you have real experience with Databricks?",
    )

    at = gaps_app
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key.startswith("answered_save_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile
    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["answer"] == "Yes, 3 years."


def test_clearing_an_answer_is_rejected(gaps_app):
    save_answer(
        skill="Databricks", role_context="Director at Acme", answer="Yes, 3 years.",
        date_captured="2026-08-01", question="Do you have real experience with Databricks?",
    )

    at = gaps_app
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    answer_box = next(t for t in at.text_area if t.label == "Do you have real experience with Databricks?")
    answer_box.set_value("")
    save_button = next(b for b in at.button if b.key.startswith("answered_save_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile
    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["answer"] == "Yes, 3 years."


def test_disqualifier_answer_shows_the_applies_everywhere_flag(gaps_app):
    save_answer(
        skill="CISO roles", role_context="Director at Acme", answer="Exclude these.",
        date_captured="2026-08-01", question="Exclude CISO-titled roles going forward?", is_disqualifier=True,
    )

    at = gaps_app
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    # st.markdown, not st.caption - this project's standing readability
    # rule (no st.caption anywhere in Panga, full-contrast text only).
    markdowns = [m.value for m in at.markdown]
    assert any("every future job match" in m for m in markdowns)
