"""tailoring.subscription_resume_qa (2026-08-13, feature/in-app-
subscription-qa) - Zahir's confirmed real design for in-app basket
document generation, replacing the message-board "Discuss & draft" shape.
Everything this module reuses (tailoring.reasoner_cli, drafting.
_finalize_resume_draft, discuss_and_draft._generate_questions_via_
subscription, drafting.save_gap_answers, applications.try_acquire_
generation_lock) is covered by its own tests elsewhere - these tests cover
the orchestration this module adds: the resume prompt's keyword-extraction
fold-in, the lock/persist/round-record sequence, and each stage's real
failure/lock-collision handling."""

import json

import tailoring.subscription_resume_qa as sqa
from tailoring.reasoner_cli import ReasonerUnavailable


JOB = {"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}
JOB_WITH_KEYWORDS = {
    "source": "linkedin", "job_id": "2", "title": "VP IT", "organization": "Acme",
    "ats_required_keywords": ["Budget ownership"], "ats_preferred_keywords": [],
}
PROFILE = {"target_title_framings": []}


# --- _resume_prompt() ---


def test_resume_prompt_requests_keywords_when_job_has_none():
    prompt = sqa._resume_prompt(JOB, PROFILE)
    assert "ats_required_keywords" in prompt
    assert "ats_preferred_keywords" in prompt


def test_resume_prompt_omits_keyword_request_when_job_already_has_them():
    # The job dict itself still gets serialized whole into "JOB POSTING:"
    # (so it legitimately contains the literal field names as data) - what
    # must be absent is the SCHEMA instruction asking the reasoner to
    # produce them again.
    prompt = sqa._resume_prompt(JOB_WITH_KEYWORDS, PROFILE)
    assert '- "ats_required_keywords":' not in prompt
    assert '- "ats_preferred_keywords":' not in prompt


# --- draft_resume_via_subscription() ---


def test_draft_resume_via_subscription_calls_cli_and_finalizes(monkeypatch):
    calls = []

    def _fake_run_claude_cli(prompt, timeout_seconds=None, on_start=None):
        calls.append(prompt)
        return json.dumps({
            "text": "resume text", "target_seniority_at_least_vp": True,
            "suggested_strategy_tag": "tag", "clarifying_questions": [], "unconfirmed_claims": [],
            "ats_required_keywords": ["Budget ownership"], "ats_preferred_keywords": [],
        })
    monkeypatch.setattr(sqa, "run_claude_cli", _fake_run_claude_cli)
    monkeypatch.setattr(sqa, "update_job_ats_keywords", lambda *a, **k: None)
    finalize_calls = []
    monkeypatch.setattr(sqa, "_finalize_resume_draft", lambda data, job, profile: finalize_calls.append((data, job, profile)) or {"text": "resume text", "ats_score": 70})

    job = dict(JOB)
    stages = []
    result = sqa.draft_resume_via_subscription(job, PROFILE, on_progress=stages.append)

    assert len(calls) == 1
    assert "VP IT" in calls[0]
    assert result == {"text": "resume text", "ats_score": 70}
    assert stages == ["drafting resume", "scoring"]
    # The extracted keywords land on the job dict itself (mirrors
    # feature/resume-reasoner-path's own reasoner_pipeline behavior) so a
    # later call for this same job doesn't need to re-extract them.
    assert job["ats_required_keywords"] == ["Budget ownership"]
    assert job["ats_preferred_keywords"] == []
    assert len(finalize_calls) == 1


def test_draft_resume_via_subscription_does_not_overwrite_existing_keywords(monkeypatch):
    monkeypatch.setattr(sqa, "run_claude_cli", lambda prompt, timeout_seconds=None, on_start=None: json.dumps({
        "text": "resume text", "target_seniority_at_least_vp": True,
        "suggested_strategy_tag": "tag", "clarifying_questions": [], "unconfirmed_claims": [],
    }))
    monkeypatch.setattr(sqa, "_finalize_resume_draft", lambda data, job, profile: {"text": "resume text", "ats_score": 70})
    update_calls = []
    monkeypatch.setattr(sqa, "update_job_ats_keywords", lambda *a, **k: update_calls.append(a))

    job = dict(JOB_WITH_KEYWORDS)
    sqa.draft_resume_via_subscription(job, PROFILE)

    assert update_calls == []  # already had keywords cached - never re-extracted or re-persisted


def test_draft_resume_via_subscription_propagates_reasoner_unavailable(monkeypatch):
    def _boom(prompt, timeout_seconds=None, on_start=None):
        raise ReasonerUnavailable("claude CLI not on PATH")
    monkeypatch.setattr(sqa, "run_claude_cli", _boom)

    try:
        sqa.draft_resume_via_subscription(dict(JOB), PROFILE)
        assert False, "expected ReasonerUnavailable"
    except ReasonerUnavailable:
        pass


# --- run_subscription_round() ---


def _patch_round_persist(monkeypatch, round_number=1, prior_app_record=None):
    monkeypatch.setattr(sqa, "get_application", lambda source, job_id: dict(prior_app_record or {}))
    upserts = []
    monkeypatch.setattr(sqa, "upsert_application", lambda *a, **k: upserts.append((a, k)))
    monkeypatch.setattr(sqa, "sync_workspace_documents", lambda *a, **k: None)
    round_calls = []
    monkeypatch.setattr(
        sqa, "record_subscription_qa_round",
        lambda source, job_id, ats_score, **k: round_calls.append(k) or round_number,
    )
    statuses = []
    monkeypatch.setattr(sqa, "set_qa_status", lambda *a, **k: statuses.append((a, k)))
    return upserts, statuses, round_calls


def test_run_subscription_round_returns_locked_when_lock_unavailable(monkeypatch):
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: False)
    released = []
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert result == {"ok": False, "locked": True, "round": None, "resume_draft": None, "error": None}
    assert released == []  # never acquired, so never released


def test_run_subscription_round_success_persists_records_round_and_releases_lock(monkeypatch):
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    released = []
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))
    resume_draft = {
        "text": "resume text", "ats_score": 82, "ats_rationale": "r", "ats_next_actions": [],
        "clarifying_questions": [{"skill": "Budget", "question": "q", "suggested_answer": ""}],
        "suggested_strategy_tag": "tag", "unconfirmed_claims": [],
    }
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", lambda job, profile, on_progress=None, on_pid=None, already_asked_questions=None: resume_draft)
    upserts, statuses, round_calls = _patch_round_persist(monkeypatch, round_number=1)

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert result["ok"] is True and result["locked"] is False and result["round"] == 1 and result["error"] is None
    assert result["resume_draft"]["qa_loop_state"] == "in_progress"  # 82 < 90 target, round 1 < 3 cap
    assert released == [("linkedin", "1")]
    assert len(upserts) == 1
    upsert_kwargs = upserts[0][1]
    assert upsert_kwargs["resume_text"] == "resume text"
    assert upsert_kwargs["resume_draft_source"] == "subscription"
    assert upsert_kwargs["resume_ats_score"] == 82
    assert upsert_kwargs["resume_clarifying_questions"] == resume_draft["clarifying_questions"]
    # Round-record call carries the real loop state + the newly-surfaced
    # question text, atomically with the round bump.
    assert round_calls[0]["loop_state"] == "in_progress"
    assert round_calls[0]["newly_asked_question_texts"] == ["q"]
    # Stamped "drafting" before the call, then cleared (None) on success.
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["drafting", None]


def test_run_subscription_round_rephrases_canned_gap_questions_when_in_progress(monkeypatch):
    # Real fix for Zahir's standing "generic keyword prompt" complaint
    # (2026-08-19, see tailoring.gap_question_phrasing's own docstring):
    # the ranked/capped in-progress question set must be run through the
    # rephrase pass before it's persisted/surfaced. Mocked at the module
    # seam run_subscription_round() actually calls - gap_question_
    # phrasing's own internal reasoner-call behavior is covered by its own
    # test suite, this only verifies the wiring/ordering.
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: None)
    original_questions = [{"skill": "Budget", "question": "canned q", "suggested_answer": ""}]
    resume_draft = {
        "text": "resume text", "ats_score": 60, "ats_rationale": "r", "ats_next_actions": [],
        "clarifying_questions": list(original_questions),
        "suggested_strategy_tag": "tag", "unconfirmed_claims": [],
    }
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", lambda job, profile, on_progress=None, on_pid=None, already_asked_questions=None: resume_draft)
    upserts, statuses, round_calls = _patch_round_persist(monkeypatch, round_number=1)

    rephrase_calls = []

    def _fake_rephrase(questions, job, profile, on_progress=None):
        rephrase_calls.append((questions, job, profile))
        return [{**questions[0], "question": "grounded q", "question_source": "llm_grounded"}]

    monkeypatch.setattr(sqa, "rephrase_canned_gap_questions_via_llm", _fake_rephrase)

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert len(rephrase_calls) == 1
    # Called with the already-ranked-and-capped list, not the raw one.
    assert rephrase_calls[0][0] == original_questions
    assert result["resume_draft"]["clarifying_questions"][0]["question"] == "grounded q"
    assert upserts[0][1]["resume_clarifying_questions"][0]["question"] == "grounded q"
    assert round_calls[0]["newly_asked_question_texts"] == ["grounded q"]


def test_run_subscription_round_skips_rephrase_when_no_more_questions_needed(monkeypatch):
    # "ready"/"plateaued" rounds already force questions_to_surface to []
    # before this pass would run - must not call the rephraser on an empty
    # list either (it would be a wasted, pointless CLI call).
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: None)
    resume_draft = {
        "text": "resume text", "ats_score": 94, "ats_rationale": "r", "ats_next_actions": [],
        "clarifying_questions": [{"skill": "Nice to have", "question": "canned q", "suggested_answer": ""}],
        "suggested_strategy_tag": "tag", "unconfirmed_claims": [],
    }
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", lambda job, profile, on_progress=None, on_pid=None, already_asked_questions=None: resume_draft)
    _patch_round_persist(monkeypatch, round_number=1)

    rephrase_calls = []
    monkeypatch.setattr(sqa, "rephrase_canned_gap_questions_via_llm", lambda *a, **k: rephrase_calls.append(1) or [])

    sqa.run_subscription_round(dict(JOB), PROFILE)

    assert rephrase_calls == []


def test_run_subscription_round_ready_state_clears_questions_at_target(monkeypatch):
    # Zahir: "the aim is to always give the user 90+ ats score... if it is
    # less then 90 them its upto the user" - once a round hits 90+, no
    # more questions should be surfaced even if the model proposed some.
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: None)
    resume_draft = {
        "text": "resume text", "ats_score": 94, "ats_rationale": "r", "ats_next_actions": [],
        "clarifying_questions": [{"skill": "Nice to have", "question": "q", "suggested_answer": ""}],
        "suggested_strategy_tag": "tag", "unconfirmed_claims": [],
    }
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", lambda job, profile, on_progress=None, on_pid=None, already_asked_questions=None: resume_draft)
    upserts, statuses, round_calls = _patch_round_persist(monkeypatch, round_number=1)

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert result["resume_draft"]["qa_loop_state"] == "ready"
    assert result["resume_draft"]["clarifying_questions"] == []
    assert upserts[0][1]["resume_clarifying_questions"] == []
    assert round_calls[0]["loop_state"] == "ready"
    assert round_calls[0]["newly_asked_question_texts"] == []


def test_run_subscription_round_plateaus_at_round_cap_below_target(monkeypatch):
    # "cap at 2 or 3 rounds. if something cannot go up on 2-3 rounds it
    # will never go up" - the 3rd round, still under 90, must stop asking
    # rather than surfacing a 4th round's worth of questions.
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: None)
    resume_draft = {
        "text": "resume text", "ats_score": 78, "ats_rationale": "r", "ats_next_actions": [],
        "clarifying_questions": [{"skill": "Whatever", "question": "q", "suggested_answer": ""}],
        "suggested_strategy_tag": "tag", "unconfirmed_claims": [],
    }
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", lambda job, profile, on_progress=None, on_pid=None, already_asked_questions=None: resume_draft)
    # Prior record already has 2 completed rounds - this call becomes round 3.
    upserts, statuses, round_calls = _patch_round_persist(
        monkeypatch, round_number=3, prior_app_record={"subscription_qa_round": 2},
    )

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert result["resume_draft"]["qa_loop_state"] == "plateaued"
    assert result["resume_draft"]["clarifying_questions"] == []
    assert upserts[0][1]["resume_clarifying_questions"] == []
    assert round_calls[0]["loop_state"] == "plateaued"


def test_run_subscription_round_refuses_a_fourth_round_with_no_ai_call(monkeypatch):
    # CLAUDE.md "check for infinite loops" applied literally: once a job
    # already has MAX_QA_ROUNDS (3) completed rounds on record, a further
    # call (bulk re-click, per-job Retry, or the manual "Check for more
    # questions" -> answer -> redraft side door) must not spend a real
    # `claude` CLI call at all - not even to re-confirm "still plateaued".
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    released = []
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))
    monkeypatch.setattr(sqa, "get_application", lambda source, job_id: {"subscription_qa_round": 3, "resume_ats_score": 78})
    draft_calls = []
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", lambda *a, **k: draft_calls.append(1) or {})
    statuses = []
    monkeypatch.setattr(sqa, "set_qa_status", lambda *a, **k: statuses.append((a, k)))

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert draft_calls == []  # no AI call at all
    assert result == {"ok": True, "locked": False, "round": 3, "resume_draft": None, "error": None, "capped": True}
    assert released == [("linkedin", "1")]  # lock still released
    assert statuses == []  # status never touched - this was never "drafting"


def test_run_subscription_round_passes_prior_asked_questions_to_draft_call(monkeypatch):
    # Cross-round dedup (2026-08-17): the full text of every question
    # asked in an earlier round must reach the draft call, not just skill
    # labels, so round N's prompt can't restate round N-1's question.
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: None)
    draft_calls = []

    def _fake_draft(job, profile, on_progress=None, on_pid=None, already_asked_questions=None):
        draft_calls.append(already_asked_questions)
        return {
            "text": "t", "ats_score": 70, "ats_rationale": "r", "ats_next_actions": [],
            "clarifying_questions": [], "suggested_strategy_tag": "tag", "unconfirmed_claims": [],
        }
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", _fake_draft)
    prior = {"subscription_qa_round": 1, "subscription_qa_asked_question_texts": ["Do you have AWS experience?"]}
    _patch_round_persist(monkeypatch, round_number=2, prior_app_record=prior)

    sqa.run_subscription_round(dict(JOB), PROFILE)

    assert draft_calls == [["Do you have AWS experience?"]]


def test_run_subscription_round_failure_stamps_failed_and_releases_lock(monkeypatch):
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    released = []
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))
    monkeypatch.setattr(sqa, "get_application", lambda source, job_id: {})

    def _boom(job, profile, on_progress=None, on_pid=None, already_asked_questions=None):
        raise ReasonerUnavailable("claude CLI not on PATH")
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", _boom)
    statuses = []
    monkeypatch.setattr(sqa, "set_qa_status", lambda *a, **k: statuses.append((a, k)))

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert result["ok"] is False
    assert result["locked"] is False
    assert "claude CLI not on PATH" in result["error"]
    assert released == [("linkedin", "1")]  # lock released even on failure
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["drafting", "failed"]


# --- rank_and_cap_questions() / compute_qa_loop_state() ---


def test_compute_qa_loop_state_ready_at_or_above_target():
    assert sqa.compute_qa_loop_state(90, 1) == "ready"
    assert sqa.compute_qa_loop_state(95, 2) == "ready"


def test_compute_qa_loop_state_plateaued_at_round_cap_below_target():
    assert sqa.compute_qa_loop_state(85, 3) == "plateaued"
    assert sqa.compute_qa_loop_state(85, 4) == "plateaued"


def test_compute_qa_loop_state_in_progress_below_target_and_cap():
    assert sqa.compute_qa_loop_state(70, 1) == "in_progress"
    assert sqa.compute_qa_loop_state(70, 2) == "in_progress"


def test_rank_and_cap_questions_required_before_preferred_and_by_point_value():
    questions = [
        {"skill": "Nice thing", "question": "q1", "is_preferred": True, "point_value": 20},
        {"skill": "Small required", "question": "q2", "point_value": 2},
        {"skill": "Big required", "question": "q3", "point_value": 10},
    ]
    ranked = sqa.rank_and_cap_questions(questions, [], max_questions=3)
    assert [q["skill"] for q in ranked] == ["Big required", "Small required", "Nice thing"]


def test_rank_and_cap_questions_caps_to_max():
    questions = [{"skill": f"s{i}", "question": f"q{i}", "point_value": i} for i in range(10)]
    ranked = sqa.rank_and_cap_questions(questions, [], max_questions=3)
    assert len(ranked) == 3
    assert [q["skill"] for q in ranked] == ["s9", "s8", "s7"]  # highest point_value first


def test_rank_and_cap_questions_drops_cross_round_near_duplicates():
    prior = ["Do you have real, hands-on Databricks experience you can describe for this role?"]
    questions = [
        {"skill": "Databricks", "question": "Can you describe your real, hands-on Databricks experience for this role?", "point_value": 5},
        {"skill": "Genuinely new", "question": "Have you owned a P&L before?", "point_value": 3},
    ]
    ranked = sqa.rank_and_cap_questions(questions, prior, max_questions=3)
    assert [q["skill"] for q in ranked] == ["Genuinely new"]


# --- generate_questions_via_subscription() ---


def test_generate_questions_via_subscription_persists_and_stamps_awaiting_answers(monkeypatch):
    monkeypatch.setattr(sqa, "get_application", lambda source, job_id: {"resume_clarifying_questions": []})
    new_q = [{"skill": "Team size", "question": "How many direct reports?", "suggested_answer": ""}]
    monkeypatch.setattr(sqa, "_generate_questions_via_subscription", lambda job, profile, app_record, on_progress=None, on_pid=None: {
        "added_count": 1, "new_questions": new_q, "merged_clarifying_questions": new_q,
    })
    upserts = []
    monkeypatch.setattr(sqa, "upsert_application", lambda *a, **k: upserts.append((a, k)))
    statuses = []
    monkeypatch.setattr(sqa, "set_qa_status", lambda *a, **k: statuses.append((a, k)))

    result = sqa.generate_questions_via_subscription(dict(JOB), PROFILE)

    assert result["added_count"] == 1
    assert upserts[0][1]["resume_clarifying_questions"] == new_q
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["generating_questions", "awaiting_answers"]


def test_generate_questions_via_subscription_honest_empty_clears_status(monkeypatch):
    monkeypatch.setattr(sqa, "get_application", lambda source, job_id: {})
    monkeypatch.setattr(sqa, "_generate_questions_via_subscription", lambda job, profile, app_record, on_progress=None, on_pid=None: {
        "added_count": 0, "new_questions": [], "merged_clarifying_questions": [],
    })
    monkeypatch.setattr(sqa, "upsert_application", lambda *a, **k: None)
    statuses = []
    monkeypatch.setattr(sqa, "set_qa_status", lambda *a, **k: statuses.append((a, k)))

    result = sqa.generate_questions_via_subscription(dict(JOB), PROFILE)

    assert result["added_count"] == 0
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["generating_questions", None]


def test_generate_questions_via_subscription_propagates_and_stamps_failed(monkeypatch):
    monkeypatch.setattr(sqa, "get_application", lambda source, job_id: {})

    def _boom(job, profile, app_record, on_progress=None, on_pid=None):
        raise ReasonerUnavailable("claude CLI not on PATH")
    monkeypatch.setattr(sqa, "_generate_questions_via_subscription", _boom)
    statuses = []
    monkeypatch.setattr(sqa, "set_qa_status", lambda *a, **k: statuses.append((a, k)))

    try:
        sqa.generate_questions_via_subscription(dict(JOB), PROFILE)
        assert False, "expected ReasonerUnavailable"
    except ReasonerUnavailable:
        pass
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["generating_questions", "failed"]


# --- submit_answers_and_redraft() ---


def test_submit_answers_and_redraft_saves_answers_then_reloads_profile_before_redrafting(monkeypatch):
    # Real bug this guards against: save_gap_answers() writes straight to
    # the on-disk profile store - redrafting against the CALLER's stale,
    # already-in-memory profile dict would silently ignore the answer that
    # was just saved. This must reload from disk, not reuse the argument.
    save_calls = []
    monkeypatch.setattr(sqa, "save_gap_answers", lambda job, answered: save_calls.append(answered))
    fresh_profile = {"target_title_framings": [], "gap_interview_answers": [{"skill": "Team size"}]}
    monkeypatch.setattr("profile.storage.load_profile", lambda: fresh_profile)

    round_calls = []
    def _fake_round(job, profile, on_progress=None):
        round_calls.append(profile)
        return {"ok": True, "locked": False, "round": 2, "resume_draft": {"ats_score": 90}, "error": None}
    monkeypatch.setattr(sqa, "run_subscription_round", _fake_round)

    stale_profile = {"target_title_framings": []}  # deliberately NOT the fresh one
    answered = [{"skill": "Team size", "type": "skill_gap", "answer": "12", "question": "How many?"}]

    result = sqa.submit_answers_and_redraft(dict(JOB), stale_profile, answered)

    assert save_calls == [answered]
    assert round_calls == [fresh_profile]  # redrafted against the RELOADED profile, not the stale argument
    assert result["round"] == 2


def test_submit_answers_and_redraft_fires_progress_for_applying_answers(monkeypatch):
    monkeypatch.setattr(sqa, "save_gap_answers", lambda job, answered: None)
    monkeypatch.setattr("profile.storage.load_profile", lambda: {"target_title_framings": []})
    monkeypatch.setattr(sqa, "run_subscription_round", lambda job, profile, on_progress=None: {"ok": True, "locked": False, "round": 1, "resume_draft": {}, "error": None})

    seen = []
    sqa.submit_answers_and_redraft(dict(JOB), PROFILE, [], on_progress=seen.append)

    assert "applying your answers" in seen
