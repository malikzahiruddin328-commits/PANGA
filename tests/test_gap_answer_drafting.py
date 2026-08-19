"""tailoring.gap_answer_drafting (2026-08-19, Phase 4 of the "final set of
questions" taxonomy-gap build) - drafts a candidate answer to a recurring
Profile Gaps question directly from the candidate's own already-documented
profile, via the same $0 subscription `claude` CLI mechanism (reasoner_cli.
run_claude_cli) every other in-app AI call already uses, never the paid
Anthropic API.

No real subprocess/network call anywhere in this file - run_claude_cli is
always monkeypatched, same convention as tests/test_subscription_resume_qa.py
(the module-level imported name, not reasoner_cli.run_claude_cli itself, is
what needs patching - gap_answer_drafting does `from tailoring.reasoner_cli
import ... run_claude_cli`, so the patch target is
tailoring.gap_answer_drafting.run_claude_cli)."""

import json

import pytest

import tailoring.gap_answer_drafting as gad
from tailoring.gap_answer_drafting import (
    build_gap_answer_prompt,
    draft_gap_answer,
    draft_gap_answers_concurrently,
)
from tailoring.reasoner_cli import ReasonerUnavailable

QUESTION = {
    "skill": "Databricks",
    "question": "Do you have real, genuine experience with \"Databricks\"? It shows up as a required or "
                "preferred qualification in 3 of your saved job postings so far.",
    "type": "confirmed_gap",
}
PROFILE = {
    "work_history": [
        {"employer": "Acme Corp", "title": "Data Engineer", "start": "2019", "end": "2022",
         "responsibilities": "Built and maintained Databricks lakehouse pipelines for the analytics team."},
    ],
    "certifications": ["AWS Certified Solutions Architect"],
}


# --- build_gap_answer_prompt: prompt construction (no AI call at all). ---

def test_prompt_embeds_the_real_profile_as_json():
    prompt = build_gap_answer_prompt(QUESTION, PROFILE)

    assert "CANDIDATE'S MASTER PROFILE:" in prompt
    assert json.dumps(PROFILE, indent=2, default=str) in prompt


def test_prompt_embeds_the_exact_question_and_skill():
    prompt = build_gap_answer_prompt(QUESTION, PROFILE)

    assert QUESTION["question"] in prompt
    assert "Databricks" in prompt


def test_prompt_instructs_zero_fabrication_and_explicit_decline():
    prompt = build_gap_answer_prompt(QUESTION, PROFILE)

    # The core "never fabricate, decline honestly" contract this whole
    # feature exists to enforce - locked here so a future prompt edit that
    # accidentally drops this instruction fails a test, not just gets
    # noticed on a live fabricated answer.
    assert "ONLY" in prompt
    assert "fabricat" in prompt.lower() or "invent" in prompt.lower()
    assert "can_answer" in prompt


def test_prompt_requests_a_single_object_never_an_array():
    # Deliberate: avoids the known parse_json_reply() top-level-array bug
    # (being fixed in parallel on fix/parse-json-reply-arrays, not relied
    # on here) by always asking for one object-wrapped reply per question.
    prompt = build_gap_answer_prompt(QUESTION, PROFILE)

    assert '{"can_answer"' in prompt
    assert "bare JSON object" in prompt


# --- draft_gap_answer: single-question call construction + reply handling. ---

def test_draft_gap_answer_returns_the_real_answer_when_the_profile_supports_it(monkeypatch):
    monkeypatch.setattr(gad, "run_claude_cli", lambda prompt, timeout_seconds=None, on_start=None: json.dumps({
        "can_answer": True,
        "answer": "Yes - built Databricks lakehouse pipelines at Acme Corp, 2019-2022.",
    }))

    result = draft_gap_answer(QUESTION, PROFILE)

    assert result == {
        "can_answer": True,
        "answer": "Yes - built Databricks lakehouse pipelines at Acme Corp, 2019-2022.",
        "error": None,
    }


def test_draft_gap_answer_passes_the_real_prompt_through_to_run_claude_cli(monkeypatch):
    captured = {}

    def _fake(prompt, timeout_seconds=None, on_start=None):
        captured["prompt"] = prompt
        return json.dumps({"can_answer": True, "answer": "x"})

    monkeypatch.setattr(gad, "run_claude_cli", _fake)

    draft_gap_answer(QUESTION, PROFILE)

    assert captured["prompt"] == build_gap_answer_prompt(QUESTION, PROFILE)


def test_draft_gap_answer_declines_honestly_when_profile_has_no_support(monkeypatch):
    monkeypatch.setattr(gad, "run_claude_cli", lambda prompt, timeout_seconds=None, on_start=None: json.dumps({
        "can_answer": False,
        "answer": "No certification or work history entry mentions Kubernetes anywhere in the profile.",
    }))

    result = draft_gap_answer({"skill": "Kubernetes", "question": "Do you have Kubernetes experience?"}, PROFILE)

    assert result["can_answer"] is False
    assert "Kubernetes" in result["answer"]
    assert result["error"] is None


def test_draft_gap_answer_handles_an_unparsable_reply_without_raising(monkeypatch):
    monkeypatch.setattr(gad, "run_claude_cli", lambda prompt, timeout_seconds=None, on_start=None: "not json at all")

    result = draft_gap_answer(QUESTION, PROFILE)

    assert result["can_answer"] is False
    assert result["answer"] == ""
    assert result["error"] is not None


def test_draft_gap_answer_strips_a_json_fence(monkeypatch):
    # parse_json_reply() already handles this (a live reasoning reply isn't
    # guaranteed to comply exactly) - this locks that this module actually
    # goes through parse_json_reply() rather than a bare json.loads().
    monkeypatch.setattr(
        gad, "run_claude_cli",
        lambda prompt, timeout_seconds=None, on_start=None: "```json\n" + json.dumps({"can_answer": True, "answer": "Yes."}) + "\n```",
    )

    result = draft_gap_answer(QUESTION, PROFILE)

    assert result == {"can_answer": True, "answer": "Yes.", "error": None}


def test_draft_gap_answer_propagates_reasoner_unavailable(monkeypatch):
    def _boom(prompt, timeout_seconds=None, on_start=None):
        raise ReasonerUnavailable("not installed")

    monkeypatch.setattr(gad, "run_claude_cli", _boom)

    with pytest.raises(ReasonerUnavailable):
        draft_gap_answer(QUESTION, PROFILE)


# --- draft_gap_answers_concurrently: the batch/"Draft all" path. ---

def test_draft_all_returns_one_result_per_item_keyed_correctly(monkeypatch):
    monkeypatch.setattr(gad, "run_claude_cli", lambda prompt, timeout_seconds=None, on_start=None: json.dumps({
        "can_answer": True, "answer": "Yes, real experience.",
    }))
    items = [
        ("key_a", {"skill": "Databricks", "question": "Databricks experience?"}),
        ("key_b", {"skill": "Snowflake", "question": "Snowflake experience?"}),
    ]

    results = draft_gap_answers_concurrently(items, PROFILE)

    assert set(results.keys()) == {"key_a", "key_b"}
    assert all(r["can_answer"] for r in results.values())


def test_draft_all_empty_items_returns_empty_dict_without_calling_the_reasoner(monkeypatch):
    called = []
    monkeypatch.setattr(gad, "run_claude_cli", lambda *a, **k: called.append(1) or "{}")

    results = draft_gap_answers_concurrently([], PROFILE)

    assert results == {}
    assert called == []


def test_draft_all_one_items_failure_does_not_take_down_the_rest(monkeypatch):
    # Matches ui/app.py's own _draftall_worker's documented "one item's
    # failure shouldn't stop the rest" contract for the exact same shape
    # of batch AI-call work.
    def _flaky(prompt, timeout_seconds=None, on_start=None):
        if "Broken" in prompt:
            raise RuntimeError("simulated reasoner failure")
        return json.dumps({"can_answer": True, "answer": "Fine."})

    monkeypatch.setattr(gad, "run_claude_cli", _flaky)
    items = [
        ("ok_key", {"skill": "Fine", "question": "Fine experience?"}),
        ("broken_key", {"skill": "Broken", "question": "Broken experience?"}),
    ]

    results = draft_gap_answers_concurrently(items, PROFILE)

    assert results["ok_key"]["can_answer"] is True
    assert results["broken_key"]["error"] is not None
    assert results["broken_key"]["can_answer"] is False


def test_draft_all_reasoner_unavailable_for_one_item_is_captured_not_raised(monkeypatch):
    def _fake(prompt, timeout_seconds=None, on_start=None):
        raise ReasonerUnavailable("not installed")

    monkeypatch.setattr(gad, "run_claude_cli", _fake)
    items = [("key_a", QUESTION)]

    results = draft_gap_answers_concurrently(items, PROFILE)

    assert results["key_a"]["error"] == "not installed"
    assert results["key_a"]["can_answer"] is False


def test_draft_all_never_exceeds_the_worker_ceiling(monkeypatch):
    captured_target_max = {}

    def _fake_effective_worker_count(target_max, *args, **kwargs):
        captured_target_max["value"] = target_max
        return target_max

    monkeypatch.setattr(gad, "effective_worker_count", _fake_effective_worker_count)
    monkeypatch.setattr(gad, "run_claude_cli", lambda prompt, timeout_seconds=None, on_start=None: json.dumps({
        "can_answer": True, "answer": "x",
    }))
    # More items than the default ceiling (6) - target_max must be capped
    # at the ceiling, not the raw item count.
    items = [(f"key_{i}", {"skill": f"s{i}", "question": f"q{i}?"}) for i in range(10)]

    draft_gap_answers_concurrently(items, PROFILE)

    assert captured_target_max["value"] == gad.GAP_ANSWER_DRAFTALL_MAX_WORKERS
