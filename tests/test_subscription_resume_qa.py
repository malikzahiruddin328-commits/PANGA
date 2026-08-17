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


def _patch_round_persist(monkeypatch, round_number=1):
    monkeypatch.setattr(sqa, "get_application", lambda source, job_id: {})
    upserts = []
    monkeypatch.setattr(sqa, "upsert_application", lambda *a, **k: upserts.append((a, k)))
    monkeypatch.setattr(sqa, "sync_workspace_documents", lambda *a, **k: None)
    monkeypatch.setattr(sqa, "record_subscription_qa_round", lambda source, job_id, ats_score: round_number)
    statuses = []
    monkeypatch.setattr(sqa, "set_qa_status", lambda *a, **k: statuses.append((a, k)))
    return upserts, statuses


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
    monkeypatch.setattr(sqa, "draft_resume_via_subscription", lambda job, profile, on_progress=None, on_pid=None: resume_draft)
    upserts, statuses = _patch_round_persist(monkeypatch, round_number=1)

    result = sqa.run_subscription_round(dict(JOB), PROFILE)

    assert result == {"ok": True, "locked": False, "round": 1, "resume_draft": resume_draft, "error": None}
    assert released == [("linkedin", "1")]
    assert len(upserts) == 1
    upsert_kwargs = upserts[0][1]
    assert upsert_kwargs["resume_text"] == "resume text"
    assert upsert_kwargs["resume_draft_source"] == "subscription"
    assert upsert_kwargs["resume_ats_score"] == 82
    # Stamped "drafting" before the call, then cleared (None) on success.
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["drafting", None]


def test_run_subscription_round_failure_stamps_failed_and_releases_lock(monkeypatch):
    monkeypatch.setattr(sqa, "try_acquire_generation_lock", lambda source, job_id: True)
    released = []
    monkeypatch.setattr(sqa, "release_generation_lock", lambda source, job_id: released.append((source, job_id)))

    def _boom(job, profile, on_progress=None, on_pid=None):
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
