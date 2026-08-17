"""tailoring.discuss_and_draft (2026-08-13, "Discuss & draft" basket
operation - docs/resume-hybrid-execution-design.md §1b). Everything this
module calls into (generate_for_job, save_gap_answers, message_board.
write_message/update_status) is already covered by its own tests
elsewhere - these tests are about the orchestration this module adds:
posting exactly one consolidated board entry, the "no open questions"
short-circuit, and the discussion_status state machine
(generating_questions -> awaiting_discussion -> drafting_final ->
done/failed, including the lock-collision revert).

2026-08-13 revision (fix/discuss-draft-subscription-questions):
start_discussion() no longer calls the paid
tailoring.drafting.request_additional_gap_questions() at all - its
opening-question generation now goes through
discuss_and_draft._generate_questions_via_subscription(), a subscription-
covered `claude` CLI call (tailoring.reasoner_cli). The start_discussion()
tests below mock _generate_questions_via_subscription directly (the
module-level seam start_discussion() actually calls), same pattern the
old suite used for request_additional_gap_questions() before this
revision. _generate_questions_via_subscription()'s own internal
logic (already-covered computation, dedup/merge) is covered separately
further down, mocking one level deeper at run_claude_cli.

Does NOT depend on the real sibling message_board.py module - the fake
_FakeBoard stub below stands in for it, so this suite works identically
whether or not <Myra>/.claude/message_board.py happens to be reachable
from wherever the suite runs (a real concern: this module lives outside
the Panga repo entirely, in the wider Myra workspace)."""

import json

import tailoring.discuss_and_draft as discuss_and_draft
from tailoring.reasoner_cli import ReasonerUnavailable


JOB = {"source": "linkedin", "job_id": "1", "title": "VP IT", "organization": "Acme"}
PROFILE = {"target_title_framings": []}


class _FakeBoard:
    def __init__(self):
        self.written = []
        self.status_updates = []
        self._next_id = 0

    def write_message(self, from_session, to_session, summary, kind, supersedes=None):
        self._next_id += 1
        message_id = f"fake-{self._next_id}"
        self.written.append({
            "id": message_id, "from": from_session, "to": to_session,
            "summary": summary, "kind": kind,
        })
        return message_id

    def update_status(self, message_id, status, updated_by):
        self.status_updates.append((message_id, status, updated_by))


def _patch_common(monkeypatch, app_record=None):
    monkeypatch.setattr(discuss_and_draft, "get_application", lambda source, job_id: app_record)
    upserts = []
    monkeypatch.setattr(discuss_and_draft, "upsert_application", lambda *a, **k: upserts.append((a, k)))
    statuses = []
    monkeypatch.setattr(discuss_and_draft, "set_discussion_status", lambda *a, **k: statuses.append((a, k)))
    return upserts, statuses


# --- start_discussion() ---


def test_start_discussion_raises_when_board_unavailable(monkeypatch):
    monkeypatch.setattr(discuss_and_draft, "_message_board", None)
    try:
        discuss_and_draft.start_discussion(JOB, PROFILE)
        assert False, "expected MessageBoardUnavailable"
    except discuss_and_draft.MessageBoardUnavailable:
        pass


def test_start_discussion_posts_one_consolidated_entry_and_sets_awaiting(monkeypatch):
    board = _FakeBoard()
    monkeypatch.setattr(discuss_and_draft, "_message_board", board)
    upserts, statuses = _patch_common(monkeypatch, app_record={})
    monkeypatch.setattr(discuss_and_draft, "_generate_questions_via_subscription", lambda *a, **k: {
        "added_count": 2,
        "new_questions": [
            {"skill": "Budget size", "question": "What was your team's annual budget?"},
            {"skill": "Team size", "question": "How many direct reports did you have?"},
        ],
        "merged_clarifying_questions": [
            {"skill": "Budget size", "question": "What was your team's annual budget?"},
            {"skill": "Team size", "question": "How many direct reports did you have?"},
        ],
    })

    result = discuss_and_draft.start_discussion(JOB, PROFILE)

    assert result["posted"] is True
    assert result["question_count"] == 2
    assert len(board.written) == 1  # ONE consolidated entry, not one per question
    entry = board.written[0]
    assert entry["kind"] == "question"
    assert entry["to"] == discuss_and_draft.BOARD_RECIPIENT
    assert "Budget size" in entry["summary"]
    assert "Team size" in entry["summary"]
    # discussion_status was stamped "generating_questions" first (real
    # progress visibility while the subscription call is in flight), then
    # "awaiting_discussion" once questions were actually posted.
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["generating_questions", "awaiting_discussion"]
    assert statuses[-1][0] == ("linkedin", "1", "awaiting_discussion")
    assert statuses[-1][1]["board_message_id"] == entry["id"]


def test_start_discussion_no_open_questions_reverts_status_and_does_not_post(monkeypatch):
    board = _FakeBoard()
    monkeypatch.setattr(discuss_and_draft, "_message_board", board)
    upserts, statuses = _patch_common(monkeypatch, app_record={})
    monkeypatch.setattr(discuss_and_draft, "_generate_questions_via_subscription", lambda *a, **k: {
        "added_count": 0, "new_questions": [], "merged_clarifying_questions": [],
    })

    result = discuss_and_draft.start_discussion(JOB, PROFILE)

    assert result == {"posted": False, "reason": "no_open_questions", "app_record": {}}
    assert board.written == []
    # Stamped "generating_questions" for progress visibility, then reverted
    # to the prior status (None, since app_record had no discussion_status
    # yet) once the subscription call honestly found nothing further to ask.
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["generating_questions", None]


def test_start_discussion_never_calls_the_paid_gap_question_api(monkeypatch):
    # The whole point of this revision: start_discussion() must not import
    # or call the paid request_additional_gap_questions() at all anymore.
    assert not hasattr(discuss_and_draft, "request_additional_gap_questions")


def test_start_discussion_reverts_status_and_reraises_on_reasoner_failure(monkeypatch):
    board = _FakeBoard()
    monkeypatch.setattr(discuss_and_draft, "_message_board", board)
    upserts, statuses = _patch_common(monkeypatch, app_record={"discussion_status": "failed"})

    def _boom(*a, **k):
        raise ReasonerUnavailable("claude CLI not on PATH")
    monkeypatch.setattr(discuss_and_draft, "_generate_questions_via_subscription", _boom)

    try:
        discuss_and_draft.start_discussion(JOB, PROFILE)
        assert False, "expected ReasonerUnavailable"
    except ReasonerUnavailable:
        pass

    assert board.written == []
    stamped = [args[2] for args, kwargs in statuses]
    # Stamped "generating_questions", then reverted back to whatever it was
    # before this call ("failed", from a previous failed attempt) rather
    # than left stuck on "generating_questions" forever.
    assert stamped == ["generating_questions", "failed"]


def test_start_discussion_progress_callback_fires_during_subscription_call(monkeypatch):
    board = _FakeBoard()
    monkeypatch.setattr(discuss_and_draft, "_message_board", board)
    _patch_common(monkeypatch, app_record={})
    seen = []

    def _fake_generate(job, profile, app_record, on_progress=None):
        if on_progress:
            on_progress("generating questions")
        return {"added_count": 0, "new_questions": [], "merged_clarifying_questions": []}
    monkeypatch.setattr(discuss_and_draft, "_generate_questions_via_subscription", _fake_generate)

    discuss_and_draft.start_discussion(JOB, PROFILE, on_progress=lambda stage: seen.append(stage))

    assert seen == ["generating questions"]


# --- finish_discussion() ---


def test_finish_discussion_success_marks_done_and_verifies_board(monkeypatch):
    board = _FakeBoard()
    monkeypatch.setattr(discuss_and_draft, "_message_board", board)
    monkeypatch.setattr(discuss_and_draft, "get_application", lambda source, job_id: {"discussion_board_message_id": "fake-1"})
    statuses = []
    monkeypatch.setattr(discuss_and_draft, "set_discussion_status", lambda *a, **k: statuses.append((a, k)))
    monkeypatch.setattr(discuss_and_draft, "generate_for_job", lambda *a, **k: {"ok": True, "locked": False, "errors": {}})

    result = discuss_and_draft.finish_discussion(JOB, PROFILE, ["resume", "cover_letter"])

    assert result["ok"] is True
    assert result["discussion_status"] == "done"
    # stamped "drafting_final" before the call, "done" after - exactly once each
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["drafting_final", "done"]
    assert board.status_updates == [("fake-1", "verified", discuss_and_draft.BOARD_IDENTITY)]


def test_finish_discussion_does_not_reloop_into_another_gap_question_round(monkeypatch):
    # The whole point: exactly one generate_for_job() call, never a second
    # question-generation round (paid or subscription) triggered from here.
    monkeypatch.setattr(discuss_and_draft, "_message_board", None)
    monkeypatch.setattr(discuss_and_draft, "get_application", lambda source, job_id: {})
    monkeypatch.setattr(discuss_and_draft, "set_discussion_status", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(discuss_and_draft, "generate_for_job", lambda *a, **k: (calls.append(1), {"ok": True, "locked": False, "errors": {}})[1])

    def _fail_if_called(*a, **k):
        raise AssertionError("finish_discussion() must never call _generate_questions_via_subscription()")
    monkeypatch.setattr(discuss_and_draft, "_generate_questions_via_subscription", _fail_if_called)

    discuss_and_draft.finish_discussion(JOB, PROFILE, ["resume"])
    assert calls == [1]


def test_finish_discussion_failure_marks_failed_with_error_message(monkeypatch):
    monkeypatch.setattr(discuss_and_draft, "_message_board", None)
    monkeypatch.setattr(discuss_and_draft, "get_application", lambda source, job_id: {})
    statuses = []
    monkeypatch.setattr(discuss_and_draft, "set_discussion_status", lambda *a, **k: statuses.append((a, k)))
    monkeypatch.setattr(discuss_and_draft, "generate_for_job", lambda *a, **k: {
        "ok": False, "locked": False, "errors": {"cover_letter": "web search failed"},
    })

    result = discuss_and_draft.finish_discussion(JOB, PROFILE, ["resume", "cover_letter"])

    assert result["discussion_status"] == "failed"
    failed_call = statuses[-1]
    assert failed_call[0][2] == "failed"
    assert "web search failed" in failed_call[1]["error"]


def test_finish_discussion_lock_collision_reverts_to_awaiting_discussion(monkeypatch):
    monkeypatch.setattr(discuss_and_draft, "_message_board", None)
    monkeypatch.setattr(discuss_and_draft, "get_application", lambda source, job_id: {})
    statuses = []
    monkeypatch.setattr(discuss_and_draft, "set_discussion_status", lambda *a, **k: statuses.append((a, k)))
    monkeypatch.setattr(discuss_and_draft, "generate_for_job", lambda *a, **k: {"ok": False, "locked": True, "errors": {}})

    result = discuss_and_draft.finish_discussion(JOB, PROFILE, ["resume"])

    assert result["discussion_status"] == "awaiting_discussion"
    stamped = [args[2] for args, kwargs in statuses]
    assert stamped == ["drafting_final", "awaiting_discussion"]


def test_finish_discussion_board_verify_failure_does_not_break_the_result(monkeypatch):
    # If the board entry can't be found/updated for some reason (already
    # moved on, deleted, whatever), the real completion state must still
    # be reported correctly - board bookkeeping is best-effort here.
    class _BrokenBoard(_FakeBoard):
        def update_status(self, message_id, status, updated_by):
            raise KeyError("no such entry")
    monkeypatch.setattr(discuss_and_draft, "_message_board", _BrokenBoard())
    monkeypatch.setattr(discuss_and_draft, "get_application", lambda source, job_id: {"discussion_board_message_id": "fake-1"})
    monkeypatch.setattr(discuss_and_draft, "set_discussion_status", lambda *a, **k: None)
    monkeypatch.setattr(discuss_and_draft, "generate_for_job", lambda *a, **k: {"ok": True, "locked": False, "errors": {}})

    result = discuss_and_draft.finish_discussion(JOB, PROFILE, ["resume"])

    assert result["ok"] is True
    assert result["discussion_status"] == "done"


# --- _generate_questions_via_subscription() ---
# The subscription-covered replacement for request_additional_gap_
# questions() used only by start_discussion(). Mocks one level deeper
# (run_claude_cli) than the start_discussion() tests above, to cover its
# own real logic: already-covered computation and skills_match dedup/merge
# against a live reasoner reply.

REAL_JOB = {
    "source": "linkedin", "job_id": "42", "title": "VP IT", "organization": "Acme",
    "ats_required_keywords": ["Budget ownership"], "ats_preferred_keywords": [],
}
REAL_PROFILE = {
    "target_title_framings": [],
    "gap_interview_answers": [{"skill": "Team size", "date_captured": "2026-08-01"}],
}


def test_generate_questions_via_subscription_calls_cli_and_returns_new_questions(monkeypatch):
    calls = []

    def _fake_run_claude_cli(prompt, timeout_seconds=None, on_start=None):
        calls.append(prompt)
        return json.dumps({
            "clarifying_questions": [
                {"type": "skill_gap", "skill": "Vendor negotiation", "question": "How large were the vendor contracts you negotiated?", "suggested_answer": ""},
            ]
        })
    monkeypatch.setattr(discuss_and_draft, "run_claude_cli", _fake_run_claude_cli)
    monkeypatch.setattr(discuss_and_draft, "score_resume_against_keywords", lambda *a, **k: {
        "missing_required_keywords": [], "missing_preferred_keywords": [],
    })
    monkeypatch.setattr(discuss_and_draft, "select_baseline_resume_text", lambda job: ("resume text", "baseline"))

    result = discuss_and_draft._generate_questions_via_subscription(REAL_JOB, REAL_PROFILE, {})

    assert len(calls) == 1
    # The real system prompt (same instructions the paid call uses) and
    # real job/profile content actually reach the CLI call - not a stub.
    assert "genuinely distinct" in calls[0]  # from _ANSWER_MORE_SYSTEM_PROMPT
    assert "VP IT" in calls[0]
    assert result["added_count"] == 1
    assert result["new_questions"][0]["skill"] == "Vendor negotiation"
    assert result["merged_clarifying_questions"] == result["new_questions"]


def test_generate_questions_via_subscription_dedupes_against_already_covered(monkeypatch):
    # "Team size" is already in gap_interview_answers - a question the
    # reasoner proposes for it anyway must be filtered out, same
    # skills_match-based dedup request_additional_gap_questions() itself
    # uses, not a bare substring check.
    def _fake_run_claude_cli(prompt, timeout_seconds=None, on_start=None):
        return json.dumps({
            "clarifying_questions": [
                {"type": "skill_gap", "skill": "Team size", "question": "How many direct reports?", "suggested_answer": ""},
                {"type": "skill_gap", "skill": "M&A experience", "question": "Have you led an acquisition integration?", "suggested_answer": ""},
            ]
        })
    monkeypatch.setattr(discuss_and_draft, "run_claude_cli", _fake_run_claude_cli)
    monkeypatch.setattr(discuss_and_draft, "score_resume_against_keywords", lambda *a, **k: {
        "missing_required_keywords": [], "missing_preferred_keywords": [],
    })
    monkeypatch.setattr(discuss_and_draft, "select_baseline_resume_text", lambda job: ("resume text", "baseline"))

    result = discuss_and_draft._generate_questions_via_subscription(REAL_JOB, REAL_PROFILE, {})

    assert result["added_count"] == 1
    assert [q["skill"] for q in result["new_questions"]] == ["M&A experience"]


def test_generate_questions_via_subscription_honest_empty_result(monkeypatch):
    monkeypatch.setattr(discuss_and_draft, "run_claude_cli", lambda prompt, timeout_seconds=None, on_start=None: json.dumps({"clarifying_questions": []}))
    monkeypatch.setattr(discuss_and_draft, "score_resume_against_keywords", lambda *a, **k: {
        "missing_required_keywords": [], "missing_preferred_keywords": [],
    })
    monkeypatch.setattr(discuss_and_draft, "select_baseline_resume_text", lambda job: ("resume text", "baseline"))

    result = discuss_and_draft._generate_questions_via_subscription(REAL_JOB, REAL_PROFILE, {})

    assert result == {"added_count": 0, "new_questions": [], "merged_clarifying_questions": []}


def test_generate_questions_via_subscription_propagates_reasoner_unavailable(monkeypatch):
    def _boom(prompt, timeout_seconds=None, on_start=None):
        raise ReasonerUnavailable("claude CLI not on PATH")
    monkeypatch.setattr(discuss_and_draft, "run_claude_cli", _boom)
    monkeypatch.setattr(discuss_and_draft, "score_resume_against_keywords", lambda *a, **k: {
        "missing_required_keywords": [], "missing_preferred_keywords": [],
    })
    monkeypatch.setattr(discuss_and_draft, "select_baseline_resume_text", lambda job: ("resume text", "baseline"))

    try:
        discuss_and_draft._generate_questions_via_subscription(REAL_JOB, REAL_PROFILE, {})
        assert False, "expected ReasonerUnavailable"
    except ReasonerUnavailable:
        pass
