"""Real design correction Zahir made live 2026-08-06: after clarifying_
questions moved off the Results tab entirely to the Profile Gaps tab
(2026-08-04, to reduce clutter while scanning many jobs), he opened one
job's own "Resume (drafted)" score card and expected to answer its
questions right there - not get pointed to a separate tab. Expanding a
specific job's score card is a clear signal of focus on THAT job; the
Profile Gaps tab still exists for scanning across every job at once, this
is additive. These tests drive the actual Results tab (via Streamlit's own
AppTest) to confirm the real, interactive Q&A now renders inline.
"""

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


def test_gap_question_renders_inline_on_results_tab(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    # The real interactive box, not just a pointer to the Profile Gaps tab.
    assert any(t.label == "The posting requires \"Databricks\" - do you have real, genuine experience with it?" for t in at.text_area)
    assert any(b.key and b.key.startswith("gapsave_") for b in at.button)
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "see the" not in markdown_text.lower() or "profile gaps" not in markdown_text.lower()


def test_gap_question_suggested_answer_prefilled_inline(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    assert box.value == "Unknown - please describe your real experience (if any) with this."


def test_answering_inline_on_results_tab_saves_and_regenerates(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
        return {"resume": {
            "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nPython, Databricks",
            "suggested_strategy_tag": "",
            "ats_score": 95,
            "ats_rationale": "Matched 2/2 keywords.",
            "ats_next_actions": [],
            "clarifying_questions": [],
        }}

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    save_button = next(b for b in at.button if b.key and b.key.startswith("gapsave_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    from tailoring.applications import get_application
    app_record = get_application("Dice", "job1")
    assert app_record["resume_ats_score"] == 95

    from profile.storage import load_profile
    answers = load_profile().get("gap_interview_answers", [])
    assert any(a["skill"] == "Databricks" and "migration" in a["answer"] for a in answers)


def test_resume_expander_auto_expands_right_after_answering_a_gap_question(results_app_with_gap_questions, monkeypatch):
    # Zahir's explicit ask 2026-08-06: the new score/rationale/questions
    # must be part of what he sees immediately after submitting an answer -
    # not behind a collapsed expander he has to remember to reopen.
    import tailoring.drafting as drafting

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
        return {"resume": {
            "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nPython, Databricks",
            "suggested_strategy_tag": "", "ats_score": 95,
            "ats_rationale": "Matched 2/2 keywords.", "ats_next_actions": [], "clarifying_questions": [],
        }}

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    save_button = next(b for b in at.button if b.key and b.key.startswith("gapsave_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert resume_expander.proto.expanded


def test_score_delta_shown_after_answering_a_gap_question(results_app_with_gap_questions, monkeypatch):
    # Zahir's explicit ask 2026-08-06: changing an answer must visibly show
    # the old -> new score change, reusing the existing delta-arrow pattern
    # already built for the JD-update flow, not just a bare new number.
    import tailoring.drafting as drafting

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
        return {"resume": {
            "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nPython, Databricks",
            "suggested_strategy_tag": "", "ats_score": 95,
            "ats_rationale": "Matched 2/2 keywords.", "ats_next_actions": [], "clarifying_questions": [],
        }}

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    save_button = next(b for b in at.button if b.key and b.key.startswith("gapsave_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    metric = next(m for m in at.metric if m.label == "ATS compatibility score")
    assert metric.value == "95/100"
    assert metric.delta is not None
    assert "35" in metric.delta  # 95 - 60 (fixture's original score)


def test_resume_expander_auto_expands_right_after_first_generate(isolated_data, monkeypatch):
    from search.job_store import save_jobs, update_job_score

    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{"source": "Dice", "job_id": "job2", "title": "Director, Fresh", "organization": "Acme Corp", "location": "Remote", "description": "Requirements: Python."}])
    update_job_score("Dice", "job2", 85, "Strong match.")

    import tailoring.drafting as drafting

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
        return {"resume": {
            "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nPython",
            "suggested_strategy_tag": "", "ats_score": 80,
            "ats_rationale": "Matched 1/1 keywords.", "ats_next_actions": [], "clarifying_questions": [],
        }}

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_resume_"))
    checkbox.set_value(True)
    gen_button = next(b for b in at.button if b.key and b.key.startswith("gendocs_"))
    gen_button.click().run(timeout=30)

    assert not at.exception
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert resume_expander.proto.expanded


def test_gap_question_also_still_appears_on_profile_gaps_tab(results_app_with_gap_questions):
    # Additive, not a replacement - Zahir's explicit requirement, same
    # principle as the JD-box design: Profile Gaps still consolidates
    # across every job for scanning; Results tab is for a job already in
    # focus. Both must work.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "gaps"
    at.run(timeout=30)

    assert not at.exception
    assert any("Databricks" in t.label for t in at.text_area)
