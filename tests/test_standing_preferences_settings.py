"""Part 3 of the 2026-08-18 build (Zahir's real live ask): disqualifier-type
questions ("Security clearance", "Washington, DC relocation and
compensation", etc.) move out of the per-job clarifying-questions panel
entirely and into a dedicated Settings > "Standing preferences" section,
declared once. See tests/test_results_tab_gap_questions.py for the per-job
removal side, tests/test_drafting.py's
test_analyze_fit_before_drafting_never_returns_a_disqualifier_check_question
for the backend filter, and src/ui/app.py's _pending_disqualifier_questions
for the cross-job aggregation this section renders."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs
from tailoring.applications import upsert_application
from profile.interview import save_answer

APP_PATH = "src/ui/app.py"


@pytest.fixture
def settings_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    return AppTest.from_file(APP_PATH)


def _seed_job_with_disqualifier_question(source, job_id, skill, question):
    save_jobs([{
        "source": source, "job_id": job_id, "title": "Director", "organization": "Acme",
        "location": "Remote", "description": "A role.",
        "ats_required_keywords": [], "ats_preferred_keywords": [],
    }])
    upsert_application(
        source, job_id, status="under review",
        resume_clarifying_questions=[{
            "type": "disqualifier_check", "skill": skill, "question": question,
            "suggested_answer": "",
        }],
    )


def test_undeclared_disqualifier_question_surfaces_in_settings(settings_app):
    _seed_job_with_disqualifier_question(
        "Dice", "job1", "Security clearance",
        "Should postings requiring an active security clearance be excluded going forward?",
    )
    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    assert not at.exception
    subheader_text = " ".join(s.value for s in at.subheader)
    assert "Standing preferences" in subheader_text
    assert any("Security clearance" in m.value for m in at.markdown)
    assert any("active security clearance" in m.value for m in at.markdown)


def test_declaring_a_pending_disqualifier_saves_it_and_removes_it_from_pending(settings_app):
    _seed_job_with_disqualifier_question(
        "Dice", "job1", "Security clearance",
        "Should postings requiring an active security clearance be excluded going forward?",
    )
    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    box = next(t for t in at.text_area if t.key and t.key.startswith("standingpref_new_"))
    box.set_value("Yes, exclude these - I do not hold an active clearance.")
    save_button = next(b for b in at.button if b.key and b.key.startswith(box.key) and b.key.endswith("_save"))
    save_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    match = next((a for a in answers if a["skill"] == "Security clearance"), None)
    assert match is not None
    assert match["is_disqualifier"] is True
    assert "exclude" in match["answer"].lower()

    # Re-render: the now-declared disqualifier no longer shows as pending.
    at.run(timeout=30)
    assert not any(t.key and t.key.startswith("standingpref_new_") for t in at.text_area)


def test_already_declared_disqualifier_shows_in_already_declared_list(settings_app):
    save_answer(
        skill="Washington DC relocation", role_context="Settings - Standing preferences",
        answer="No, I will not relocate to DC.", date_captured="2026-08-01",
        question="Would you relocate to Washington, DC for the right compensation?",
        is_disqualifier=True,
    )
    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    assert not at.exception
    assert any("Washington DC relocation" in m.value for m in at.markdown)
    assert any("No, I will not relocate to DC." in t.value for t in at.text_area)


def test_declared_disqualifier_does_not_also_appear_as_pending(settings_app):
    # Same underlying skill, worded identically in both places - a
    # disqualifier already declared must not ALSO show up in the "not yet
    # declared" list just because a job's stored question still carries
    # the raw AI-proposed text.
    save_answer(
        skill="Security clearance", role_context="Settings - Standing preferences",
        answer="Exclude these.", date_captured="2026-08-01",
        question="Should postings requiring an active security clearance be excluded going forward?",
        is_disqualifier=True,
    )
    _seed_job_with_disqualifier_question(
        "Dice", "job1", "Security clearance",
        "Should postings requiring an active security clearance be excluded going forward?",
    )
    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    assert not at.exception
    assert not any(t.key and t.key.startswith("standingpref_new_") for t in at.text_area)
    # But it IS shown once in the "already declared" section.
    assert any("Security clearance" in m.value for m in at.markdown)


def test_two_jobs_proposing_the_same_disqualifier_topic_surface_once(settings_app):
    _seed_job_with_disqualifier_question(
        "Dice", "job1", "Security clearance", "Exclude clearance-required roles?",
    )
    _seed_job_with_disqualifier_question(
        "LinkedIn", "job2", "Security clearance", "Exclude clearance-required roles going forward?",
    )
    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    assert not at.exception
    pending_boxes = [t for t in at.text_area if t.key and t.key.startswith("standingpref_new_")]
    assert len(pending_boxes) == 1


def test_updating_an_already_declared_disqualifier(settings_app):
    save_answer(
        skill="Security clearance", role_context="Settings - Standing preferences",
        answer="Exclude these.", date_captured="2026-08-01",
        question="Exclude clearance-required roles?", is_disqualifier=True,
    )
    at = settings_app
    at.session_state["active_tab"] = "settings"
    at.run(timeout=30)

    box = next(t for t in at.text_area if t.key and t.key.startswith("standingpref_edit_"))
    box.set_value("Actually, include these now - I've since obtained one.")
    update_button = next(b for b in at.button if b.key and b.key.startswith(box.key) and b.key.endswith("_save"))
    update_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    match = next(a for a in answers if a["skill"] == "Security clearance")
    assert "obtained one" in match["answer"]
    assert match["is_disqualifier"] is True
