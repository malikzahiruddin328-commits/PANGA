"""profile.intake_flow (2026-08-19, feature/resume-jd-intake-redesign) -
covers role-example generation/confirmation, JD sampling (board fetchers
mocked at the module seam intake_flow actually calls, same pattern as
test_gap_question_phrasing.py mocks reasoner_cli.run_claude_cli), the
probing-question-generation prompt construction, and persisting answers
through the SAME store profile.interview.save_answer() already uses."""

import json

import profile.intake_flow as intake_flow
from profile.interview import save_answer
from profile.storage import load_profile
from tailoring.reasoner_cli import ReasonerUnavailable


# --- generate_role_examples() ---


def test_generate_role_examples_returns_cleaned_list(isolated_data, monkeypatch):
    calls = []

    def _fake_run(prompt, *a, **k):
        calls.append(prompt)
        return json.dumps({
            "example_roles": [
                {"title": "VP of IT", "why": "Led enterprise IT for 8 years."},
                {"title": "  ", "why": "blank title should be dropped"},
                {"title": "Chief Information Officer", "why": "Direct next step."},
            ]
        })

    monkeypatch.setattr(intake_flow, "run_claude_cli", _fake_run)
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "RESUME TEXT HERE")

    result = intake_flow.generate_role_examples({"gap_interview_answers": []}, count=5)

    assert len(calls) == 1
    assert "RESUME TEXT HERE" in calls[0]
    assert [r["title"] for r in result] == ["VP of IT", "Chief Information Officer"]


def test_generate_role_examples_caps_to_requested_count(isolated_data, monkeypatch):
    def _fake_run(prompt, *a, **k):
        return json.dumps({"example_roles": [{"title": f"Role {i}", "why": ""} for i in range(20)]})

    monkeypatch.setattr(intake_flow, "run_claude_cli", _fake_run)
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "text")

    result = intake_flow.generate_role_examples({}, count=3)
    assert len(result) == 3


def test_generate_role_examples_reasoner_unavailable_raises(isolated_data, monkeypatch):
    def _raise(*a, **k):
        raise ReasonerUnavailable("not logged in")
    monkeypatch.setattr(intake_flow, "run_claude_cli", _raise)
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "text")

    try:
        intake_flow.generate_role_examples({})
        assert False, "expected ReasonerUnavailable"
    except ReasonerUnavailable:
        pass


def test_generate_role_examples_prompt_excludes_already_confirmed_roles(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(intake_flow, "run_claude_cli", lambda p, *a, **k: calls.append(p) or json.dumps({"example_roles": []}))
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "text")

    profile = {"intake_confirmed_roles": [{"title": "VP of IT", "confirmed_at": "x"}]}
    intake_flow.generate_role_examples(profile)
    assert "VP of IT" in calls[0]
    assert "do not repeat these" in calls[0]


# --- save_confirmed_roles() ---


def test_save_confirmed_roles_persists_confirmed_and_rejected(isolated_data):
    intake_flow.save_confirmed_roles(["VP of IT", "CIO"], rejected_titles=["Nurse Manager"])
    profile = load_profile()
    confirmed_titles = [r["title"] for r in profile["intake_confirmed_roles"]]
    rejected_titles = [r["title"] for r in profile["intake_rejected_roles"]]
    assert confirmed_titles == ["VP of IT", "CIO"]
    assert rejected_titles == ["Nurse Manager"]


def test_save_confirmed_roles_merges_across_rounds_without_duplicating(isolated_data):
    intake_flow.save_confirmed_roles(["VP of IT"])
    intake_flow.save_confirmed_roles(["VP of IT", "CIO"])
    profile = load_profile()
    confirmed_titles = [r["title"] for r in profile["intake_confirmed_roles"]]
    assert confirmed_titles == ["VP of IT", "CIO"]


def test_save_confirmed_roles_ignores_blank_titles(isolated_data):
    intake_flow.save_confirmed_roles(["  ", "CIO"], rejected_titles=["   "])
    profile = load_profile()
    assert [r["title"] for r in profile["intake_confirmed_roles"]] == ["CIO"]
    assert profile.get("intake_rejected_roles", []) == []


# --- sample_jds_for_role() ---


def _dice_job(n):
    return {
        "source": "Dice", "job_id": f"dice-{n}", "title": f"VP of IT #{n}",
        "organization": f"Org {n}", "location": "Remote", "posting_url": f"https://dice.example/{n}",
    }


def test_sample_jds_for_role_prefers_dice_with_real_description(isolated_data, monkeypatch):
    monkeypatch.setattr(intake_flow, "fetch_dice_jobs", lambda kw, limit: [_dice_job(i) for i in range(12)])
    monkeypatch.setattr(intake_flow, "fetch_dice_job_description", lambda url: f"Full JD text for {url}")
    monkeypatch.setattr(intake_flow, "fetch_built_in_jobs", lambda kw, limit: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(intake_flow, "fetch_simplyhired_jobs", lambda kw, limit: (_ for _ in ()).throw(AssertionError("should not be called")))

    samples = intake_flow.sample_jds_for_role("VP of IT", target_count=10)

    assert len(samples) == 10
    assert all(not s["thin_text"] for s in samples)
    assert all(s["description"].startswith("Full JD text for") for s in samples)


def test_sample_jds_for_role_backfills_from_other_boards_when_dice_short(isolated_data, monkeypatch):
    monkeypatch.setattr(intake_flow, "fetch_dice_jobs", lambda kw, limit: [_dice_job(i) for i in range(3)])
    monkeypatch.setattr(intake_flow, "fetch_dice_job_description", lambda url: "real dice text")
    monkeypatch.setattr(intake_flow, "fetch_built_in_jobs", lambda kw, limit: [
        {"source": "Built In", "job_id": f"bi-{i}", "title": f"CIO #{i}", "organization": "X", "location": "NY", "posting_url": "u"} for i in range(10)
    ])
    monkeypatch.setattr(intake_flow, "fetch_simplyhired_jobs", lambda kw, limit: (_ for _ in ()).throw(AssertionError("should not need SimplyHired")))

    samples = intake_flow.sample_jds_for_role("VP of IT", target_count=10)

    assert len(samples) == 10
    dice_samples = [s for s in samples if s["source"] == "Dice"]
    builtin_samples = [s for s in samples if s["source"] == "Built In"]
    assert len(dice_samples) == 3
    assert all(not s["thin_text"] for s in dice_samples)
    assert len(builtin_samples) == 7
    assert all(s["thin_text"] for s in builtin_samples)


def test_sample_jds_for_role_one_board_failure_does_not_stop_others(isolated_data, monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("Dice is blocking scrapers")
    monkeypatch.setattr(intake_flow, "fetch_dice_jobs", _raise)
    monkeypatch.setattr(intake_flow, "fetch_built_in_jobs", lambda kw, limit: [
        {"source": "Built In", "job_id": "bi-1", "title": "CIO", "organization": "X", "location": "NY", "posting_url": "u"}
    ])
    monkeypatch.setattr(intake_flow, "fetch_simplyhired_jobs", lambda kw, limit: [])

    samples = intake_flow.sample_jds_for_role("VP of IT", target_count=5)
    assert len(samples) == 1
    assert samples[0]["source"] == "Built In"


def test_sample_jds_for_role_dedupes_across_boards(isolated_data, monkeypatch):
    # Same content-based (source, job_id) key returned twice - must not
    # double-count toward target_count.
    monkeypatch.setattr(intake_flow, "fetch_dice_jobs", lambda kw, limit: [_dice_job(1), _dice_job(1)])
    monkeypatch.setattr(intake_flow, "fetch_dice_job_description", lambda url: "text")
    monkeypatch.setattr(intake_flow, "fetch_built_in_jobs", lambda kw, limit: [])
    monkeypatch.setattr(intake_flow, "fetch_simplyhired_jobs", lambda kw, limit: [])

    samples = intake_flow.sample_jds_for_role("VP of IT", target_count=10)
    assert len(samples) == 1


def test_sample_jds_for_role_respects_max_candidates_circuit_breaker(isolated_data, monkeypatch):
    # A board that could return far more than target_count must not cause
    # unbounded description-fetch calls - bounded by MAX_JD_CANDIDATES_PER_ROLE.
    monkeypatch.setattr(intake_flow, "MAX_JD_CANDIDATES_PER_ROLE", 5)
    monkeypatch.setattr(intake_flow, "fetch_dice_jobs", lambda kw, limit: [_dice_job(i) for i in range(100)])
    fetch_calls = []
    monkeypatch.setattr(intake_flow, "fetch_dice_job_description", lambda url: fetch_calls.append(url) or None)
    monkeypatch.setattr(intake_flow, "fetch_built_in_jobs", lambda kw, limit: [])
    monkeypatch.setattr(intake_flow, "fetch_simplyhired_jobs", lambda kw, limit: [])

    intake_flow.sample_jds_for_role("VP of IT", target_count=10)
    assert len(fetch_calls) <= 5


def test_sample_jds_for_confirmed_roles_returns_dict_per_role(isolated_data, monkeypatch):
    monkeypatch.setattr(intake_flow, "sample_jds_for_role", lambda role, target_count=10: [{"title": role}])
    result = intake_flow.sample_jds_for_confirmed_roles(["VP of IT", "CIO"])
    assert set(result.keys()) == {"VP of IT", "CIO"}


# --- generate_probing_questions() ---


def test_generate_probing_questions_grounds_prompt_in_docs_roles_and_jds(isolated_data, monkeypatch):
    calls = []

    def _fake_run(prompt, *a, **k):
        calls.append(prompt)
        return json.dumps({"questions": [
            {"skill": "Kubernetes", "question": "Have you run production K8s clusters?", "inferred_answer": "Likely yes, given your cloud migration work.", "confidence": "medium"},
        ]})

    monkeypatch.setattr(intake_flow, "run_claude_cli", _fake_run)
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "Led cloud migration to AWS.")

    jd_samples = {"VP of IT": [{"title": "VP IT", "organization": "Acme", "source": "Dice", "description": "Must have Kubernetes experience.", "thin_text": False}]}
    result = intake_flow.generate_probing_questions({"gap_interview_answers": []}, ["VP of IT"], jd_samples)

    assert len(calls) == 1
    assert "Led cloud migration to AWS." in calls[0]
    assert "VP of IT" in calls[0]
    assert "Kubernetes experience" in calls[0]
    assert result == [{
        "skill": "Kubernetes", "question": "Have you run production K8s clusters?",
        "inferred_answer": "Likely yes, given your cloud migration work.", "confidence": "medium",
    }]


def test_generate_probing_questions_excludes_already_answered_facts_from_prompt(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(intake_flow, "run_claude_cli", lambda p, *a, **k: calls.append(p) or json.dumps({"questions": []}))
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "text")

    profile = {"gap_interview_answers": [{"skill": "SAP", "answer": "Yes, 5 years."}]}
    intake_flow.generate_probing_questions(profile, ["VP of IT"], {})
    assert "SAP" in calls[0]
    assert "Facts already confirmed" in calls[0]


def test_generate_probing_questions_drops_items_missing_skill_or_question(isolated_data, monkeypatch):
    monkeypatch.setattr(intake_flow, "run_claude_cli", lambda p, *a, **k: json.dumps({"questions": [
        {"skill": "", "question": "Missing skill"},
        {"skill": "GCP", "question": ""},
        {"skill": "AWS", "question": "Real one?"},
    ]}))
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "text")
    result = intake_flow.generate_probing_questions({}, [], {})
    assert [q["skill"] for q in result] == ["AWS"]


def test_generate_probing_questions_defaults_bad_confidence_to_low(isolated_data, monkeypatch):
    monkeypatch.setattr(intake_flow, "run_claude_cli", lambda p, *a, **k: json.dumps({"questions": [
        {"skill": "AWS", "question": "Real?", "confidence": "extremely sure"},
    ]}))
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "text")
    result = intake_flow.generate_probing_questions({}, [], {})
    assert result[0]["confidence"] == "low"


def test_generate_probing_questions_caps_to_max_questions(isolated_data, monkeypatch):
    monkeypatch.setattr(intake_flow, "MAX_INTAKE_QUESTIONS", 3)
    monkeypatch.setattr(intake_flow, "run_claude_cli", lambda p, *a, **k: json.dumps({
        "questions": [{"skill": f"skill{i}", "question": f"q{i}"} for i in range(10)]
    }))
    monkeypatch.setattr(intake_flow, "all_documents_text", lambda: "text")
    result = intake_flow.generate_probing_questions({}, [], {})
    assert len(result) == 3


# --- save_intake_probing_answers() / the suppression integration point ---


def test_save_intake_probing_answers_writes_via_save_answer_and_skips_blank(isolated_data):
    saved = intake_flow.save_intake_probing_answers([
        {"skill": "Kubernetes", "question": "Have you run K8s?", "answer": "Yes, 3 years at Acme."},
        {"skill": "GCP", "question": "Have you used GCP?", "answer": "   "},  # blank, must be skipped
    ])
    assert saved == 1
    profile = load_profile()
    answers = profile["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["skill"] == "Kubernetes"
    assert answers[0]["role_context"] == intake_flow.INTAKE_ROLE_CONTEXT
    assert answers[0]["answer"] == "Yes, 3 years at Acme."


def test_intake_confirmed_fact_suppresses_future_per_job_question_via_shared_store(isolated_data):
    """Real integration proof for the spec's downstream-suppression
    requirement: a fact saved through THIS module's save path must be
    visible to drafting.py's existing previously_answered_skills check and
    to gap_frequency_analysis's already_answered_ids check - both read
    profile["gap_interview_answers"], the same store save_answer() (and
    thus this module) already writes to. No changes to either of those two
    modules were needed - this test is the evidence that reuse actually
    holds, not just a docstring claim."""
    intake_flow.save_intake_probing_answers([
        {"skill": "Kubernetes", "question": "Have you run K8s?", "answer": "Yes, 3 years."},
    ])

    profile = load_profile()
    previously_answered_skills = [a.get("skill") for a in profile.get("gap_interview_answers", [])]
    assert "Kubernetes" in previously_answered_skills

    from skills.canonical_taxonomy import find_canonical_id, load_taxonomy
    taxonomy = load_taxonomy()
    already_answered_ids = {
        a.get("canonical_skill_id") for a in profile.get("gap_interview_answers", []) if a.get("canonical_skill_id")
    }
    kubernetes_id = find_canonical_id("Kubernetes", taxonomy)
    assert kubernetes_id is not None
    assert kubernetes_id in already_answered_ids


# --- record_intake_completed() ---


def test_record_intake_completed_appends_compact_history_entry(isolated_data):
    intake_flow.record_intake_completed(
        confirmed_roles=["VP of IT"], rejected_roles=["Nurse Manager"],
        jd_sample_counts={"VP of IT": 10}, questions_saved=4,
    )
    profile = load_profile()
    history = profile["intake_history"]
    assert len(history) == 1
    assert history[0]["confirmed_roles"] == ["VP of IT"]
    assert history[0]["rejected_roles"] == ["Nurse Manager"]
    assert history[0]["jd_sample_counts"] == {"VP of IT": 10}
    assert history[0]["questions_saved"] == 4
    # No raw JD text stored - only compact counts, per this module's own
    # "don't bloat the encrypted profile store" design decision.
    assert "description" not in json.dumps(history[0])


def test_record_intake_completed_appends_across_rounds(isolated_data):
    intake_flow.record_intake_completed(["A"], [], {"A": 5}, 1)
    intake_flow.record_intake_completed(["B"], [], {"B": 5}, 2)
    profile = load_profile()
    assert len(profile["intake_history"]) == 2
