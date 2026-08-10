"""Real bug found by the state-handling audit (2026-08-10): both
regenerate_resume_and_persist() and the "Generate documents" button
hardcoded status="under review" unconditionally - regenerating a resume or
re-drafting documents for a job already marked "applied" (or any later
stage) silently reverted its status with zero warning, not even mentioned
in the success toast. Fixed to preserve the job's current status, matching
every other write path in app.py (e.g. _persist_resolved_claim)."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application, get_application

APP_PATH = "src/ui/app.py"


def _fake_generate_documents(job, profile, doc_keys, on_progress=None, existing_resume_text=None):
    return {"resume": {
        "text": "PROFESSIONAL EXPERIENCE\nRegenerated.\n\nSKILLS\nPython",
        "suggested_strategy_tag": "", "ats_score": 88,
        "ats_rationale": "Matched keywords.", "ats_next_actions": [], "clarifying_questions": [],
    }}


@pytest.fixture
def applied_job(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Applied", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="applied",
        resume_text="PROFESSIONAL EXPERIENCE\nOriginal draft.\n\nSKILLS\nPython",
        resume_ats_score=80, resume_ats_rationale="placeholder", resume_ats_next_actions=[],
    )


def test_regenerate_resume_button_preserves_applied_status(applied_job, monkeypatch):
    # Drives the real "Generate resume" button in Analyze Fit, which for a
    # job that already has a resume_text goes through the regenerate-
    # confirmation gate (check_regenerate_impact) before calling
    # regenerate_resume_and_persist() - the exact function this bug was
    # in. Monkeypatched to report genuinely new info so Generate proceeds
    # immediately (the same non-blocking path the existing gap-questions
    # test suite already uses), rather than exercising the confirm dialog
    # itself, which is a separate concern.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr(drafting, "check_regenerate_impact", lambda job, app_record, profile: {
        "has_new_info": True, "new_fact_count": 1, "current_score": 80,
        "estimated_new_score": 88, "cost_estimate": 0.05, "last_generation_cost": None,
    })
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.session_state["results_show_applied"] = True
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("analyzefit_generate_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert app_record["status"] == "applied"
    assert app_record["resume_text"] == "PROFESSIONAL EXPERIENCE\nRegenerated.\n\nSKILLS\nPython"


def test_generate_documents_button_preserves_applied_status(applied_job, monkeypatch):
    import tailoring.drafting as drafting

    # The "Generate documents" button checks drafting_is_configured()
    # itself (a separate, explicit gate in app.py, not inside
    # generate_documents()) before ever calling generate_documents() - the
    # autouse _no_real_anthropic_api_calls fixture unsets ANTHROPIC_API_KEY
    # for every test, so without this the button silently takes the
    # "not configured" branch (covered separately below) instead of the
    # real-draft branch this test means to exercise.
    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.session_state["results_show_applied"] = True
    at.run(timeout=30)

    resume_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_resume_"))
    resume_checkbox.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("gendocs_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert app_record["status"] == "applied"
    assert app_record["resume_text"] == "PROFESSIONAL EXPERIENCE\nRegenerated.\n\nSKILLS\nPython"


def test_generate_documents_without_api_key_preserves_applied_status(applied_job, monkeypatch):
    monkeypatch.setattr("tailoring.drafting.is_configured", lambda: False)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.session_state["results_show_applied"] = True
    at.run(timeout=30)

    resume_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_resume_"))
    resume_checkbox.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("gendocs_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    assert get_application("Dice", "job1")["status"] == "applied"
