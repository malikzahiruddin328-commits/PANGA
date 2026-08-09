"""Regression tests for a real bug (Zahir live-testing, 2026-08-07): the
Results tab's channel expander and "Resume (drafted)" expander both
collapsed on him mid-form the moment he answered a clarifying question and
moved to the next one - well before he even clicked "Save answers &
regenerate resume". Root cause: both expanders relied on key= alone (or a
one-shot session_state.pop() flag) with a plain `expanded=` argument
re-evaluated fresh on every rerun - confirmed via the real installed
st.expander's own docstring that key= alone does NOT persist expanded
state without on_change="rerun" or a callable; without it, Python's
`expanded=` argument is authoritative on every render, silently
overwriting whatever the user's own click set.

Fixed by giving both expanders on_change="rerun" so st.session_state[key]
becomes the real, Streamlit-maintained source of truth, plus (for the
resume expander) defaulting open when the job has genuinely unanswered
clarifying questions, not just right after a fresh generation."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app_with_gap_questions(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    save_jobs([
        {"source": "Dice", "job_id": "job1", "title": "Director, Gaps", "organization": "Acme Corp", "location": "Remote", "description": "Requirements: Python, Databricks."},
    ])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython",
        resume_ats_score=60, resume_ats_rationale="Matched 1/2 keywords.", resume_ats_next_actions=[],
        resume_clarifying_questions=[{
            "type": "skill_gap", "skill": "Databricks",
            "question": "The posting requires \"Databricks\" - do you have real, genuine experience with it?",
            "suggested_answer": "Unknown - please describe your real experience (if any) with this.",
        }],
    )
    return AppTest.from_file(APP_PATH)


def test_both_expanders_stay_open_while_answering_without_saving(results_app_with_gap_questions):
    # The exact reported scenario: type an answer and move on, WITHOUT
    # clicking "Save answers & regenerate resume" yet - any text_area
    # losing focus already triggers a script rerun on its own.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    # Simulate the user having already opened both expanders by hand.
    at.session_state["channel_expander_Dice"] = True
    at.session_state["resume_drafted_open_Dice_job1"] = True
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, I've led a couple of Databricks migrations.")
    at.run(timeout=30)  # rerun from the text_area change, not a save click

    assert not at.exception
    channel_expander = next(e for e in at.expander if e.label.startswith("Dice"))
    assert channel_expander.proto.expanded
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert resume_expander.proto.expanded
    box = next(t for t in at.text_area if "Databricks" in t.label)
    assert box.value == "Yes, I've led a couple of Databricks migrations."


def test_resume_expander_defaults_open_when_job_has_unanswered_questions(results_app_with_gap_questions):
    # A fresh session, no just_drafted flag in play at all - coming back
    # to a job left mid-answer shouldn't require remembering to reopen the
    # section by hand.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert resume_expander.proto.expanded


def test_resume_expander_manual_collapse_is_not_reforced_open(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert resume_expander.proto.expanded  # defaulted open first

    at.session_state["resume_drafted_open_Dice_job1"] = False  # user collapses it
    at.run(timeout=30)

    assert not at.exception
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert not resume_expander.proto.expanded


def test_channel_expander_default_collapsed_state_is_unchanged(results_app_with_gap_questions):
    # The fix is on_change="rerun" for real persistence, not a change to
    # the collapsed-by-default behavior itself.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    channel_expander = next(e for e in at.expander if e.label.startswith("Dice"))
    assert not channel_expander.proto.expanded
