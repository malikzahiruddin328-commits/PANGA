"""Part 1 of the 2026-08-18 build (Zahir's real live ask): an "Accept all"
button at the bottom of a job's clarifying-questions list in
render_analyze_fit_section (src/ui/app.py) that bulk-confirms every eligible
suggested answer currently shown, then triggers this job's own $0
subscription redraft round (tailoring.subscription_resume_qa.
run_subscription_round) so the newly confirmed facts actually get folded
into the resume text/score - not just saved and left stale on screen.

Eligibility mirrors the existing per-question "Confirm as shown" button
exactly: not a disqualifier_check question, and point_value is not None.
Placeholder ("no reference found...") answers and disqualifier questions are
skipped - there's nothing real to force-accept there."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application, get_application
from tailoring.ats_score import score_resume_against_keywords
from tailoring.drafting import no_reference_found_answer

APP_PATH = "src/ui/app.py"

REQUIRED_KEYWORDS = ["Python", "Databricks", "Snowflake"]
INITIAL_RESUME_TEXT = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
_SCORE_RESULT = score_resume_against_keywords(REQUIRED_KEYWORDS, [], INITIAL_RESUME_TEXT)
INITIAL_SCORE = _SCORE_RESULT["ats_score"]
_POINT_VALUES = {item["label"]: item["point_value"] for item in _SCORE_RESULT["missing_required_keywords"]}


def _safe_no_op_gap_scan(job, profile, app_record, model=None, on_progress=None):
    return {"added_count": 0, "new_questions": [], "merged_clarifying_questions": app_record.get("resume_clarifying_questions") or []}


@pytest.fixture
def accept_all_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    import tailoring.drafting as drafting
    from tailoring.drafting import gap_scan_baseline_fingerprint

    monkeypatch.setattr(drafting, "request_additional_gap_questions", _safe_no_op_gap_scan)

    job = {
        "source": "Dice", "job_id": "job1", "title": "Director, Gaps", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python, Databricks, Snowflake.",
        "ats_required_keywords": REQUIRED_KEYWORDS, "ats_preferred_keywords": [],
    }
    save_jobs([job])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text=INITIAL_RESUME_TEXT,
        resume_ats_score=INITIAL_SCORE, resume_ats_rationale=_SCORE_RESULT["ats_rationale"],
        resume_ats_next_actions=[],
        resume_clarifying_questions=[
            {
                "type": "skill_gap", "skill": "Databricks",
                "question": "The posting requires \"Databricks\" - do you have real, genuine experience with it?",
                "suggested_answer": "Roughly 3 years, hands-on.",
                "point_value": _POINT_VALUES["Databricks"],
            },
            {
                "type": "skill_gap", "skill": "Snowflake",
                "question": "The posting requires \"Snowflake\" - do you have real, genuine experience with it?",
                "suggested_answer": no_reference_found_answer("Snowflake"),
                "point_value": _POINT_VALUES["Snowflake"],
            },
        ],
        resume_gap_scan_fingerprint=gap_scan_baseline_fingerprint(job, {"resume_text": INITIAL_RESUME_TEXT}),
    )
    return AppTest.from_file(APP_PATH)


def _fake_run_subscription_round_success(job, profile, on_progress=None):
    return {"ok": True, "locked": False, "round": 1, "resume_draft": {"ats_score": 95}, "error": None}


def test_accept_all_button_renders_below_the_question_list(accept_all_app):
    at = accept_all_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    assert any(b.key and b.key.startswith("acceptall_") for b in at.button)


def test_accept_all_confirms_eligible_answers_and_triggers_a_redraft(accept_all_app, monkeypatch):
    import tailoring.subscription_resume_qa as sqa

    called = []

    def _fake(job, profile, on_progress=None):
        called.append((job.get("source"), job.get("job_id")))
        return _fake_run_subscription_round_success(job, profile, on_progress)

    monkeypatch.setattr(sqa, "run_subscription_round", _fake)

    at = accept_all_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    accept_button = next(b for b in at.button if b.key and b.key.startswith("acceptall_"))
    accept_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    # Databricks (real suggested_answer, point_value set) got confirmed.
    databricks = next((a for a in answers if a["skill"] == "Databricks"), None)
    assert databricks is not None
    assert "3 years" in databricks["answer"]
    # Snowflake's box was the honest "no reference found" placeholder -
    # skipped, nothing real to accept.
    assert not any(a["skill"] == "Snowflake" for a in answers)
    # The redraft round was actually triggered, exactly once, for this job.
    assert called == [("Dice", "job1")]
    assert any("Accepted" in t.value for t in at.toast)


def test_accept_all_button_absent_when_nothing_is_eligible(accept_all_app):
    # Every question is either a disqualifier or a "no reference found"
    # placeholder - nothing real to bulk-accept, so the button shouldn't
    # even be offered.
    at = accept_all_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    upsert_application(
        "Dice", "job1", status="under review",
        resume_clarifying_questions=[
            {
                "type": "skill_gap", "skill": "Snowflake",
                "question": "The posting requires \"Snowflake\" - do you have real, genuine experience with it?",
                "suggested_answer": no_reference_found_answer("Snowflake"),
                "point_value": _POINT_VALUES["Snowflake"],
            },
        ],
    )
    at.run(timeout=30)

    assert not at.exception
    assert not any(b.key and b.key.startswith("acceptall_") for b in at.button)


def test_accept_all_skips_disqualifier_questions(accept_all_app, monkeypatch):
    # A disqualifier_check question is never even rendered here any more
    # (Part 3 of this same build), so it can never end up in the accepted
    # set - this locks that behavior specifically from Accept all's own
    # eligibility filter's point of view, not just the render-time filter.
    import tailoring.subscription_resume_qa as sqa

    monkeypatch.setattr(sqa, "run_subscription_round", _fake_run_subscription_round_success)

    at = accept_all_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    upsert_application(
        "Dice", "job1", status="under review",
        resume_clarifying_questions=[
            {
                "type": "skill_gap", "skill": "Databricks",
                "question": "The posting requires \"Databricks\" - do you have real, genuine experience with it?",
                "suggested_answer": "Roughly 3 years, hands-on.",
                "point_value": _POINT_VALUES["Databricks"],
            },
            {
                "type": "disqualifier_check", "skill": "role_level",
                "question": "Should VP/CIO roles below a certain org size be excluded going forward?",
                "suggested_answer": "",
            },
        ],
    )
    at.run(timeout=30)

    accept_button = next(b for b in at.button if b.key and b.key.startswith("acceptall_"))
    accept_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    assert any(a["skill"] == "Databricks" for a in answers)
    assert not any(a.get("is_disqualifier") for a in answers)


def test_accept_all_uses_current_edited_box_text_not_the_original_suggestion(accept_all_app, monkeypatch):
    import tailoring.subscription_resume_qa as sqa

    monkeypatch.setattr(sqa, "run_subscription_round", _fake_run_subscription_round_success)

    at = accept_all_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    box = next(t for t in at.text_area if "Databricks" in t.label)
    box.set_value("5 years leading a Databricks lakehouse migration.")

    accept_button = next(b for b in at.button if b.key and b.key.startswith("acceptall_"))
    accept_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    match = next(a for a in answers if a["skill"] == "Databricks")
    assert "lakehouse migration" in match["answer"]


def test_accept_all_shows_a_locked_toast_when_generation_lock_is_busy(accept_all_app, monkeypatch):
    import tailoring.subscription_resume_qa as sqa

    def _fake_locked(job, profile, on_progress=None):
        return {"ok": False, "locked": True, "round": None, "resume_draft": None, "error": None}

    monkeypatch.setattr(sqa, "run_subscription_round", _fake_locked)

    at = accept_all_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    accept_button = next(b for b in at.button if b.key and b.key.startswith("acceptall_"))
    accept_button.click().run(timeout=30)

    assert not at.exception
    # Answers still saved even though the redraft itself was skipped.
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    assert any(a["skill"] == "Databricks" for a in answers)
    assert any("busy" in t.value.lower() or "lock" in t.value.lower() for t in at.toast)
