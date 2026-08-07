"""Real bug found live 2026-08-06 (Zahir): jobs from sources with no
captured JD text (ZipRecruiter, Indeed, industry boards) were being scored
and drafted as if genuinely tailored against the posting, with no
indication anything was missing. Zahir's explicit product direction: the
fix is an active paste-the-JD-yourself prompt framed as Panga respecting
sites' anti-bot protections (not an apology for a "missing" feature), not
just a passive warning. These tests drive the actual Results tab (via
Streamlit's own AppTest, not a browser - more reliable here for nested
expanders) to confirm the prompt renders exactly when there's no real
posting text, and never when there is.
"""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application

APP_PATH = "src/ui/app.py"


@pytest.fixture
def results_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")

    save_jobs([
        {"source": "Indeed", "job_id": "nojd1", "title": "Director, No JD", "organization": "Acme Corp", "location": "Remote"},
        {"source": "Dice", "job_id": "withjd1", "title": "Director, With JD", "organization": "Beta Inc", "location": "Remote", "description": "Requirements: Python, SQL."},
        {"source": "Indeed", "job_id": "nojd2", "title": "Director, Never Drafted", "organization": "Gamma LLC", "location": "Remote"},
        {"source": "Dice", "job_id": "withjd2", "title": "Director, JD Never Drafted", "organization": "Delta Co", "location": "Remote", "description": "Requirements: Go, Rust."},
    ])
    update_job_score("Indeed", "nojd1", 85, "Strong match.")
    update_job_score("Dice", "withjd1", 85, "Strong match.")
    update_job_score("Indeed", "nojd2", 85, "Strong match.")
    update_job_score("Dice", "withjd2", 85, "Strong match.")
    upsert_application(
        "Indeed", "nojd1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava",
        resume_ats_score=45, resume_ats_rationale="No JD text available.", resume_ats_next_actions=[],
    )
    upsert_application(
        "Dice", "withjd1", status="under review",
        resume_text="PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava, Python, SQL",
        resume_ats_score=90, resume_ats_rationale="Matched 2/2 keywords.", resume_ats_next_actions=[],
    )
    return AppTest.from_file(APP_PATH)


def test_paste_jd_prompt_shown_when_posting_has_no_captured_text(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    assert not at.exception
    infos = [i.value for i in at.info]
    assert any("Panga respects that" in i for i in infos)
    assert any("bypass" in i for i in infos)
    # Framed as a deliberate design choice, never as something broken/missing.
    assert not any("unavailable" in i.lower() or "missing" in i.lower() for i in infos)

    paste_boxes = [t for t in at.text_area if t.key == "jd_paste_Indeed_nojd1"]
    assert len(paste_boxes) == 1
    save_buttons = [b for b in at.button if b.key == "jd_paste_save_Indeed_nojd1"]
    assert len(save_buttons) == 1


def test_no_paste_jd_prompt_shown_when_posting_has_real_jd_text(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    assert len(at.info) == 0
    assert not any(t.key == "jd_paste_Dice_withjd1" for t in at.text_area)


def test_clicking_save_without_pasting_shows_a_toast_and_does_not_crash(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key == "jd_paste_save_Indeed_nojd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    # Still no description saved - the empty-paste guard did its job.
    from search.job_store import load_jobs
    job = next(j for j in load_jobs() if j["job_id"] == "nojd1")
    assert not job.get("description")


def test_pasting_jd_and_saving_stores_description_and_rescoring(results_app, monkeypatch):
    import tailoring.drafting as drafting

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
        assert job["description"] == "Requirements: Kubernetes, Terraform."
        return {"resume": {
            "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nKubernetes, Terraform",
            "suggested_strategy_tag": "",
            "ats_score": 88,
            "ats_rationale": "Matched 2/2 keywords.",
            "ats_next_actions": [],
            "clarifying_questions": [],
        }}

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    paste_box = next(t for t in at.text_area if t.key == "jd_paste_Indeed_nojd1")
    paste_box.set_value("Requirements: Kubernetes, Terraform.")
    save_button = next(b for b in at.button if b.key == "jd_paste_save_Indeed_nojd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    from search.job_store import load_jobs
    from tailoring.applications import get_application
    job = next(j for j in load_jobs() if j["job_id"] == "nojd1")
    assert job["description"] == "Requirements: Kubernetes, Terraform."
    app_record = get_application("Indeed", "nojd1")
    assert app_record["resume_ats_score"] == 88


def test_proactive_prompt_shown_before_any_document_has_ever_been_drafted(results_app):
    # Zahir's follow-up ask 2026-08-06: the paste option must be available
    # BEFORE the first draft, not only reactively after a resume's already
    # been drafted blind - job "nojd2" has no application/draft at all yet.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 1
    at.run(timeout=30)

    assert not at.exception
    assert any(t.key == "jd_paste_pre_Indeed_nojd2" for t in at.text_area)
    assert any(b.key == "jd_paste_pre_save_Indeed_nojd2" for b in at.button)
    # Nothing's been drafted yet, so the post-hoc (already-drafted) variant
    # genuinely has nothing to attach to.
    assert not any(t.key == "jd_paste_Indeed_nojd2" for t in at.text_area)


def test_proactive_prompt_hidden_once_a_resume_is_already_drafted(results_app):
    # Real duplicate-UI bug Zahir hit live 2026-08-06: once a resume's
    # already drafted, the proactive job-row prompt and the post-hoc
    # score-card prompt showed the exact same content twice on the same
    # screen. The proactive box's whole purpose (prompt/view before
    # anything's drafted) is moot once a draft exists - "nojd1" already has
    # a blind-drafted resume (from the fixture), so only the post-hoc
    # variant should render now.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 0
    at.run(timeout=30)

    assert not at.exception
    assert not any(t.key == "jd_paste_pre_Indeed_nojd1" for t in at.text_area)
    assert any(t.key == "jd_paste_Indeed_nojd1" for t in at.text_area)


def test_proactive_save_persists_description_without_drafting_anything(results_app, monkeypatch):
    import tailoring.drafting as drafting

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("generate_documents must not be called by the proactive (pre-drafting) save path")

    monkeypatch.setattr(drafting, "generate_documents", _fail_if_called)

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Indeed"] = 1
    at.run(timeout=30)

    paste_box = next(t for t in at.text_area if t.key == "jd_paste_pre_Indeed_nojd2")
    paste_box.set_value("Requirements: Python, AWS.")
    save_button = next(b for b in at.button if b.key == "jd_paste_pre_save_Indeed_nojd2")
    save_button.click().run(timeout=30)

    assert not at.exception
    from search.job_store import load_jobs
    job = next(j for j in load_jobs() if j["job_id"] == "nojd2")
    assert job["description"] == "Requirements: Python, AWS."


def test_jd_column_shown_in_results_table(results_app):
    # Zahir's ask 2026-08-06: a column he can scan across the whole table
    # at a glance, instead of opening each job to find out.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.run(timeout=30)

    assert not at.exception
    rows = {}
    for d in at.dataframe:
        df = d.value
        assert "JD" in df.columns
        rows.update(dict(zip(df["Role"], df["JD"])))

    assert rows["Director, No JD"] == "–"
    assert rows["Director, Never Drafted"] == "–"
    assert rows["Director, With JD"] == "✓"


def test_view_update_box_shown_with_existing_text_prefilled_when_jd_present(results_app):
    # Retrieval ask 2026-08-06: the paste box was write-only - Zahir wants
    # to see what's already stored, not just overwrite it blind.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    assert not at.exception
    view_box = next(t for t in at.text_area if t.key.startswith("jd_view_Dice_withjd1_"))
    assert view_box.value == "Requirements: Python, SQL."
    assert any(b.key == "jd_view_save_Dice_withjd1" for b in at.button)
    # No fresh-empty-paste-box framing once real text already exists.
    assert not any(t.key == "jd_paste_Dice_withjd1" for t in at.text_area)
    # "withjd1" already has a drafted resume - the proactive (job-row,
    # before-drafting) variant must NOT also render here, or it's the same
    # duplicate-box bug Zahir hit live.
    assert not any(t.key.startswith("jd_view_pre_Dice_withjd1_") for t in at.text_area)


def test_proactive_view_box_shown_when_jd_present_but_nothing_drafted_yet(results_app):
    # The combination that still needs the proactive variant: a job with
    # captured JD text (auto-captured, e.g. via Dice/Workday/SmartRecruiters)
    # but no draft yet at all - "withjd2" in the fixture.
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 1
    at.run(timeout=30)

    assert not at.exception
    pre_view_box = next(t for t in at.text_area if t.key.startswith("jd_view_pre_Dice_withjd2_"))
    assert pre_view_box.value == "Requirements: Go, Rust."
    # Nothing's drafted yet, so the post-hoc variant has nothing to attach to.
    assert not any(t.key.startswith("jd_view_Dice_withjd2_") for t in at.text_area)


def test_view_update_box_is_collapsed_by_default(results_app):
    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    expanders = [e for e in at.expander if e.label == "View / update job description"]
    assert len(expanders) >= 1
    assert all(not e.proto.expanded for e in expanders)


def test_clicking_update_with_unchanged_text_shows_info_toast_and_does_not_regenerate(results_app, monkeypatch):
    import tailoring.drafting as drafting

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("generate_documents must not be called when nothing changed")

    monkeypatch.setattr(drafting, "generate_documents", _fail_if_called)

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    save_button = next(b for b in at.button if b.key == "jd_view_save_Dice_withjd1")
    save_button.click().run(timeout=30)

    assert not at.exception


def test_updating_jd_text_persists_and_rescores(results_app, monkeypatch):
    import tailoring.drafting as drafting

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None):
        assert job["description"] == "Requirements: Kubernetes, updated."
        return {"resume": {
            "text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nKubernetes",
            "suggested_strategy_tag": "",
            "ats_score": 77,
            "ats_rationale": "Matched 1/1 keywords.",
            "ats_next_actions": [],
            "clarifying_questions": [],
        }}

    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = results_app
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    view_box = next(t for t in at.text_area if t.key.startswith("jd_view_Dice_withjd1_"))
    view_box.set_value("Requirements: Kubernetes, updated.")
    save_button = next(b for b in at.button if b.key == "jd_view_save_Dice_withjd1")
    save_button.click().run(timeout=30)

    assert not at.exception
    from search.job_store import load_jobs
    from tailoring.applications import get_application
    job = next(j for j in load_jobs() if j["job_id"] == "withjd1")
    assert job["description"] == "Requirements: Kubernetes, updated."
    app_record = get_application("Dice", "withjd1")
    assert app_record["resume_ats_score"] == 77
