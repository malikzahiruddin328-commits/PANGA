"""The review/resolve UI for tailoring.unconfirmed_claims.find_unconfirmed_markers()
(render_unconfirmed_claims_section in ui/app.py) - the piece that actually
lets Zahir turn a hedged "?" guess into a confirmed fact, one claim at a
time, rather than just being told (by the pre-existing hard gates in
test_results_tab_unconfirmed_claims_gate.py) that something is blocked."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import get_application, upsert_application

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app_with_unconfirmed_claim(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Claims", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nLed a team of 8-10 engineers?\n\nEDUCATION\nBS",
        resume_ats_score=80, resume_ats_rationale="placeholder", resume_ats_next_actions=[],
        resume_unconfirmed_claims_ai_reported=[{"skill": "Team size", "text": "Led a team of 8-10 engineers?"}],
    )
    return AppTest.from_file(APP_PATH)


def _open_job(at):
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)


def test_panel_lists_the_flagged_claim_with_its_skill_label(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    _open_job(at)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "1 unconfirmed claim to resolve" in markdown_text
    assert "Led a team of 8-10 engineers?" in markdown_text
    assert "Team size" in markdown_text


def test_panel_absent_when_job_has_no_unconfirmed_claims(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Claims", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nLed a team of 12 engineers.\n\nEDUCATION\nBS",
        resume_ats_score=80, resume_ats_rationale="placeholder", resume_ats_next_actions=[],
    )
    at = AppTest.from_file(APP_PATH)
    _open_job(at)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "unconfirmed claim" not in markdown_text


def test_confirm_button_accepts_the_guess_as_true_and_strips_the_marker(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    _open_job(at)

    confirm_button = next(b for b in at.button if b.key and b.key.endswith("_confirm") and b.key.startswith("unconfirmedclaim_"))
    confirm_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert "Led a team of 8-10 engineers." in app_record["resume_text"] or "Led a team of 8-10 engineers" in app_record["resume_text"]
    assert "?" not in app_record["resume_text"]
    # Status must be preserved, not silently reset by the save.
    assert app_record["status"] == "under review"
    # The panel itself should be gone on the next render since the claim
    # list is now empty.
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "unconfirmed claim" not in markdown_text


def test_save_as_edited_replaces_the_line_with_the_typed_fact(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    _open_job(at)

    edit_box = next(t for t in at.text_input if t.key and t.key.endswith("_edittext"))
    edit_box.set_value("Led a team of 12 engineers.")
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key and b.key.endswith("_save") and b.key.startswith("unconfirmedclaim_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert "Led a team of 12 engineers." in app_record["resume_text"]
    assert "8-10" not in app_record["resume_text"]
    assert "?" not in app_record["resume_text"]


def test_save_as_edited_rejects_text_that_still_has_a_question_mark(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    _open_job(at)

    edit_box = next(t for t in at.text_input if t.key and t.key.endswith("_edittext"))
    edit_box.set_value("Maybe 12 engineers?")
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key and b.key.endswith("_save") and b.key.startswith("unconfirmedclaim_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    # Unchanged - still has the original unresolved claim.
    assert "8-10" in app_record["resume_text"]
    toasts = " ".join(t.value for t in at.toast)
    assert "?" in toasts  # the rejection toast mentions the marker


def test_save_as_edited_rejects_empty_text(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    _open_job(at)

    edit_box = next(t for t in at.text_input if t.key and t.key.endswith("_edittext"))
    edit_box.set_value("   ")
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key and b.key.endswith("_save") and b.key.startswith("unconfirmedclaim_"))
    save_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert "8-10" in app_record["resume_text"]


def test_resolving_the_claim_unblocks_marking_the_job_applied(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    _open_job(at)

    confirm_button = next(b for b in at.button if b.key and b.key.endswith("_confirm") and b.key.startswith("unconfirmedclaim_"))
    confirm_button.click().run(timeout=30)
    assert not at.exception

    reason_box = next(t for t in at.text_area if t.key and t.key.startswith("editreason_Dice_job1"))
    reason_box.set_value("Confirmed the real team size.")
    status_box = next(s for s in at.selectbox if s.key and s.key.startswith("status_Dice_job1"))
    status_box.set_value("applied")
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key == "save_status_Dice_job1")
    save_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["status"] == "applied"


def test_apply_answers_claim_resolves_via_confirm(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Claims", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        apply_answers=[{"label": "Desired Salary", "value": "Roughly $180K?"}],
    )
    at = AppTest.from_file(APP_PATH)
    _open_job(at)

    confirm_button = next(b for b in at.button if b.key and b.key.endswith("_confirm") and b.key.startswith("unconfirmedclaim_"))
    confirm_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    answers = app_record["apply_answers"]
    assert answers[0]["value"] == "Roughly $180K"
    assert "?" not in answers[0]["value"]
