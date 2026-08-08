"""Covers the 2026-08-08 score-first resume flow (docs/score-first-resume-
flow-spec.md, Option B layout, items 3/5/6 - the frontend half; items
1/2/4/7 are ATS Engine's backend work). Replaces the old "answer a
question, regenerate the whole resume every single time" pattern
(render_gap_questions_section) with render_analyze_fit_section(): answers
now auto-save on every rerun with no button required (item 4), and
generating is a separate, explicit action (item 6's confirmation logic)
rather than bundled into the same click as saving.

tailoring.score_first_flow_stub's analyze_fit_before_drafting() and
check_regenerate_needs_confirmation() are still temporary stubs pending
ATS Engine's real backend functions - these tests exercise the real
UI/wiring logic (rendering, auto-save, button branching, dialog gating)
against the stubs' honest fallback behavior, and monkeypatch
check_regenerate_needs_confirmation directly (on the real stub MODULE,
not the name app.py imports it as - AppTest re-executes app.py fresh
every .run(), so patching a name defined/aliased inside app.py itself
never takes effect; see score_first_flow_stub.py's own docstring) where a
test needs to force a specific branch (has_new_info True/False) that the
current stub's fixed default wouldn't otherwise reach."""

import pytest
from streamlit.testing.v1 import AppTest

import tailoring.score_first_flow_stub as score_first_flow_stub
from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application, get_application

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


def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
    return {"resume": {
        "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nPython, Databricks",
        "suggested_strategy_tag": "", "ats_score": 95,
        "ats_rationale": "Matched 2/2 keywords.", "ats_next_actions": [], "clarifying_questions": [],
    }}


def test_gap_question_renders_inline_on_results_tab(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    assert any(t.label == "The posting requires \"Databricks\" - do you have real, genuine experience with it?" for t in at.text_area)
    assert any(b.key and b.key.startswith("analyzefit_generate_") for b in at.button)
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "see the" not in markdown_text.lower() or "profile gaps" not in markdown_text.lower()


def test_gap_question_suggested_answer_prefilled_inline(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    assert box.value == "Unknown - please describe your real experience (if any) with this."


def test_skill_gap_question_shows_a_point_badge(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    badge_lines = [m.value for m in at.markdown if "-badge[" in m.value]
    assert any("pts" in b for b in badge_lines)


def test_disqualifier_question_gets_no_point_badge_and_distinct_flag_note(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    upsert_application(
        "Dice", "job1", status="under review",
        resume_clarifying_questions=[{
            "type": "disqualifier_check", "skill": "role_level",
            "question": "Should VP/CIO roles below a certain org size be excluded going forward?",
            "suggested_answer": "",
        }],
    )
    at.run(timeout=30)

    assert not at.exception
    badge_lines = [m.value for m in at.markdown if "-badge[" in m.value]
    assert any("standing pref" in b for b in badge_lines)
    assert not any("pts" in b for b in badge_lines)
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "applies to every" in markdown_text.lower()


def test_answer_saves_immediately_without_clicking_any_button(results_app_with_gap_questions):
    # Item 4: answers persist even if the user never reaches Generate -
    # real regression test for the old design's coupling of "answer
    # saved" to "document generated" (spec's explicit warning about this).
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    at.run(timeout=30)  # a rerun from the text_area itself, not any button

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile().get("gap_interview_answers", [])
    assert any(a["skill"] == "Databricks" and "migration" in a["answer"] for a in answers)
    # Nothing was regenerated just from answering.
    assert get_application("Dice", "job1")["resume_ats_score"] == 60


def test_generate_button_regenerates_using_the_confirmed_answer(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)
    # Stub always returns has_new_info=True today (see its own docstring) -
    # the non-blocking heads-up path, so Generate proceeds immediately.
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert app_record["resume_ats_score"] == 95


def test_resume_expander_auto_expands_right_after_answering_a_gap_question(results_app_with_gap_questions, monkeypatch):
    # Zahir's explicit ask 2026-08-06: the new score/rationale/questions
    # must be part of what he sees immediately after generating - not
    # behind a collapsed expander he has to remember to reopen.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    at.run(timeout=30)
    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    resume_expander = next(e for e in at.expander if e.label == "Resume (drafted)")
    assert resume_expander.proto.expanded


def test_score_delta_shown_after_regenerating(results_app_with_gap_questions, monkeypatch):
    # Zahir's explicit ask 2026-08-06: changing an answer must visibly show
    # the old -> new score change, reusing the existing delta-arrow pattern
    # already built for the JD-update flow, not just a bare new number.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("Yes, led a 2-year Databricks migration.")
    at.run(timeout=30)
    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    metric = next(m for m in at.metric if m.label == "ATS compatibility score")
    assert metric.value == "95/100"
    assert metric.delta is not None
    assert "35" in metric.delta  # 95 - 60 (fixture's original score)


def test_generate_with_no_new_info_opens_a_blocking_confirmation_dialog(results_app_with_gap_questions, monkeypatch):
    # Item 6: regenerating with nothing new confirmed is pure downside
    # risk (a full rewrite that could accidentally drop a matched
    # keyword) - forces the has_new_info=False branch the stub's current
    # default doesn't reach on its own, to verify the blocking gate.
    monkeypatch.setattr(
        score_first_flow_stub, "check_regenerate_needs_confirmation",
        lambda job, profile: {
            "has_new_info": False, "new_fact_count": None, "estimated_new_score": None,
            "cost_estimate": None, "last_generation_cost": 0.0421, "current_score": 60,
        },
    )
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    # No regeneration happened yet - blocked behind the dialog.
    assert get_application("Dice", "job1")["resume_ats_score"] == 60
    assert "regen_confirm_pending" in at.session_state
    dialog_text = " ".join(m.value for m in at.markdown)
    assert "0.04" in dialog_text
    assert any(b.key == "regen_confirm_confirm" for b in at.button)
    assert any(b.key == "regen_confirm_cancel" for b in at.button)


def test_confirming_the_blocking_dialog_regenerates(results_app_with_gap_questions, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)
    monkeypatch.setattr(
        score_first_flow_stub, "check_regenerate_needs_confirmation",
        lambda job, profile: {
            "has_new_info": False, "new_fact_count": None, "estimated_new_score": None,
            "cost_estimate": None, "last_generation_cost": 0.0421, "current_score": 60,
        },
    )
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    confirm_button = next(b for b in at.button if b.key == "regen_confirm_confirm")
    confirm_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["resume_ats_score"] == 95


def test_cancelling_the_blocking_dialog_does_not_regenerate(results_app_with_gap_questions, monkeypatch):
    monkeypatch.setattr(
        score_first_flow_stub, "check_regenerate_needs_confirmation",
        lambda job, profile: {
            "has_new_info": False, "new_fact_count": None, "estimated_new_score": None,
            "cost_estimate": None, "last_generation_cost": 0.0421, "current_score": 60,
        },
    )
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    cancel_button = next(b for b in at.button if b.key == "regen_confirm_cancel")
    cancel_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["resume_ats_score"] == 60
    assert "regen_confirm_pending" not in at.session_state


def test_no_open_questions_shows_the_exhausted_message(results_app_with_gap_questions):
    at = results_app_with_gap_questions
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    upsert_application("Dice", "job1", status="under review", resume_clarifying_questions=[])
    at.run(timeout=30)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "no more real gaps found" in markdown_text.lower()


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
