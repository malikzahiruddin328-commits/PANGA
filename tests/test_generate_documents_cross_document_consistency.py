"""Real gap Zahir hit live 2026-08-09: cover_letter/exec_bio/leadership_
summary each get their own independent drafting call, sharing only the raw
job+profile context - never the resume text drafted earlier in the SAME
batch, or already on file for a "redraft just this one doc" regenerate.
These exercise the actual "Generate documents" checkbox button in app.py
(not just the underlying tailoring.drafting functions in isolation), since
that's the real choke point where existing_resume_text needs to be threaded
through correctly based on whether "resume" is in this batch's selection."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application

APP_PATH = "src/ui/app.py"

_CAPTURED = {}


def _fake_generate_documents(job, profile, doc_keys, on_progress=None, existing_resume_text=None):
    _CAPTURED["doc_keys"] = doc_keys
    _CAPTURED["existing_resume_text"] = existing_resume_text
    result = {}
    if "resume" in doc_keys:
        result["resume"] = {
            "text": "PROFESSIONAL EXPERIENCE\nFreshly drafted resume.", "ats_score": 90,
            "ats_rationale": "", "ats_next_actions": [], "clarifying_questions": [],
            "unconfirmed_claims": [], "suggested_strategy_tag": "",
        }
    if "cover_letter" in doc_keys:
        result["cover_letter"] = "Dear Hiring Team, ..."
    return result


@pytest.fixture
def results_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    _CAPTURED.clear()
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Docs", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nAlready-stored resume text.",
    )
    return AppTest.from_file(APP_PATH)


def test_redrafting_only_the_cover_letter_passes_the_existing_resume_text(results_app, monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    cover_letter_box = next(c for c in at.checkbox if c.key == "doc_cover_letter_Dice_job1")
    cover_letter_box.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key == "gendocs_Dice_job1")
    generate_button.click().run(timeout=30)

    assert not at.exception
    assert _CAPTURED["doc_keys"] == ["cover_letter"]
    assert _CAPTURED["existing_resume_text"] == "PROFESSIONAL EXPERIENCE\nAlready-stored resume text."


def test_generating_resume_and_cover_letter_together_does_not_pass_existing_resume_text(results_app, monkeypatch):
    # "resume" being in this same batch means the freshly-drafted text is
    # used automatically (see generate_documents' own in-loop tracking) -
    # existing_resume_text is only for the resume-not-in-this-batch case.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    resume_box = next(c for c in at.checkbox if c.key == "doc_resume_Dice_job1")
    resume_box.set_value(True)
    cover_letter_box = next(c for c in at.checkbox if c.key == "doc_cover_letter_Dice_job1")
    cover_letter_box.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key == "gendocs_Dice_job1")
    generate_button.click().run(timeout=30)

    assert not at.exception
    assert _CAPTURED["doc_keys"] == ["resume", "cover_letter"]
    assert _CAPTURED["existing_resume_text"] is None
