"""Systemic sweep (PRD §13, "Systemic sweep: raw AI-generated text rendered
via st.markdown()..."): the unconfirmed-claims branch (commit dd4ce48) fixed
two spots where a literal "$" in AI-generated text got auto-interpreted as
inline KaTeX by st.markdown, corrupting the display (e.g. "$300K-500K"
rendering with the dollar sign stripped and the hyphen turned into a minus
sign). That same risk existed in ~24 other places across app.py rendering
raw AI-generated/free-text content the same unescaped way - this sweep
applies _escape_markdown_dollar()/st_markdown_raw_text() across all of them.

Exhaustively AppTest-covering all ~24 spots isn't worth the fixture cost -
the fix is mechanically identical everywhere (visually reviewed at every
site). These tests cover a representative sample across genuinely different
plumbing (a stored applications.json field, a stored prospector-score file,
pure session_state, and a mocked AI-drafting call) to prove the pattern
holds across all of them, not just one."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score

APP_PATH = "src/ui/app.py"

DOLLAR_TEXT = "Drove savings of $300K-$500K by renegotiating vendor contracts."
ESCAPED_DOLLAR_TEXT = "Drove savings of \\$300K-\\$500K by renegotiating vendor contracts."


@pytest.fixture
def base_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


def test_resume_ats_rationale_and_next_actions_are_escaped(isolated_data, monkeypatch):
    from tailoring.applications import upsert_application

    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Claims", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nSomething.\n\nEDUCATION\nBS",
        resume_ats_score=80, resume_ats_rationale=DOLLAR_TEXT,
        resume_ats_next_actions=[DOLLAR_TEXT],
    )
    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert ESCAPED_DOLLAR_TEXT in markdown_text
    # Two spots use this exact text (rationale + the next_actions bullet) -
    # both must be escaped, not just the first one found.
    assert markdown_text.count(ESCAPED_DOLLAR_TEXT) == 2


def test_prospector_score_rationale_and_next_actions_are_escaped(isolated_data, monkeypatch):
    from prospector.prospector_score import save_prospector_score

    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_prospector_score(
        score=72, rationale=DOLLAR_TEXT, next_actions=[DOLLAR_TEXT],
        data_points=5, computed_at="2026-08-10T12:00:00+00:00",
    )
    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "prospector"
    at.run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert ESCAPED_DOLLAR_TEXT in markdown_text
    assert markdown_text.count(ESCAPED_DOLLAR_TEXT) == 2


def test_rejection_diagnosis_narrative_and_recommendations_are_escaped(base_app):
    at = base_app
    at.session_state["active_tab"] = "prospector"
    at.session_state["diagnosis_result"] = {
        "narrative": DOLLAR_TEXT,
        "recommendations": [DOLLAR_TEXT],
    }
    at.run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert ESCAPED_DOLLAR_TEXT in markdown_text
    assert markdown_text.count(ESCAPED_DOLLAR_TEXT) == 2


def test_learn_engine_narrative_recommendations_and_gaps_are_escaped(base_app):
    at = base_app
    at.session_state["active_tab"] = "prospector"
    at.session_state["learn_engine_result"] = {
        "narrative": DOLLAR_TEXT,
        "recommendations": [DOLLAR_TEXT],
        "known_gaps": [DOLLAR_TEXT],
    }
    at.run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert ESCAPED_DOLLAR_TEXT in markdown_text
    assert markdown_text.count(ESCAPED_DOLLAR_TEXT) == 3


def test_interview_prep_company_snapshot_research_summary_and_talking_point_are_escaped(base_app, monkeypatch):
    import tailoring.interview_prep as interview_prep

    save_jobs([{"source": "Indeed", "job_id": "p1", "title": "VP Engineering", "organization": "Acme Corp", "location": "Remote"}])

    def _fake_generate_prep(job, profile, interviewers, on_progress=None):
        return {
            "interviewers": [{
                "name": "Jane Doe", "title": "CTO",
                "research_summary": DOLLAR_TEXT,
                "persona": DOLLAR_TEXT,
            }],
            "company_snapshot": DOLLAR_TEXT,
            "likely_questions": [{"question": DOLLAR_TEXT, "why": DOLLAR_TEXT, "talking_point": DOLLAR_TEXT}],
            "questions_to_ask": [{"question": DOLLAR_TEXT}],
        }

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)

    at = base_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)
    at.button(key="generate_prep_btn").click().run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    # company_snapshot, research_summary, persona, likely_questions'
    # question/why/talking_point, and questions_to_ask's question - 7
    # distinct escaped occurrences from one round's worth of AI content.
    assert markdown_text.count(ESCAPED_DOLLAR_TEXT) == 7
