"""The review/resolve UI for tailoring.unconfirmed_claims.find_unconfirmed_markers()
(render_unconfirmed_claims_section in ui/app.py) - the piece that actually
lets Zahir turn a hedged "?" guess into a confirmed fact, one claim at a
time, rather than just being told (by the pre-existing hard gates in
test_results_tab_unconfirmed_claims_gate.py) that something is blocked."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs, update_job_score
from tailoring.applications import get_application, upsert_application

APP_PATH = "src/ui/app.py"


@pytest.fixture(autouse=True)
def _no_real_source_crosscheck_reasoner_call(monkeypatch):
    """Real gap this file would otherwise hit: 2026-08-19's source-
    crosscheck pass (tailoring.claim_source_crosscheck) now runs the FIRST
    time render_unconfirmed_claims_section renders for a job, and calls the
    real `claude` CLI subprocess (reasoner_cli.run_claude_cli) unless
    mocked - unlike the paid-API path, _no_real_anthropic_api_calls in
    conftest.py does nothing to stop this one. Defaulting every test in
    this file to "nothing resolved" (same shape a genuine reasoner failure
    already fails soft to) keeps every existing test's behavior exactly
    what it was before this pass existed - a real claim it can't verify is
    still left for the manual panel below, unchanged. Tests that want to
    exercise the new auto-resolve behavior itself override this with their
    own monkeypatch."""
    import tailoring.claim_source_crosscheck as csc
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({"resolved": []}))


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


def test_dollar_amount_in_a_claim_renders_escaped_not_as_latex(isolated_data, monkeypatch):
    # Real bug caught live 2026-08-09: st.markdown auto-interprets $...$ as
    # inline KaTeX, so a claim like "($300K-500K annual savings)" rendered
    # with the dollar sign gone and the hyphen turned into a minus sign.
    # Display-only corruption - the stored text itself was always correct -
    # but the panel must escape "$" before handing the line to st.markdown.
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([{
        "source": "Dice", "job_id": "job1", "title": "Director, Claims", "organization": "Acme Corp",
        "location": "Remote", "description": "Requirements: Python.",
    }])
    update_job_score("Dice", "job1", 85, "Strong match.")
    upsert_application(
        "Dice", "job1", status="under review",
        resume_text=(
            "PROFESSIONAL EXPERIENCE\n"
            "Drove agent-based first-line security operations automation "
            "($300K-500K annual savings)?\n\nEDUCATION\nBS"
        ),
        resume_ats_score=80, resume_ats_rationale="placeholder", resume_ats_next_actions=[],
        resume_unconfirmed_claims_ai_reported=[{
            "skill": "Cost savings",
            "text": "Drove agent-based first-line security operations automation ($300K-500K annual savings)?",
        }],
    )
    at = AppTest.from_file(APP_PATH)
    _open_job(at)

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "\\$300K-500K" in markdown_text
    # The un-escaped form must not appear bare in any markdown element -
    # that's exactly what triggers Streamlit's KaTeX auto-interpretation.
    assert "> Drove agent-based first-line security operations automation ($300K-500K" not in markdown_text


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


def test_auto_crosscheck_resolves_a_claim_already_in_source_documents(results_app_with_unconfirmed_claim, monkeypatch):
    # Real bug Zahir hit live (2026-08-19): asked to re-confirm facts already
    # stated in his own ingested documents. The panel must now silently
    # resolve anything the crosscheck can verify, with zero question asked.
    import tailoring.claim_source_crosscheck as csc
    monkeypatch.setattr("profile.ingest.all_documents_text", lambda: "Real resume: led a team of 8-10 engineers.")
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({
        "resolved": [{"index": 0, "resolved_line": "Led a team of 8-10 engineers."}],
    }))

    at = results_app_with_unconfirmed_claim
    _open_job(at)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert "Led a team of 8-10 engineers." in app_record["resume_text"]
    assert "?" not in app_record["resume_text"]
    # The panel itself is gone - nothing left to ask.
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "unconfirmed claim" not in markdown_text


def test_auto_crosscheck_leaves_genuinely_unverifiable_claims_for_the_manual_panel(results_app_with_unconfirmed_claim, monkeypatch):
    import tailoring.claim_source_crosscheck as csc
    monkeypatch.setattr("profile.ingest.all_documents_text", lambda: "Real resume has no team-size figure at all.")
    monkeypatch.setattr(csc, "run_claude_cli", lambda *a, **k: json.dumps({"resolved": []}))

    at = results_app_with_unconfirmed_claim
    _open_job(at)

    assert not at.exception
    app_record = get_application("Dice", "job1")
    assert "8-10" in app_record["resume_text"]
    assert "?" in app_record["resume_text"]
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "1 unconfirmed claim to resolve" in markdown_text


def test_auto_crosscheck_only_calls_the_reasoner_once_per_job(results_app_with_unconfirmed_claim, monkeypatch):
    # No per-render reasoner call (CLAUDE.md's own rule) - session-state
    # gated to run exactly once per job, even across multiple reruns.
    import tailoring.claim_source_crosscheck as csc
    calls = []

    def _fake_run(prompt, timeout_seconds=None, on_start=None):
        calls.append(1)
        return json.dumps({"resolved": []})

    monkeypatch.setattr("profile.ingest.all_documents_text", lambda: "")
    monkeypatch.setattr(csc, "run_claude_cli", _fake_run)

    at = results_app_with_unconfirmed_claim
    _open_job(at)
    assert len(calls) == 1

    # A later, unrelated rerun on the same job must not fire it again.
    at.run(timeout=30)
    assert len(calls) == 1
