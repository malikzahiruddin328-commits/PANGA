"""Real HCI bug (Mirror's fine-needle audit, 2026-08-06): a freshly
generated prep round collapsed immediately - the expander's own
expanded=(status == "in_progress") went False the instant status flipped
to "ready", the same moment the content that was just generated became
available, so seeing it needed a scroll-down and a re-click. Fixed with
the same one-shot force-expand session_state pattern already used for the
Results tab's just-drafted-resume flag (from the inline-gap-questions
work) - these tests drive the real "Generate prep" button (via AppTest,
generate_prep mocked so no real API call happens) rather than just
inspecting the code."""

import pytest
from streamlit.testing.v1 import AppTest

from search.job_store import save_jobs

APP_PATH = "src/ui/app.py"


@pytest.fixture
def prep_app(isolated_data, monkeypatch):
    monkeypatch.setenv("PANGA_TEST_MODE", "1")
    save_jobs([
        {"source": "Indeed", "job_id": "p1", "title": "VP Engineering", "organization": "Acme Corp", "location": "Remote"},
        {"source": "Dice", "job_id": "p2", "title": "CTO", "organization": "Beta Inc", "location": "Remote"},
    ])
    return AppTest.from_file(APP_PATH)


def _fake_generate_prep(job, profile, interviewers, on_progress=None):
    return {
        "interviewers": interviewers,
        "company_snapshot": "Acme Corp is a mid-size SaaS company.",
        "likely_questions": [{"question": "Why this role?", "why_theyll_ask": "Standard opener."}],
        "questions_to_ask": [{"question": "What does success look like in 90 days?"}],
    }


def test_freshly_generated_round_is_force_expanded(prep_app, monkeypatch):
    import tailoring.interview_prep as interview_prep

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)

    generate_btn = next(b for b in at.button if b.key == "generate_prep_btn")
    generate_btn.click().run(timeout=30)

    assert not at.exception
    round_expanders = [e for e in at.expander if e.label.startswith("Round 1")]
    assert len(round_expanders) == 1
    assert round_expanders[0].proto.expanded


def test_force_expand_is_one_shot_not_sticky_on_a_later_unrelated_rerun(prep_app, monkeypatch):
    # The whole point of popping (not just reading) the flag - a later
    # rerun for something unrelated (e.g. switching tabs and back) must
    # not keep forcing this same round open forever.
    import tailoring.interview_prep as interview_prep

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)
    at.button(key="generate_prep_btn").click().run(timeout=30)

    # A second, unrelated rerun - nothing about the just-generated flag
    # should still be set.
    at.run(timeout=30)

    round_expanders = [e for e in at.expander if e.label.startswith("Round 1")]
    assert len(round_expanders) == 1
    assert not round_expanders[0].proto.expanded


def test_generating_one_jobs_prep_does_not_force_expand_another_jobs_round(prep_app, monkeypatch):
    import tailoring.interview_prep as interview_prep
    from tailoring.interview_prep import start_round

    monkeypatch.setattr(interview_prep, "generate_prep", _fake_generate_prep)
    # A second job with its own, already-existing (not just-generated)
    # round in progress.
    start_round("Dice", "p2", "Round 1")

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.session_state["prep_target"] = {
        "kind": "job", "source": "Indeed", "job_id": "p1", "job_label": "VP Engineering - Acme Corp",
    }
    at.run(timeout=30)
    at.button(key="generate_prep_btn").click().run(timeout=30)

    assert not at.exception
    subheaders = {s.value: s for s in at.subheader}
    assert "VP Engineering - Acme Corp" in subheaders
    assert "CTO - Beta Inc" in subheaders
    round_expanders = [e for e in at.expander if e.label.startswith("Round 1")]
    assert len(round_expanders) == 2
    by_status = {e.label: e.proto.expanded for e in round_expanders}
    # Indeed/p1's round: just generated, ready - force-expanded.
    assert by_status["Round 1 - ready"] is True
    # Dice/p2's round: untouched, still genuinely in_progress on its own
    # terms (not via the one-shot flag) - stays expanded for that reason,
    # not because it leaked the other job's force-expand flag.
    assert by_status["Round 1 - in progress"] is True


def test_round_expander_stays_open_while_recording_an_outcome(prep_app):
    # Real bug (Mirror's proactive sweep, 2026-08-08): selecting a
    # different outcome value (a real rerun-triggering interaction, before
    # ever clicking "Save outcome") used to collapse the round's expander
    # out from under the user mid-edit - the plain
    # expanded=(status == "in_progress" or just_generated) had no
    # persistence at all once status wasn't literally "in_progress" and
    # the one-shot flag was long gone.
    from tailoring.interview_prep import start_round

    start_round("Indeed", "p1", "Round 1")

    at = prep_app
    at.session_state["active_tab"] = "prep"
    at.run(timeout=30)

    round_expander = next(e for e in at.expander if e.label.startswith("Round 1"))
    assert round_expander.proto.expanded  # starts open - genuinely in_progress

    outcome_box = next(s for s in at.selectbox if s.key and s.key.startswith("outcome_Indeed_p1_"))
    outcome_box.set_value("went well")
    at.run(timeout=30)  # the rerun the selectbox change itself triggers

    assert not at.exception
    round_expander = next(e for e in at.expander if e.label.startswith("Round 1"))
    assert round_expander.proto.expanded