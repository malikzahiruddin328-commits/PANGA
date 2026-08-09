"""Zahir's explicit, non-negotiable safety requirement (2026-08-09) for the
new hedged-"?"-claim feature: a hard, deterministic, code-level block on
marking a job "applied" or using the Apply Assist packet while ANY
unresolved "?" remains in the drafted documents - same "code-level
backstop, not just an instruction" bar as CLAUDE.md's known failure
pattern #3. These exercise the actual in-app wiring (AppTest), not just
the underlying tailoring.unconfirmed_claims functions in isolation."""

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


def test_marking_applied_is_blocked_while_an_unconfirmed_claim_remains(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    status_box = next(s for s in at.selectbox if s.key and s.key.startswith("status_Dice_job1"))
    status_box.set_value("applied")
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key == "save_status_Dice_job1")
    save_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["status"] == "under review"
    error_text = " ".join(e.value for e in at.error)
    assert "Led a team of 8-10 engineers?" in error_text


def test_marking_applied_succeeds_once_the_claim_is_gone(results_app_with_unconfirmed_claim):
    at = results_app_with_unconfirmed_claim
    # Simulate resolution: the text no longer has a "?" in it (as if
    # resolve_unconfirmed_claim() had already run and updated the field).
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nLed a team of 12 engineers.\n\nEDUCATION\nBS",
    )
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    # A freshly-set resume_text bumps documents_drafted_at, which trips
    # the SEPARATE, pre-existing needs_edit_review() gate too - satisfy
    # that one (unrelated to what this test is isolating) so only the
    # unconfirmed-claims gate's behavior is actually being verified.
    reason_box = next(t for t in at.text_area if t.key and t.key.startswith("editreason_Dice_job1"))
    reason_box.set_value("Rewrote the team-size bullet with the real number.")
    status_box = next(s for s in at.selectbox if s.key and s.key.startswith("status_Dice_job1"))
    status_box.set_value("applied")
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key == "save_status_Dice_job1")
    save_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["status"] == "applied"


def test_apply_assist_packet_is_blocked_while_an_unconfirmed_claim_remains(results_app_with_unconfirmed_claim):
    upsert_application(
        "Dice", "job1", status="under review",
        apply_answers=[{"label": "Desired Salary", "value": "Roughly $180K?"}],
    )
    at = results_app_with_unconfirmed_claim
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    error_text = " ".join(e.value for e in at.error)
    assert "can't be used yet" in error_text
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Roughly $180K?" in markdown_text
    # The raw packet value must not also be rendered as a usable code block
    # while blocked.
    assert not any("$180K" in c.value for c in at.code)


def test_apply_assist_packet_renders_normally_once_clean(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Claims", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        apply_answers=[{"label": "Desired Salary", "value": "$180,000"}],
    )
    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    assert any("$180,000" in c.value for c in at.code)
