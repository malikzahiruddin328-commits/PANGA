"""tailoring.discuss_and_draft (2026-08-13, "Discuss & draft" basket
operation - docs/resume-hybrid-execution-design.md §1b). Everything this
module calls into (request_additional_gap_questions, generate_for_job,
save_gap_answers, message_board.write_message/update_status) is already
covered by its own tests elsewhere - these tests are about the
orchestration this module adds: posting exactly one consolidated board
entry, the "no open questions" short-circuit, and the discussion_status
state machine (awaiting_discussion -> drafting_final -> done/failed,
including the lock-collision revert).

Does NOT depend on the real sibling message_board.py module - the fake
_FakeBoard stub below stands in for it, so this suite works identically
whether or not <Myra>/.claude/message_board.py happens to be reachable
from wherever the suite runs (a real concern: this module lives outside
the Panga repo entirely, in the wider Myra workspace)."""

import tailoring.discuss_and_draft as discuss_and_draft


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
    monkeypatch.setattr(discuss_and_draft, "request_additional_gap_questions", lambda *a, **k: {
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
    # discussion_status was stamped "awaiting_discussion" with the real board message id
    assert statuses[-1][0] == ("linkedin", "1", "awaiting_discussion")
    assert statuses[-1][1]["board_message_id"] == entry["id"]


def test_start_discussion_no_open_questions_does_not_post_or_stamp_status(monkeypatch):
    board = _FakeBoard()
    monkeypatch.setattr(discuss_and_draft, "_message_board", board)
    upserts, statuses = _patch_common(monkeypatch, app_record={})
    monkeypatch.setattr(discuss_and_draft, "request_additional_gap_questions", lambda *a, **k: {
        "added_count": 0, "new_questions": [], "merged_clarifying_questions": [],
    })

    result = discuss_and_draft.start_discussion(JOB, PROFILE)

    assert result == {"posted": False, "reason": "no_open_questions", "app_record": {}}
    assert board.written == []
    assert statuses == []  # discussion_status untouched - nothing to discuss


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
    # request_additional_gap_questions() round triggered from here.
    monkeypatch.setattr(discuss_and_draft, "_message_board", None)
    monkeypatch.setattr(discuss_and_draft, "get_application", lambda source, job_id: {})
    monkeypatch.setattr(discuss_and_draft, "set_discussion_status", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(discuss_and_draft, "generate_for_job", lambda *a, **k: (calls.append(1), {"ok": True, "locked": False, "errors": {}})[1])

    def _fail_if_called(*a, **k):
        raise AssertionError("finish_discussion() must never call request_additional_gap_questions()")
    monkeypatch.setattr(discuss_and_draft, "request_additional_gap_questions", _fail_if_called)

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
