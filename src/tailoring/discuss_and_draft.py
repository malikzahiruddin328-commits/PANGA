"""Basket operation "Discuss & draft" (2026-08-13) - the built version of
the confirmed hybrid design in docs/resume-hybrid-execution-design.md §1b.

Zahir's own words on the real workflow this implements: "basically all
questions and answers should be a 1on1 conversation as i am doing with you
and the final resume build should be the API call so that it is done once
and only once." Concretely, this exists to stop the exact pattern that cost
~$4 on one real job today (Catalent) across 3 redundant paid rounds of
request_additional_gap_questions()/"Answer more questions".

**2026-08-13 revision (fix/discuss-draft-subscription-questions) - real,
explicit correction from Zahir on the very same day this module first
shipped:** the first version above kept ONE paid request_additional_gap_
questions() call inside start_discussion(), reasoning that repeated
rounds (not the first one) were the actual problem. Zahir corrected that
directly: "remember above i said explicitly i want the last stage to be
API driven only" - i.e. EVERYTHING before finish_discussion()'s final
draft, including the very first round of question generation, must be
subscription-covered, not paid. start_discussion() now generates its
opening questions via _generate_questions_via_subscription() below - the
same real prompt/instructions request_additional_gap_questions() uses
(drafting._ANSWER_MORE_SYSTEM_PROMPT, unchanged), executed through the
same subscription-covered `claude` CLI subprocess mechanism
tailoring.reasoner_cli already implements (proven out the same day on
feature/resume-reasoner-path's resume/cover-letter drafting) instead of
the paid Anthropic API. request_additional_gap_questions() itself is
UNCHANGED and still used elsewhere (the existing "Answer more questions"
button, and anywhere else that already calls it directly) - this module
just no longer calls it.

Two phases, deliberately not one function - the whole point is that
something genuinely different happens in between them (a live human
conversation), not just two lines of code:

- start_discussion() - generates this job's real open questions via
  _generate_questions_via_subscription() (subscription-covered, see
  above), then posts them as one consolidated kind="question" entry to
  the shared cross-session message board (../../.claude/message_board.py,
  a sibling of this Panga checkout in the wider Myra workspace) so Zahir
  can resolve them the same way he resolved the real Catalent job today -
  live, in chat, for free - instead of clicking "Answer more questions"
  again. Stamps discussion_status="generating_questions" while the
  subprocess call is in flight (real progress visibility - that call can
  genuinely take upward of a minute, see reasoner_cli's own docstring),
  then "awaiting_discussion" once questions are posted.

- finish_discussion() - called once Zahir has answered every open question
  in a live conversation and a session has called drafting.save_gap_
  answers() with his real, unparaphrased answers (never invented - same
  discipline save_gap_answers() itself already documents). Fires exactly
  ONE final draft via the EXISTING, well-tested bulk_generate.
  generate_for_job() (self-correction loop, structured schema, both safety
  gates, the generation lock - all reused as-is, per the ask: "reuse it as-
  is, don't rebuild it"). Does not loop back into another gap-question
  round - "resolved" means resolved. UNCHANGED by this revision - this is
  still, and only, the one real paid API call in this whole module, per
  Zahir's explicit design intent quoted above.

Neither phase re-implements generate_documents()'s pipeline or the safety
gates - this module is orchestration and message-board plumbing around
code that already exists and is already tested elsewhere."""

import json
import os
import sys
from pathlib import Path

from skill_label_match import build_already_known_units, filter_questions_evidenced_in_profile, skills_match
from tailoring.applications import get_application, set_discussion_status, upsert_application
from tailoring.ats_score import score_resume_against_keywords
from tailoring.baseline_resume import select_baseline_resume_text
from tailoring.bulk_generate import generate_for_job
from tailoring.drafting import _ANSWER_MORE_SYSTEM_PROMPT, _CLARIFYING_QUESTION_ITEM_SCHEMA, _total_years_of_experience
from tailoring.reasoner_cli import ReasonerUnavailable, parse_json_reply, run_claude_cli


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


def _questions_prompt(job: dict, profile: dict, already_covered: list[str]) -> str:
    """Builds the reasoner-CLI prompt for this job's opening clarifying
    questions - the SAME system instructions request_additional_gap_
    questions() sends the paid API (drafting._ANSWER_MORE_SYSTEM_PROMPT,
    unchanged, unmodified), so the quality bar this module targets is
    identical to the paid call's, just executed via the subscription-
    covered CLI instead. Deliberately reuses that exact prompt text rather
    than writing a new one - Zahir's requirement #3 was explicit that the
    mechanism changing must not degrade the real value produced."""
    schema_lines = [
        '- "clarifying_questions": array of objects {"type": "skill_gap"|"disqualifier_check", "skill": string, "question": string, "suggested_answer": string} - '
        + _CLARIFYING_QUESTION_ITEM_SCHEMA["properties"]["type"]["description"]
    ]
    return "\n\n".join([
        _ANSWER_MORE_SYSTEM_PROMPT,
        "CANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str),
        "JOB POSTING:\n" + json.dumps(job, indent=2, default=str),
        "ALREADY COVERED (do not repeat any of these, even reworded):\n" + json.dumps(sorted(set(already_covered)), indent=2),
        "Reply with ONLY a single JSON object (no markdown code fence, no commentary before or after it) with exactly this key:\n"
        + "\n".join(schema_lines),
    ])


def _generate_questions_via_subscription(job: dict, profile: dict, app_record: dict, on_progress=None, on_pid=None) -> dict:
    """Subscription-covered replacement for drafting.
    request_additional_gap_questions(), used ONLY by start_discussion()
    (2026-08-13, fix/discuss-draft-subscription-questions - see this
    module's own top docstring for the explicit correction that prompted
    this). request_additional_gap_questions() itself is untouched and
    still the real tool for every other caller (e.g. the existing "Answer
    more questions" button) - this is a parallel, not a replacement,
    implementation, built specifically so this module's own opening-
    question step no longer touches the paid API at all.

    Mirrors request_additional_gap_questions()'s own already-covered
    computation and skills_match-based dedup/merge logic exactly (same
    non-fragile matching this codebase already established, not a bare
    substring check) so behavior is identical apart from which mechanism
    actually generates the candidate list of new questions. Returns the
    same {"added_count", "new_questions", "merged_clarifying_questions"}
    shape request_additional_gap_questions() does, so start_discussion()
    doesn't need to know which one produced it.

    Raises ReasonerUnavailable if the `claude` CLI itself can't be
    invoked (not installed/not logged in - the whole mechanism is
    unusable, not just this one call), or RuntimeError on a genuine
    per-call failure (timeout, non-JSON reply, no JSON object found) -
    neither is swallowed, matching this module's existing "never silently
    treat a failed generation as success" discipline."""
    required_keywords = job.get("ats_required_keywords") or []
    preferred_keywords = job.get("ats_preferred_keywords") or []
    resume_text = app_record.get("resume_text") or select_baseline_resume_text(job)[0]
    score_result = score_resume_against_keywords(
        required_keywords, preferred_keywords, resume_text,
        candidate_years_experience=_total_years_of_experience(profile),
    )

    existing_questions = app_record.get("resume_clarifying_questions") or []
    already_covered = (
        [q["skill"] for q in existing_questions if q.get("skill")]
        + [a["skill"] for a in profile.get("gap_interview_answers", []) if a.get("skill")]
        + [
            item["label"]
            for item in score_result["missing_required_keywords"] + score_result["missing_preferred_keywords"]
        ]
    )

    if on_progress:
        on_progress("generating questions")
    reply = run_claude_cli(_questions_prompt(job, profile, already_covered), on_start=on_pid)
    data = parse_json_reply(reply)

    new_questions = [
        q for q in data.get("clarifying_questions", [])
        if q.get("skill") and not any(skills_match(q["skill"], covered) for covered in already_covered)
    ]
    # Same deterministic backstop as drafting.py's _finalize_resume_draft/
    # request_additional_gap_questions (2026-08-17, real production
    # incident - see skill_label_match.py's own docstring item 3): the
    # already_covered check above only matches a proposed question's
    # "skill" LABEL against prior skill LABELS, so it can't catch a fact
    # already confirmed inside the free-text ANSWER of a differently-
    # labeled gap entry, or stated directly in a work_history/
    # client_engagements bullet that was never a "gap" question at all.
    new_questions = filter_questions_evidenced_in_profile(new_questions, build_already_known_units(profile))
    return {
        "added_count": len(new_questions),
        "new_questions": new_questions,
        "merged_clarifying_questions": existing_questions + new_questions,
    }


def start_discussion(job: dict, profile: dict, model: str | None = None, on_progress=None) -> dict:
    """Phase 1. Generates this job's real open clarifying/gap questions via
    _generate_questions_via_subscription() - a subscription-covered
    `claude` CLI call, NOT the paid Anthropic API (2026-08-13 revision,
    see this module's top docstring for Zahir's explicit correction: "i
    want the last stage to be API driven only", meaning everything before
    finish_discussion()'s final draft, including this opening-question
    step, must be off the paid path) - then posts them as ONE consolidated
    kind="question" board entry so Zahir can resolve them live and for
    free, the same way the real Catalent job was handled today.

    Stamps discussion_status="generating_questions" before the
    subscription call (real progress visibility for a call that can take
    upward of a minute on a large real profile - see reasoner_cli's own
    docstring for the measured order of magnitude on the equivalent
    resume-drafting call) so a second tab/session polling this record
    mid-call sees real state, not silence - same "not a black box"
    standard finish_discussion() already holds itself to for
    "drafting_final".

    `model` is accepted for backward signature compatibility only and is
    now unused - the subscription path has no per-call model choice the
    way the paid API call did.

    Returns:
    - {"posted": True, "message_id": str, "question_count": int,
      "app_record": dict} - questions posted, discussion_status is now
      "awaiting_discussion".
    - {"posted": False, "reason": "no_open_questions", "app_record": dict}
      - the subscription call legitimately found nothing further to ask
      (an honest "already know enough" outcome, not an error/failure) -
      the caller should treat this as "ready to draft now", no discussion
      needed, discussion_status is left untouched.

    Raises MessageBoardUnavailable if the board can't be reached at all
    (before any state is stamped - see _require_message_board), and
    ReasonerUnavailable/RuntimeError if the subscription call itself fails
    (see _generate_questions_via_subscription's own docstring) - neither
    is swallowed, since a caller that thinks "awaiting_discussion" got set
    when it didn't would show a silently wrong status. On a raised
    ReasonerUnavailable/RuntimeError, discussion_status is reverted to
    whatever it was before this call (None on a first attempt) rather than
    left stuck on "generating_questions" forever."""
    board = _require_message_board()
    source, job_id = job.get("source"), job.get("job_id")
    app_record = get_application(source, job_id) or {}
    previous_status = app_record.get("discussion_status")
    previous_message_id = app_record.get("discussion_board_message_id")

    set_discussion_status(source, job_id, "generating_questions", board_message_id=previous_message_id)
    try:
        result = _generate_questions_via_subscription(job, profile, app_record, on_progress=on_progress)
    except Exception:
        set_discussion_status(source, job_id, previous_status, board_message_id=previous_message_id)
        raise
    new_questions = result["new_questions"]

    # Persist the merged clarifying_questions either way - same thing the
    # existing "Answer more questions" button already does on every call,
    # discussion or not, so this job's stored question list stays fresh.
    upsert_application(
        source, job_id, status=app_record.get("status", "under review"),
        resume_clarifying_questions=result["merged_clarifying_questions"],
    )

    if not new_questions:
        set_discussion_status(source, job_id, previous_status, board_message_id=previous_message_id)
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
