"""Real risk found by Mirror's proactive sweep (2026-08-08), same
structural shape as the resume-regenerate bug that motivated the
score-first resume flow redesign: save_round() replaces a round's
interviewers/company_snapshot/likely_questions/questions_to_ask wholesale
on every call, and each call is a genuinely fresh web-search pass - not
guaranteed to find at least as much as the last one. The "Round" text
input defaults to "Round 1" every time the prep form opens, so reopening
"Prep for this interview" for an already-researched round and clicking
Generate without changing the label is one accidental click away from
silently replacing good research with a thinner result.

Not yet exercised by real data as of this fix (moderate exposure, not
urgent - hub's framing), but the same "don't silently overwrite what's
already there" principle that already protects outcome/outcome_notes
(never touched by regeneration) now extends to the rest via a warning
before overwriting, rather than trying to merge two independent research
passes the way gap answers merge into a resume score."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs
from tailoring.interview_prep import start_round, save_round

APP_PATH = "src/ui/app.py"


@pytest.fixture
def prep_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([
        {"source": "Indeed", "job_id": "p1", "title": "VP Engineering", "organization": "Acme Corp", "location": "Remote"},
    ])
    return AppTest.from_file(APP_PATH)


def _fake_generate_prep(job, profile, interviewers, on_progress=None):
    return {
        "interviewers": interviewers,
        "company_snapshot": "Acme Corp is a mid-size SaaS company.",
        "likely_questions": [{"question": "Why this role?", "why_theyll_ask": "Standard opener."}],
        "questions_to_ask": [{"question": "What does success look like in 90 days?"}],
    }


def test_generating_a_fresh_round_does_not_show_a_confirmation(prep_app, monkeypatch):
    # No existing round for this label - should generate immediately, no
    # extra click, matching the pre-existing behavior exactly.
    import tailoring.interview_prep as interview_prep

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)

    at.button(key="generate_prep_btn").click().run(timeout=30)

    assert not at.exception
    assert "prep_regen_confirm_pending" not in at.session_state
    round_expanders = [e for e in at.expander if e.label.startswith("Round 1")]
    assert len(round_expanders) == 1
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Acme Corp is a mid-size SaaS company" in markdown_text


def test_regenerating_an_already_ready_round_shows_a_confirmation_first(prep_app, monkeypatch):
    import tailoring.interview_prep as interview_prep

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)
    start_round("Indeed", "p1", "Round 1")
    save_round(
        "Indeed", "p1", "Round 1",
        interviewers=[], company_snapshot="Old snapshot text.",
        likely_questions=[{"question": "Old question?", "why_theyll_ask": "", "asked_by": "", "talking_point": ""}],
        questions_to_ask=[], status="ready",
    )

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)

    at.button(key="generate_prep_btn").click().run(timeout=30)

    assert not at.exception
    # Blocked behind the dialog - nothing regenerated yet.
    assert "prep_regen_confirm_pending" in at.session_state
    dialog_text = " ".join(m.value for m in at.markdown)
    assert "already has research" in dialog_text.lower()
    assert any(b.key == "prep_regen_confirm" for b in at.button)
    assert any(b.key == "prep_regen_cancel" for b in at.button)
    from tailoring.interview_prep import get_interview_prep

    record = get_interview_prep("Indeed", "p1")
    assert record["rounds"][0]["company_snapshot"] == "Old snapshot text."


def test_confirming_the_regen_dialog_replaces_the_content(prep_app, monkeypatch):
    import tailoring.interview_prep as interview_prep

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)
    start_round("Indeed", "p1", "Round 1")
    save_round(
        "Indeed", "p1", "Round 1",
        interviewers=[], company_snapshot="Old snapshot text.",
        likely_questions=[], questions_to_ask=[], status="ready",
    )

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)
    at.button(key="generate_prep_btn").click().run(timeout=30)

    confirm_button = next(b for b in at.button if b.key == "prep_regen_confirm")
    confirm_button.click().run(timeout=30)

    assert not at.exception
    assert "prep_regen_confirm_pending" not in at.session_state
    from tailoring.interview_prep import get_interview_prep

    record = get_interview_prep("Indeed", "p1")
    assert record["rounds"][0]["company_snapshot"] == "Acme Corp is a mid-size SaaS company."


def test_cancelling_the_regen_dialog_leaves_the_old_content_untouched(prep_app, monkeypatch):
    import tailoring.interview_prep as interview_prep

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)
    start_round("Indeed", "p1", "Round 1")
    save_round(
        "Indeed", "p1", "Round 1",
        interviewers=[], company_snapshot="Old snapshot text.",
        likely_questions=[], questions_to_ask=[], status="ready",
    )

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)
    at.button(key="generate_prep_btn").click().run(timeout=30)

    cancel_button = next(b for b in at.button if b.key == "prep_regen_cancel")
    cancel_button.click().run(timeout=30)

    assert not at.exception
    assert "prep_regen_confirm_pending" not in at.session_state
    from tailoring.interview_prep import get_interview_prep

    record = get_interview_prep("Indeed", "p1")
    assert record["rounds"][0]["company_snapshot"] == "Old snapshot text."


def test_a_still_in_progress_round_regenerates_without_confirmation(prep_app, monkeypatch):
    # start_round() alone (no save_round() yet) leaves status="in_progress" -
    # nothing real has been generated for this label yet, so re-clicking
    # Generate (e.g. after a failed first attempt) shouldn't need a
    # confirmation - there's no real prior research to lose.
    import tailoring.interview_prep as interview_prep

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)
    start_round("Indeed", "p1", "Round 1")

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)

    at.button(key="generate_prep_btn").click().run(timeout=30)

    assert not at.exception
    assert "prep_regen_confirm_pending" not in at.session_state
    from tailoring.interview_prep import get_interview_prep

    record = get_interview_prep("Indeed", "p1")
    assert record["rounds"][0]["company_snapshot"] == "Acme Corp is a mid-size SaaS company."
