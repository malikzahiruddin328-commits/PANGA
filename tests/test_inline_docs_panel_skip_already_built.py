"""The Results tab's inline "Documents for this application" panel
(src/ui/app.py, the "Generate documents" button/checkbox section inside
each job's detail view) duplicates generate_documents()-calling/persist
logic OUTSIDE tailoring/bulk_generate.py's generate_for_job() entirely -
per bulk_generate.py's own module docstring, which calls this panel "risky
to disturb". It had the same theoretical gap generate_for_job() was fixed
for (2026-08-19, unmerged worktree fix/reuse-already-built-docs): no check
for whether a doc type was already built before redrafting it, so retrying
a partially-failed batch (e.g. resume succeeded, cover letter failed) used
to redraft the already-good, already-paid-for resume all over again.

Fixed here (2026-08-19) the same way bulk_generate.py's own
_already_built_doc_keys() was, but as an inline duplicate rather than a
restructure onto the shared path - the panel's own progress-bar/toast/
error-handling wiring stays exactly as it was, this only changes what gets
sent to generate_documents() and how the result is persisted. A new
_already_built_doc_keys() (a pure function, same field checks as
_job_already_built() which now shares its implementation) backs both.

Same streamlit.testing.v1.AppTest convention as
tests/test_status_preserved_on_regenerate.py (this codebase's established
way of driving the real "Generate documents" button) for the UI-level
assertions, plus direct unit tests of _already_built_doc_keys() itself."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import upsert_application, get_application

APP_PATH = "src/ui/app.py"

ALREADY_BUILT_RESUME_APP_RECORD = {
    "status": "under review",
    "resume_draft_source": "paid",
    "resume_text": "existing real resume text",
    "resume_ats_score": 82,
    "resume_ats_rationale": "existing rationale",
    "resume_ats_next_actions": [],
    "resume_clarifying_questions": [],
    "suggested_strategy_tag": "existing-tag",
    "resume_unconfirmed_claims_ai_reported": [],
}


# --- _already_built_doc_keys() pure-function unit tests ---


def test_already_built_doc_keys_returns_only_the_built_subset():
    import ui.app as app_module

    app_record = {
        "resume_draft_source": "paid", "resume_text": "a resume",
        "cover_letter_text": "",  # never actually built
    }
    assert app_module._already_built_doc_keys(app_record, ["resume", "cover_letter"]) == ["resume"]


def test_already_built_doc_keys_ignores_subscription_only_resume():
    import ui.app as app_module

    # subscription_resume_qa.py stamps resume_draft_source="subscription" for
    # a free Draft & score round - must never count as "built" for the paid
    # panel (it's not the paid final build this button drafts).
    app_record = {"resume_draft_source": "subscription", "resume_text": "free draft text"}
    assert app_module._already_built_doc_keys(app_record, ["resume"]) == []


def test_already_built_doc_keys_treats_unknown_doc_key_as_not_built():
    import ui.app as app_module

    assert app_module._already_built_doc_keys({}, ["some_future_doc_type"]) == []


def test_already_built_doc_keys_checks_apply_answers_as_a_non_empty_list():
    import ui.app as app_module

    assert app_module._already_built_doc_keys({"apply_answers": []}, ["apply_answers"]) == []
    assert app_module._already_built_doc_keys(
        {"apply_answers": [{"label": "x", "value": "y"}]}, ["apply_answers"]
    ) == ["apply_answers"]


def test_job_already_built_still_agrees_with_already_built_doc_keys():
    # _job_already_built() (the basket's own pre-existing "mass redo"
    # check) is now built directly on top of _already_built_doc_keys() so
    # the two can never drift apart - a quick end-to-end sanity check that
    # refactor didn't change its observable behavior.
    import ui.app as app_module

    app_record = {"resume_draft_source": "paid", "resume_text": "a resume", "cover_letter_text": "a cover letter"}
    assert app_module._job_already_built(app_record, ["resume", "cover_letter"]) is True
    assert app_module._job_already_built(app_record, ["resume", "exec_bio"]) is False


# --- Panel-level (AppTest) behavior ---


@pytest.fixture
def job_with_built_resume(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Applied", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application("Dice", "job1", **ALREADY_BUILT_RESUME_APP_RECORD)


def test_generate_documents_button_skips_already_built_resume_only_drafts_cover_letter(job_with_built_resume, monkeypatch):
    import tailoring.drafting as drafting

    seen_doc_keys = []

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None, existing_resume_text=None):
        seen_doc_keys.append(doc_keys)
        return {"cover_letter": "fresh cover letter text"}

    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    resume_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_resume_"))
    resume_checkbox.set_value(True)
    cover_letter_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_cover_letter_"))
    cover_letter_checkbox.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("gendocs_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    # generate_documents() was only ever asked for the missing doc type -
    # the already-built resume was never sent back through the paid path.
    assert seen_doc_keys == [["cover_letter"]]
    # The persisted (merged) result has real content for BOTH the reused
    # resume and the freshly drafted cover letter - nothing already-good
    # was dropped.
    app_record = get_application("Dice", "job1")
    assert app_record["resume_text"] == "existing real resume text"
    assert app_record["cover_letter_text"] == "fresh cover letter text"
    assert app_record["documents_requested"] == ["resume", "cover_letter"]


def test_generate_documents_button_everything_already_built_skips_the_call_entirely(job_with_built_resume, monkeypatch):
    import tailoring.drafting as drafting

    upsert_application("Dice", "job1", status="under review", cover_letter_text="already built cover letter")

    called = []

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None, existing_resume_text=None):
        called.append(doc_keys)
        return {}

    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    sync_calls = []
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: sync_calls.append(1))

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    resume_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_resume_"))
    resume_checkbox.set_value(True)
    cover_letter_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_cover_letter_"))
    cover_letter_checkbox.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("gendocs_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    assert called == []  # generate_documents() never called - no wasted paid call
    assert sync_calls == []  # nothing to sync either - no fresh drafts
    # Both doc types' already-good content survives untouched.
    app_record = get_application("Dice", "job1")
    assert app_record["resume_text"] == "existing real resume text"
    assert app_record["cover_letter_text"] == "already built cover letter"


def test_generate_documents_button_resume_reused_passes_existing_resume_text_through(job_with_built_resume, monkeypatch):
    # Cross-document consistency contract: when resume is reused (excluded
    # from what's actually sent to generate_documents()), the EXISTING
    # resume text must still be passed as existing_resume_text so
    # cover_letter can be checked for factual consistency against it - the
    # same contract generate_documents()'s own docstring establishes and
    # the panel's pre-fix code already respected for the "resume not in
    # selected" case.
    import tailoring.drafting as drafting

    captured = {}

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None, existing_resume_text=None):
        captured["doc_keys"] = doc_keys
        captured["existing_resume_text"] = existing_resume_text
        return {"cover_letter": "fresh cover letter text"}

    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    resume_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_resume_"))
    resume_checkbox.set_value(True)
    cover_letter_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_cover_letter_"))
    cover_letter_checkbox.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("gendocs_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    assert captured["doc_keys"] == ["cover_letter"]
    assert captured["existing_resume_text"] == "existing real resume text"


def test_generate_documents_button_normal_case_nothing_already_built_drafts_everything_unchanged(monkeypatch, isolated_data):
    # Regression guard: a job with NO prior output at all must still draft
    # every checked doc type, exactly as before this fix.
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Applied", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")

    import tailoring.drafting as drafting

    seen_doc_keys = []

    def _fake_generate_documents(job, profile, doc_keys, on_progress=None, existing_resume_text=None):
        seen_doc_keys.append(doc_keys)
        assert existing_resume_text is None  # resume IS in this batch
        return {
            "resume": {
                "text": "brand new resume", "ats_score": 90, "ats_rationale": "r",
                "ats_next_actions": [], "clarifying_questions": [], "suggested_strategy_tag": "tag",
            },
            "cover_letter": "brand new cover letter",
        }

    monkeypatch.setattr(drafting, "is_configured", lambda: True)
    monkeypatch.setattr(drafting, "generate_documents", _fake_generate_documents)
    monkeypatch.setattr("tailoring.dossier.sync_workspace_documents", lambda *a, **k: None)

    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "results"
    at.session_state["selected_idx_Dice"] = 0
    at.run(timeout=30)

    resume_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_resume_"))
    resume_checkbox.set_value(True)
    cover_letter_checkbox = next(c for c in at.checkbox if c.key and c.key.startswith("doc_cover_letter_"))
    cover_letter_checkbox.set_value(True)
    at.run(timeout=30)

    generate_button = next(b for b in at.button if b.key and b.key.startswith("gendocs_"))
    generate_button.click().run(timeout=30)

    assert not at.exception
    assert seen_doc_keys == [["resume", "cover_letter"]]  # full set drafted, nothing excluded
    app_record = get_application("Dice", "job1")
    assert app_record["resume_text"] == "brand new resume"
    assert app_record["cover_letter_text"] == "brand new cover letter"
    # This panel now stamps resume_draft_source="paid" itself when it
    # actually drafts a resume - the real functional gap this fix would
    # otherwise have: without this stamp, a resume drafted through THIS
    # panel could never be recognized as "already built" by
    # _already_built_doc_keys() on a later click, since that check
    # requires resume_draft_source == "paid" (previously only ever stamped
    # by the separate basket bulk-generate path).
    assert app_record["resume_draft_source"] == "paid"
