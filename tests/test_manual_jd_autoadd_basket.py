"""Zahir's explicit ask (2026-08-19): entering a job description by hand -
whether pasting one in for an existing job, or adding a brand-new job from
scratch - is itself the decision to apply, so the job should land in the
basket automatically rather than needing a separate "Add to basket" click.
Covers all three places a JD can be manually entered: the proactive
pre-drafting paste box, the post-hoc paste box, and the "Add a job
manually" form."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    save_jobs([
        {"source": "Indeed", "job_id": "nojd1", "title": "Director, Never Drafted", "organization": "Acme Corp", "location": "Remote"},
        {"source": "Dice", "job_id": "withjd1", "title": "Director, With JD", "organization": "Beta Inc", "location": "Remote", "description": "Requirements: Python, SQL."},
    ])
    update_job_score("Indeed", "nojd1", 85, "Strong match.")
    update_job_score("Dice", "withjd1", 85, "Strong match.")
    return AppTest.from_file(APP_PATH)


def test_proactive_paste_jd_save_adds_to_basket(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    paste_box = next(t for t in at.text_area if t.key.startswith("jd_paste_pre_Indeed_nojd1"))
    paste_box.set_value("Requirements: Kubernetes.")
    save_button = next(b for b in at.button if b.key == "jd_paste_pre_save_Indeed_nojd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    from search.job_store import load_jobs
    job = next(j for j in load_jobs() if j["job_id"] == "nojd1")
    assert job.get("in_basket") is True


def test_post_hoc_paste_jd_save_adds_to_basket(results_app, monkeypatch):
    import tailoring.drafting as drafting

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
        return {"resume": {
            "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nKubernetes",
            "suggested_strategy_tag": "",
            "ats_score": 88,
            "ats_rationale": "Matched.",
            "ats_next_actions": [],
            "clarifying_questions": [],
        }}

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    from tailoring.applications import upsert_application
    upsert_application(
        "Indeed", "nojd1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava",
        resume_ats_score=45, resume_ats_rationale="No JD text available.", resume_ats_next_actions=[],
    )

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    paste_box = next(t for t in at.text_area if t.key == "jd_paste_Indeed_nojd1")
    paste_box.set_value("Requirements: Kubernetes.")
    save_button = next(b for b in at.button if b.key == "jd_paste_save_Indeed_nojd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    from search.job_store import load_jobs
    job = next(j for j in load_jobs() if j["job_id"] == "nojd1")
    assert job.get("in_basket") is True


def test_saving_empty_paste_does_not_add_to_basket(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key == "jd_paste_pre_save_Indeed_nojd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    from search.job_store import load_jobs
    job = next(j for j in load_jobs() if j["job_id"] == "nojd1")
    assert not job.get("in_basket")


def test_add_a_job_manually_adds_to_basket(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    reveal_button = next(b for b in at.button if b.label == "Add a job manually")
    reveal_button.click().run(timeout=30)

    at.text_input(key="manual_job_title").set_value("Head of IT")
    at.text_input(key="manual_job_org").set_value("Test Org")
    at.text_input(key="manual_job_url").set_value("https://example.com/job/123")
    at.text_area(key="manual_job_description").set_value("Requirements: Azure, Terraform.")
    save_button = next(b for b in at.button if b.label == "Save job")
    save_button.click().run(timeout=30)

    assert not at.exception
    from search.job_store import load_jobs
    job = next((j for j in load_jobs() if j.get("organization") == "Test Org"), None)
    assert job is not None
    assert job.get("in_basket") is True
