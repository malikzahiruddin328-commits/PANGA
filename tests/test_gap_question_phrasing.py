"""tailoring.gap_question_phrasing (2026-08-19) - real fix for Zahir's
standing "generic keyword prompt, not a real interviewer question"
complaint. See that module's own top docstring for the full investigation
and design. These tests mock reasoner_cli.run_claude_cli at the module
seam gap_question_phrasing actually calls (same pattern already used by
test_subscription_resume_qa.py / test_discuss_and_draft.py for the same
mechanism) - they verify: canned-question detection, prompt construction
(gap terms passed, full job/profile serialized), successful rephrase
replacing only canned entries, graceful fallback on a partial/malformed
reply and on a genuine reasoner failure, and that AI-authored questions are
never touched or sent to a second call at all."""

import json

import tailoring.gap_question_phrasing as gqp
from tailoring.drafting import _keyword_gap_question_text
from tailoring.reasoner_cli import ReasonerUnavailable

JOB = {
    "source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme",
    "description": "Looking for a leader with deep stakeholder management experience across global teams.",
}
PROFILE = {"target_title_framings": [], "skills": {"leadership": ["Team management"]}}

CANNED_REQUIRED_Q = {
    "type": "skill_gap",
    "skill": "Stakeholder management",
    "question": _keyword_gap_question_text("Stakeholder management", is_preferred=False),
    "suggested_answer": "No reference found...",
    "point_value": 5.0,
    "keyword_verified": True,
}
CANNED_PREFERRED_Q = {
    "type": "skill_gap",
    "skill": "GCP",
    "is_preferred": True,
    "question": _keyword_gap_question_text("GCP", is_preferred=True),
    "suggested_answer": "No reference found...",
    "point_value": 2.0,
    "keyword_verified": True,
}
AI_AUTHORED_Q = {
    "type": "skill_gap",
    "skill": "Budget ownership",
    "question": "You led a $40M IT budget at Acme - did that include direct P&L accountability, or was it opex only?",
    "suggested_answer": "",
    "point_value": 4.0,
    "keyword_verified": True,
}


# --- _is_canned_gap_question() ---


def test_is_canned_gap_question_true_for_template_text():
    assert gqp._is_canned_gap_question(CANNED_REQUIRED_Q) is True


def test_is_canned_gap_question_true_for_preferred_template_text():
    assert gqp._is_canned_gap_question(CANNED_PREFERRED_Q) is True


def test_is_canned_gap_question_false_for_ai_authored_text_even_with_point_value():
    # Backfilled point_value/keyword_verified alone must not be mistaken
    # for "this is the canned template" - only the literal question text
    # matching the template counts.
    assert gqp._is_canned_gap_question(AI_AUTHORED_Q) is False


def test_is_canned_gap_question_false_for_non_skill_gap_type():
    q = {"type": "disqualifier_check", "skill": "Something", "question": "Does this apply?"}
    assert gqp._is_canned_gap_question(q) is False


def test_is_canned_gap_question_false_with_no_skill():
    q = {"type": "skill_gap", "skill": "", "question": "whatever"}
    assert gqp._is_canned_gap_question(q) is False


# --- rephrase_canned_gap_questions_via_llm() ---


def test_no_canned_questions_makes_no_cli_call(monkeypatch):
    calls = []
    monkeypatch.setattr(gqp, "run_claude_cli", lambda *a, **k: calls.append(1) or "{}")
    result = gqp.rephrase_canned_gap_questions_via_llm([AI_AUTHORED_Q], JOB, PROFILE)
    assert result == [AI_AUTHORED_Q]
    assert calls == []


def test_rephrases_canned_question_and_leaves_ai_authored_untouched(monkeypatch):
    calls = []

    def _fake_run_claude_cli(prompt, timeout_seconds=None, on_start=None):
        calls.append(prompt)
        return json.dumps({
            "rephrased_questions": [
                {"skill": "Stakeholder management", "question": "This role coordinates across three regional VPs - can you walk through a time you aligned stakeholders with conflicting priorities like that?"},
            ]
        })

    monkeypatch.setattr(gqp, "run_claude_cli", _fake_run_claude_cli)
    result = gqp.rephrase_canned_gap_questions_via_llm(
        [CANNED_REQUIRED_Q, AI_AUTHORED_Q], JOB, PROFILE,
    )

    assert len(calls) == 1
    # The prompt genuinely carries the full job and profile content, not
    # just the bare skill label - the actual grounding this task requires.
    assert "stakeholder management experience across global teams" in calls[0]
    assert "Team management" in calls[0]
    assert "Stakeholder management" in calls[0]

    rephrased = next(q for q in result if q["skill"] == "Stakeholder management")
    assert rephrased["question"] == (
        "This role coordinates across three regional VPs - can you walk through a time you aligned "
        "stakeholders with conflicting priorities like that?"
    )
    assert rephrased["question_source"] == "llm_grounded"
    # Every other field carried through unchanged - the gap ITSELF (skill,
    # point_value, keyword_verified, suggested_answer) is never touched,
    # only the question text.
    assert rephrased["point_value"] == 5.0
    assert rephrased["keyword_verified"] is True
    assert rephrased["suggested_answer"] == CANNED_REQUIRED_Q["suggested_answer"]

    untouched = next(q for q in result if q["skill"] == "Budget ownership")
    assert untouched == AI_AUTHORED_Q


def test_batches_multiple_canned_questions_into_one_call(monkeypatch):
    calls = []

    def _fake_run_claude_cli(prompt, timeout_seconds=None, on_start=None):
        calls.append(prompt)
        return json.dumps({
            "rephrased_questions": [
                {"skill": "Stakeholder management", "question": "Grounded question 1"},
                {"skill": "GCP", "question": "Grounded question 2"},
            ]
        })

    monkeypatch.setattr(gqp, "run_claude_cli", _fake_run_claude_cli)
    result = gqp.rephrase_canned_gap_questions_via_llm(
        [CANNED_REQUIRED_Q, CANNED_PREFERRED_Q], JOB, PROFILE,
    )

    assert len(calls) == 1
    by_skill = {q["skill"]: q["question"] for q in result}
    assert by_skill["Stakeholder management"] == "Grounded question 1"
    assert by_skill["GCP"] == "Grounded question 2"


def test_partial_reply_keeps_canned_text_for_uncovered_skill(monkeypatch):
    monkeypatch.setattr(gqp, "run_claude_cli", lambda *a, **k: json.dumps({
        "rephrased_questions": [
            {"skill": "Stakeholder management", "question": "Grounded question"},
            # GCP genuinely omitted - simulates the model dropping one.
        ]
    }))
    result = gqp.rephrase_canned_gap_questions_via_llm(
        [CANNED_REQUIRED_Q, CANNED_PREFERRED_Q], JOB, PROFILE,
    )
    by_skill = {q["skill"]: q for q in result}
    assert by_skill["Stakeholder management"]["question"] == "Grounded question"
    assert by_skill["GCP"]["question"] == CANNED_PREFERRED_Q["question"]
    assert "question_source" not in by_skill["GCP"]


def test_blank_rephrased_question_falls_back_to_canned_text(monkeypatch):
    monkeypatch.setattr(gqp, "run_claude_cli", lambda *a, **k: json.dumps({
        "rephrased_questions": [{"skill": "Stakeholder management", "question": "   "}],
    }))
    result = gqp.rephrase_canned_gap_questions_via_llm([CANNED_REQUIRED_Q], JOB, PROFILE)
    assert result[0]["question"] == CANNED_REQUIRED_Q["question"]


def test_reasoner_unavailable_falls_back_to_original_questions_unchanged(monkeypatch):
    def _raise(*a, **k):
        raise ReasonerUnavailable("not logged in")
    monkeypatch.setattr(gqp, "run_claude_cli", _raise)
    result = gqp.rephrase_canned_gap_questions_via_llm([CANNED_REQUIRED_Q], JOB, PROFILE)
    assert result == [CANNED_REQUIRED_Q]


def test_runtime_error_falls_back_to_original_questions_unchanged(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("timed out")
    monkeypatch.setattr(gqp, "run_claude_cli", _raise)
    result = gqp.rephrase_canned_gap_questions_via_llm([CANNED_REQUIRED_Q], JOB, PROFILE)
    assert result == [CANNED_REQUIRED_Q]


def test_malformed_json_reply_falls_back_to_original_questions_unchanged(monkeypatch):
    monkeypatch.setattr(gqp, "run_claude_cli", lambda *a, **k: "not json at all")
    result = gqp.rephrase_canned_gap_questions_via_llm([CANNED_REQUIRED_Q], JOB, PROFILE)
    assert result == [CANNED_REQUIRED_Q]


def test_does_not_mutate_input_list_or_items(monkeypatch):
    monkeypatch.setattr(gqp, "run_claude_cli", lambda *a, **k: json.dumps({
        "rephrased_questions": [{"skill": "Stakeholder management", "question": "Grounded question"}],
    }))
    original = dict(CANNED_REQUIRED_Q)
    questions = [dict(CANNED_REQUIRED_Q)]
    gqp.rephrase_canned_gap_questions_via_llm(questions, JOB, PROFILE)
    assert questions[0] == original


def test_on_progress_fires_only_when_a_call_is_actually_made(monkeypatch):
    stages = []
    monkeypatch.setattr(gqp, "run_claude_cli", lambda *a, **k: json.dumps({"rephrased_questions": []}))

    gqp.rephrase_canned_gap_questions_via_llm([AI_AUTHORED_Q], JOB, PROFILE, on_progress=stages.append)
    assert stages == []

    gqp.rephrase_canned_gap_questions_via_llm([CANNED_REQUIRED_Q], JOB, PROFILE, on_progress=stages.append)
    assert stages == ["phrasing follow-up questions"]
