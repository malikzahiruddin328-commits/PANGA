"""Basket operation "Discuss & draft" (2026-08-13) - the built version of
the confirmed hybrid design in docs/resume-hybrid-execution-design.md §1b.

Zahir's own words on the real workflow this implements: "basically all
questions and answers should be a 1on1 conversation as i am doing with you
and the final resume build should be the API call so that it is done once
and only once." Concretely, this exists to stop the exact pattern that cost
~$4 on one real job today (Catalent) across 3 redundant paid rounds of
request_additional_gap_questions()/"Answer more questions" - the fix is
NOT re-implementing that question-generation call (it's fine to keep as a
one-time paid step - the problem is REPEATED rounds, not the first one),
it's routing the actual back-and-forth dialogue through a live, free
Claude Code conversation instead, and making sure exactly ONE paid call
drafts the final documents once that dialogue is resolved.

Two phases, deliberately not one function - the whole point is that
something genuinely different happens in between them (a live human
conversation), not just two lines of code:

- start_discussion() - ONE call to the EXISTING request_additional_gap_
  questions() (unchanged, still the right tool for this - see its own
  docstring) to get this job's real open questions, then posts them as one
  consolidated kind="question" entry to the shared cross-session message
  board (../../.claude/message_board.py, a sibling of this Panga checkout
  in the wider Myra workspace) so Zahir can resolve them the same way he
  resolved the real Catalent job today - live, in chat, for free - instead
  of clicking "Answer more questions" again. Stamps discussion_status=
  "awaiting_discussion" on the application record.

- finish_discussion() - called once Zahir has answered every open question
  in a live conversation and a session has called drafting.save_gap_
  answers() with his real, unparaphrased answers (never invented - same
  discipline save_gap_answers() itself already documents). Fires exactly
  ONE final draft via the EXISTING, well-tested bulk_generate.
  generate_for_job() (self-correction loop, structured schema, both safety
  gates, the generation lock - all reused as-is, per the ask: "reuse it as-
  is, don't rebuild it"). Does not loop back into another gap-question
  round - "resolved" means resolved.

Neither phase re-implements generate_documents()'s pipeline, the safety
gates, or the gap-question generation itself - this module is orchestration
and message-board plumbing around code that already exists and is already
tested elsewhere."""

import os
import sys
from pathlib import Path

from tailoring.applications import get_application, set_discussion_status, upsert_application
from tailoring.bulk_generate import generate_for_job
from tailoring.drafting import request_additional_gap_questions


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Board-facing identity for entries this module writes/updates. Not a real
# dedicated Claude Code session (this code runs inside the Streamlit
# process, or a one-off script a live session invokes) - named so a reader
# of the board can tell where the entry came from, same spirit as every
# other `from`/`to` value already on the board being a real, distinguishable
# identity, never a generic "system".
BOARD_IDENTITY = "Panga-App (Discuss & draft)"

# Per the workspace's "single point of contact" convention (dedicated
# sessions route judgment calls and questions through the hub, never
# straight to Zahir) - see .claude/session-map.md - this posts to the hub,
# not to Zahir directly. Whichever live Claude Code session Zahir actually
# has the conversation in reads/resolves it from there.
BOARD_RECIPIENT = "Panga-General"


def _find_message_board_dir(project_root: Path) -> Path | None:
    """Locates the sibling message_board.py module, normally at
    <Myra>/.claude/message_board.py - one level above the Panga checkout,
    same "walk up the ancestor chain" pattern ui/app.py's _find_bhangi_src
    already uses for the analogous Bhangi lookup, needed for the same
    reason: every git worktree branch lives under Panga/.claude/worktrees/
    <branch>/, where project_root.parent has no .claude/message_board.py
    of its own - walking up passes through the real Panga checkout for any
    worktree. MESSAGE_BOARD_DIR env var overrides this search entirely."""
    override = os.environ.get("MESSAGE_BOARD_DIR")
    if override:
        candidate = Path(override)
        if (candidate / "message_board.py").is_file():
            return candidate
    for ancestor in (project_root, *project_root.parents):
        candidate = ancestor.parent / ".claude"
        if (candidate / "message_board.py").is_file():
            return candidate
    return None


_message_board_dir = _find_message_board_dir(PROJECT_ROOT)
if _message_board_dir is not None and str(_message_board_dir) not in sys.path:
    sys.path.insert(0, str(_message_board_dir))

try:
    import message_board as _message_board
except ImportError:
    _message_board = None


class MessageBoardUnavailable(Exception):
    """Raised when .claude/message_board.py couldn't be located/imported -
    see _find_message_board_dir(). Discuss & draft cannot post real
    questions without it, so this is a hard stop, not a silent skip (a
    silently-skipped post would leave discussion_status stuck at
    "awaiting_discussion" with nothing Zahir could ever actually see)."""


def _require_message_board():
    if _message_board is None:
        raise MessageBoardUnavailable(
            "Could not locate/import the shared message_board.py module "
            "(expected at <Myra>/.claude/message_board.py, a sibling of "
            "the Panga checkout). Discuss & draft cannot post questions "
            "without it - set MESSAGE_BOARD_DIR if it lives somewhere else."
        )
    return _message_board


def _format_questions_for_board(job: dict, questions: list[dict]) -> str:
    """Plain, human-readable question summary for message_board.py's
    render_human_readable() view (Zahir reads this directly, not JSON) -
    real job context up front, then every open question with its skill
    label so a live session picking this up has enough to actually start
    the conversation without a second lookup."""
    header = f"Open questions for {job.get('title', 'Unknown role')} at {job.get('organization', 'Unknown organization')} ({job.get('source')}/{job.get('job_id')}):"
    lines = [header]
    for i, q in enumerate(questions, start=1):
        skill = q.get("skill", "")
        question_text = q.get("question", "")
        lines.append(f"  {i}. [{skill}] {question_text}")
    lines.append(
        "Resolve these in a live conversation, then call "
        "tailoring.drafting.save_gap_answers() with the real answers and "
        "tailoring.discuss_and_draft.finish_discussion() to trigger the one "
        "final draft."
    )
    return "\n".join(lines)


def start_discussion(job: dict, profile: dict, model: str | None = None) -> dict:
    """Phase 1. Generates this job's real open clarifying/gap questions via
    the existing request_additional_gap_questions() (a one-time paid call,
    not the repeated-rounds problem this feature exists to stop), then
    posts them as ONE consolidated kind="question" board entry so Zahir can
    resolve them live and for free, the same way the real Catalent job was
    handled today.

    Returns:
    - {"posted": True, "message_id": str, "question_count": int,
      "app_record": dict} - questions posted, discussion_status is now
      "awaiting_discussion".
    - {"posted": False, "reason": "no_open_questions", "app_record": dict}
      - request_additional_gap_questions() legitimately found nothing
      further to ask (an honest "already know enough" outcome, not an
      error/failure) - the caller should treat this as "ready to draft
      now", no discussion needed, discussion_status is left untouched.

    Raises MessageBoardUnavailable if the board can't be reached at all
    (before any state is stamped - see _require_message_board), and
    whatever request_additional_gap_questions() itself can raise
    (DraftingNotConfigured/DraftingFailed) if that call fails - neither is
    swallowed, since a caller that thinks "awaiting_discussion" got set
    when it didn't would show a silently wrong status."""
    board = _require_message_board()
    source, job_id = job.get("source"), job.get("job_id")
    app_record = get_application(source, job_id) or {}

    result = request_additional_gap_questions(job, profile, app_record, model=model)
    new_questions = result["new_questions"]

    # Persist the merged clarifying_questions either way - same thing the
    # existing "Answer more questions" button already does on every call,
    # discussion or not, so this job's stored question list stays fresh.
    upsert_application(
        source, job_id, status=app_record.get("status", "under review"),
        resume_clarifying_questions=result["merged_clarifying_questions"],
    )

    if not new_questions:
        return {"posted": False, "reason": "no_open_questions", "app_record": get_application(source, job_id)}

    summary = _format_questions_for_board(job, new_questions)
    message_id = board.write_message(
        from_session=BOARD_IDENTITY, to_session=BOARD_RECIPIENT, summary=summary, kind="question",
    )
    set_discussion_status(source, job_id, "awaiting_discussion", board_message_id=message_id)
    return {
        "posted": True, "message_id": message_id, "question_count": len(new_questions),
        "app_record": get_application(source, job_id),
    }


def finish_discussion(job: dict, profile: dict, doc_keys: list[str]) -> dict:
    """Phase 2. Called once Zahir has resolved every open question in a
    live conversation and a session has already called drafting.
    save_gap_answers() with his real, confirmed answers (this function does
    NOT itself capture answers or call save_gap_answers() - that already
    happened, live, before this runs, exactly like the real Catalent
    precedent). Fires exactly ONE final draft via the existing generate_
    for_job() (same generation lock, same persistence, same safety gates
    as every other draft in this app) - never loops back into another
    gap-question round.

    Stamps discussion_status "drafting_final" before the call (so a second
    tab/session polling this record mid-draft sees real state, not a black
    box - generate_for_job()'s self-correction loop can run several
    minutes) and "done"/"failed" after, mirroring generate_for_job()'s own
    {"ok", "locked", "errors"} result shape in discussion_error when it
    fails. A lock collision (another generation already in progress for
    this exact job) is reported the same way, not treated as a discussion
    failure - discussion_status reverts to "awaiting_discussion" so the
    caller can just try again once the other generation finishes.

    If a board message is on record for this discussion, marks it
    "verified" on success - real independent evidence the discussion led
    to a real completed draft, not a self-report. Deliberately does NOT
    call message_board.answer_question() to attach the literal answer text
    back onto the board entry - the real answers live in the profile's
    gap_interview_answers via save_gap_answers(), and the live conversation
    itself is the actual record of what was asked/answered; duplicating
    that text onto the board would be a second, driftable copy of the same
    fact. Flagged as a judgment call, not settled by the design doc.

    Returns generate_for_job()'s own result dict, unchanged, plus
    "discussion_status" reflecting what got stamped."""
    source, job_id = job.get("source"), job.get("job_id")
    app_record = get_application(source, job_id) or {}
    message_id = app_record.get("discussion_board_message_id")

    set_discussion_status(source, job_id, "drafting_final", board_message_id=message_id)
    result = generate_for_job(job, profile, doc_keys)

    if result.get("locked"):
        # Someone else is mid-draft for this exact job right now - not a
        # discussion failure, just try again once that finishes. Revert to
        # "awaiting_discussion" rather than leaving "drafting_final" stuck
        # forever with nothing actually in flight.
        set_discussion_status(source, job_id, "awaiting_discussion", board_message_id=message_id)
        result["discussion_status"] = "awaiting_discussion"
        return result

    if result["ok"]:
        set_discussion_status(source, job_id, "done", board_message_id=message_id)
        result["discussion_status"] = "done"
        if message_id and _message_board is not None:
            try:
                _message_board.update_status(message_id, "verified", updated_by=BOARD_IDENTITY)
            except (KeyError, ValueError):
                # Board entry already moved on its own (e.g. re-answered,
                # or already verified) - not this function's problem to
                # fix; the real completion state already got persisted
                # above regardless of board bookkeeping succeeding.
                pass
    else:
        error_summary = "; ".join(f"{k}: {v}" for k, v in result["errors"].items()) or "unknown error"
        set_discussion_status(source, job_id, "failed", board_message_id=message_id, error=error_summary)
        result["discussion_status"] = "failed"

    return result
