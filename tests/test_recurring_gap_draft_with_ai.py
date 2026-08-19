"""UI wiring for the 2026-08-19 "Draft with AI" / "Draft all with AI"
addition to render_recurring_gap_review_panel (src/ui/app.py, Profile Gaps
tab) - real, permanent version of a one-off throwaway script that proved
most recurring gap questions can be answered directly and correctly from
Zahir's own already-documented profile by an LLM call.

Same streamlit.testing.v1.AppTest convention as
tests/test_accept_all_gap_questions.py (this codebase's established way of
testing app.py-adjacent UI logic - Streamlit rendering itself isn't
otherwise unit-testable here) - INCLUDING that file's own real pattern of
monkeypatching the CALLED function at its DEFINITION module (tailoring.
subscription_resume_qa.run_subscription_round there; tailoring.
gap_answer_drafting.draft_gap_answer/draft_gap_answers_concurrently here),
never at ui.app's own imported name. AppTest.from_file() re-executes
src/ui/app.py as a fresh script on every .run() call, so its own `from
tailoring.gap_answer_drafting import draft_gap_answer, ...` line re-runs
and re-binds to whatever the source module's attribute currently is at
that moment - patching ui.app's already-bound name (confirmed live while
writing these tests: it silently falls through to the REAL run_claude_cli
subprocess instead, several real ~30-60s subscription calls, no mocking
in effect at all) does NOT intercept anything, only patching the
definition module does."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs
from skills.canonical_taxonomy import save_taxonomy

APP_PATH = "src/ui/app.py"


def _job(job_id, required=None):
    return {
        "source": "linkedin", "job_id": job_id, "title": f"Role {job_id}", "organization": f"Org {job_id}",
        "ats_required_keywords": required if required is not None else [],
        "ats_preferred_keywords": [],
    }


@pytest.fixture
def gaps_app(isolated_data, monkeypatch):
    # Same requirement as test_accept_all_gap_questions.py's own fixture -
    # without this, ui/app.py's real license_gate would block the whole
    # page behind render_block_screen()/st.stop() before any of this
    # panel's own markdown/buttons ever render.
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": []}]})
    save_jobs(
        [_job(str(i), required=["SAP S/4HANA"]) for i in range(3)],
        apply_exclusion=False, review_required=False,
    )
    at = AppTest.from_file(APP_PATH)
    at.session_state["active_tab"] = "gaps"
    return at


def test_draft_with_ai_button_renders_per_question(gaps_app):
    at = gaps_app
    at.run(timeout=30)

    assert not at.exception
    assert any(b.key and b.key.endswith("_draftai") for b in at.button)
    # The plain manual-entry box is still there until a draft exists.
    assert any("SAP S/4HANA" in ta.label for ta in at.text_area)


def test_draft_all_button_renders_when_questions_exist(gaps_app):
    at = gaps_app
    at.run(timeout=30)

    assert not at.exception
    assert any(b.key == "recurring_gap_draftall" for b in at.button)


def test_clicking_draft_with_ai_shows_a_prefilled_but_editable_review_box_and_does_not_save(gaps_app, monkeypatch):
    import tailoring.gap_answer_drafting as gad_module

    def _fake_draft(question, profile):
        return {"can_answer": True, "answer": "Yes - real SAP S/4HANA implementation experience.", "error": None}

    monkeypatch.setattr(gad_module, "draft_gap_answer", _fake_draft)

    at = gaps_app
    at.run(timeout=30)
    draft_button = next(b for b in at.button if b.key and b.key.endswith("_draftai"))
    draft_button.click().run(timeout=30)

    assert not at.exception
    # The drafted answer is shown pre-filled...
    review_box = next(ta for ta in at.text_area if ta.key and "_ai_review_" in ta.key)
    assert review_box.value == "Yes - real SAP S/4HANA implementation experience."
    # ...but NOTHING saved yet - this is the core "never auto-save an AI
    # suggestion" requirement. A "Confirm & save" click is required.
    from profile.storage import load_profile

    assert not load_profile().get("gap_interview_answers")
    assert any(b.key and b.key.endswith("_ai_confirm") for b in at.button)
    assert any(b.key and b.key.endswith("_ai_discard") for b in at.button)


def test_confirming_a_draft_saves_via_the_real_recurring_gap_save_path(gaps_app, monkeypatch):
    import tailoring.gap_answer_drafting as gad_module

    monkeypatch.setattr(
        gad_module, "draft_gap_answer",
        lambda question, profile: {"can_answer": True, "answer": "Yes - 5 years, hands-on SAP S/4HANA.", "error": None},
    )

    at = gaps_app
    at.run(timeout=30)
    draft_button = next(b for b in at.button if b.key and b.key.endswith("_draftai"))
    draft_button.click().run(timeout=30)

    confirm_button = next(b for b in at.button if b.key and b.key.endswith("_ai_confirm"))
    confirm_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["skill"] == "SAP S/4HANA"
    assert "5 years" in answers[0]["answer"]
    # Role context marks this as coming from the recurring-gap review panel,
    # same as a manually-typed answer through this same panel would.
    from profile.interview import PROFILE_GAP_REVIEW_ROLE_CONTEXT

    assert answers[0]["role_context"] == PROFILE_GAP_REVIEW_ROLE_CONTEXT
    assert any("Saved" in t.value for t in at.toast)


def test_editing_the_drafted_answer_before_confirming_saves_the_edited_text(gaps_app, monkeypatch):
    import tailoring.gap_answer_drafting as gad_module

    monkeypatch.setattr(
        gad_module, "draft_gap_answer",
        lambda question, profile: {"can_answer": True, "answer": "Original AI draft.", "error": None},
    )

    at = gaps_app
    at.run(timeout=30)
    draft_button = next(b for b in at.button if b.key and b.key.endswith("_draftai"))
    draft_button.click().run(timeout=30)

    review_box = next(ta for ta in at.text_area if ta.key and "_ai_review_" in ta.key)
    review_box.set_value("Zahir's own edited version of the answer.")

    confirm_button = next(b for b in at.button if b.key and b.key.endswith("_ai_confirm"))
    confirm_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    answers = load_profile()["gap_interview_answers"]
    assert "Zahir's own edited version" in answers[0]["answer"]


def test_discarding_a_draft_saves_nothing(gaps_app, monkeypatch):
    # NOTE on scope: this only asserts the real, persisted effect of a
    # discard (nothing saved, the in-memory draft state cleared) - it
    # deliberately does NOT call at.run() again after the discard, or
    # assert on at.button/at.text_area post-discard. Found live writing
    # this test (2026-08-19): streamlit.testing.v1.AppTest has a real
    # limitation where a keyed widget (the review text_area) that stops
    # being rendered on one run reliably crashes the NEXT external
    # .run() call with a KeyError deep in Streamlit's own session-state
    # widget-id bookkeeping (venv/Lib/site-packages/streamlit/testing/v1/
    # element_tree.py's get_widget_states(), reproduced consistently
    # regardless of what that next run does). This is a harness artifact,
    # not a real app bug - confirmed by live-verifying this exact
    # draft -> discard -> manual-box-returns -> redraft -> confirm & save
    # round-trip by hand in a running instance of this worktree's app
    # (localhost:8506, PANGA_TEST_MODE=1): it works correctly, including
    # a second draft after a discard showing fresh text (not the stale
    # first draft), which is exactly what ai_draft_seq (ui/app.py) exists
    # to guarantee. The real, persisted assertions below are what AppTest
    # CAN reliably verify without hitting that harness limitation.
    import tailoring.gap_answer_drafting as gad_module

    monkeypatch.setattr(
        gad_module, "draft_gap_answer",
        lambda question, profile: {"can_answer": True, "answer": "Draft to be discarded.", "error": None},
    )

    at = gaps_app
    at.run(timeout=30)
    draft_button = next(b for b in at.button if b.key and b.key.endswith("_draftai"))
    draft_button.click().run(timeout=30)

    discard_button = next(b for b in at.button if b.key and b.key.endswith("_ai_discard"))
    discard_button.click().run(timeout=30)

    assert not at.exception
    from profile.storage import load_profile

    assert not load_profile().get("gap_interview_answers")
    assert at.session_state["recurring_gap_ai_drafts"] == {}


def test_ai_declining_shows_an_honest_note_not_a_fabricated_answer(gaps_app, monkeypatch):
    import tailoring.gap_answer_drafting as gad_module

    monkeypatch.setattr(
        gad_module, "draft_gap_answer",
        lambda question, profile: {"can_answer": False, "answer": "No SAP S/4HANA reference found anywhere in the profile.", "error": None},
    )

    at = gaps_app
    at.run(timeout=30)
    draft_button = next(b for b in at.button if b.key and b.key.endswith("_draftai"))
    draft_button.click().run(timeout=30)

    assert not at.exception
    review_box = next(ta for ta in at.text_area if ta.key and "_ai_review_" in ta.key)
    assert "No SAP S/4HANA reference found" in review_box.value
    # Still nothing saved, still requires an explicit confirm - a decline
    # is not silently dropped, it's shown for Zahir to review/edit/discard.
    from profile.storage import load_profile

    assert not load_profile().get("gap_interview_answers")


def test_draft_all_drafts_every_question_concurrently_and_saves_nothing_until_confirmed(gaps_app, monkeypatch):
    import tailoring.gap_answer_drafting as gad_module

    calls = []

    def _fake_batch(items, profile, max_workers=None):
        calls.append(list(items))
        return {key: {"can_answer": True, "answer": f"Answer for {q['skill']}.", "error": None} for key, q in items}

    monkeypatch.setattr(gad_module, "draft_gap_answers_concurrently", _fake_batch)

    at = gaps_app
    at.run(timeout=30)
    draftall_button = next(b for b in at.button if b.key == "recurring_gap_draftall")
    draftall_button.click().run(timeout=30)

    assert not at.exception
    # Called once, with every open question (not a sequential per-question
    # loop calling draft_gap_answer one at a time).
    assert len(calls) == 1
    assert len(calls[0]) == 1  # one distinct recurring gap in this fixture (SAP S/4HANA)
    review_boxes = [ta for ta in at.text_area if ta.key and "_ai_review_" in ta.key]
    assert len(review_boxes) == 1
    assert "Answer for SAP S/4HANA" in review_boxes[0].value
    from profile.storage import load_profile

    assert not load_profile().get("gap_interview_answers")
