"""tailoring.gap_question_phrasing (2026-08-19) - real fix for Zahir's
standing complaint, unresolved 8-10 days as of 2026-08-19: Panga's per-job
clarifying/intake questions default to a bare, canned keyword-echo template
("The posting requires 'stakeholder management' - do you have real,
genuine experience with it?", drafting._keyword_gap_question_text())
instead of a real, specific follow-up grounded in the actual resume and the
actual job posting. Zahir's own words: the system should behave "like a
skilled interviewer who has read the candidate's resume and the job
description" - not run a keyword lookup.

**Investigation done before building this (this repo's standing rule -
read/understand real code before proposing changes):**

- The LLM-derived mechanism this task asked for already exists and is
  already wired into the real, live, most-used per-job intake flow.
  tailoring.subscription_resume_qa.draft_resume_via_subscription()'s
  _resume_prompt() already sends the reasoner (tailoring.reasoner_cli.
  run_claude_cli(), the $0 subscription-CLI mechanism proven out
  2026-08-13, never the paid Anthropic API) the candidate's FULL master
  profile and the job's FULL posting text in one prompt, and asks for
  clarifying_questions in the same reply - genuinely an LLM reading both
  documents, not a keyword match.
- tailoring.discuss_and_draft.py (mentioned in this task's brief as a place
  to check) is legacy/superseded: its own top docstring says
  subscription_resume_qa.py (2026-08-13, feature/in-app-subscription-qa)
  explicitly REPLACED it per Zahir's own rejection of the message-board
  UX ("the user will remain in the app they will not come to claude
  screens"). It's not imported anywhere in ui/app.py; subscription_resume_
  qa.run_subscription_round() is the real path every "Draft & score"/
  "Draft & score all" button in the app actually calls.
- So the actual bug isn't a missing LLM mechanism - it's that
  drafting._merge_keyword_gap_questions(), the deterministic, exhaustively
  tested (tests/test_drafting.py, 40+ cases) ATS-keyword-gap DETECTOR that
  runs AFTER the draft call (once the real ats_score.py scorer has computed
  which required/preferred keywords are still genuinely missing from the
  drafted resume), has no visibility into what the AI already tried to ask
  in its own free-form clarifying_questions. Any deterministically-
  confirmed gap the AI's own list didn't happen to independently phrase
  falls back to drafting._keyword_gap_question_text()'s bare template -
  exactly the "stakeholder engagement? stakeholder management?" shape
  Zahir has been reporting. (The one other concrete bug this task
  mentioned - the deterministic matcher missing a Bachelor's degree already
  on file - was already fixed separately: drafting._profile_narrative_
  units()/skill_label_match.build_already_known_units() both already read
  profile["education"], confirmed live in this investigation; not touched
  here.)

**Design, and why _merge_keyword_gap_questions() itself is deliberately
left untouched:** it's called from several hot/frequent paths (drafting.
analyze_fit_before_drafting() re-runs on every score refresh, not just once
per draft) where adding a network/subprocess call would directly violate
CLAUDE.md's performance and cost-blast-radius rules - a per-render LLM call
is exactly the kind of thing that rule exists to prevent. Instead, this
module adds one bounded, OPT-IN rephrasing pass, called only from the real
per-job in-app QA flow (subscription_resume_qa.run_subscription_round())
AFTER rank_and_cap_questions() has already trimmed the round's question
list down to its final, small (<= MAX_QUESTIONS_PER_ROUND, currently 3)
surfaced set: it finds exactly the entries whose question text is still
byte-identical to what _keyword_gap_question_text() would generate for
that skill (i.e., genuinely never touched by the AI's own phrasing), and
asks the SAME reasoner mechanism, in ONE additional batched subscription
call per round (never one call per question, and often zero calls at all
when nothing in the final surfaced set is still canned), to write a
natural, specific, interviewer-style question for each - grounded in the
job's own actual wording and the candidate's real profile, using the FULL
job posting and FULL profile as context, exactly like the draft call
itself already does.

Never invents a new gap: the set of skills being rephrased is always the
exact deterministic set drafting.py already confirmed missing (skill/
is_preferred/point_value/suggested_answer/keyword_verified all carried
through completely unchanged) - only the QUESTION TEXT for that already-
confirmed gap is replaced, and only when the rephrase call succeeds and
returns a real, non-empty question for that exact skill string. On any
failure this quietly keeps the original canned text - see
rephrase_canned_gap_questions_via_llm()'s own docstring for why this
deliberately never raises."""

import json

from tailoring.drafting import _keyword_gap_question_text
from tailoring.reasoner_cli import ReasonerUnavailable, parse_json_reply, run_claude_cli

_REPHRASE_SYSTEM_PROMPT = (
    "You are a skilled technical recruiter/interviewer who has read this candidate's full resume/profile "
    "and the full job posting below. For each gap term listed, this app has ALREADY deterministically "
    "confirmed it is a real, genuine gap - a required or preferred qualification stated in the job posting "
    "that this candidate's profile does not evidence. Your job is ONLY to phrase one specific, natural "
    "interview-style question per gap term, the way a real interviewer would ask it after reading both "
    "documents - referencing what the job posting is actually asking for (scope, scale, tools, context) and, "
    "where relevant, what's adjacent in the candidate's real background, instead of just echoing the bare "
    "term back as a yes/no prompt. Do not invent, add, remove, merge, or reinterpret gap terms - answer for "
    "EXACTLY the terms given, one question per term, using the exact same skill string as the key so the app "
    "can match your answer back to the right term. Never assert or imply the candidate does or doesn't have "
    "the skill - you are asking a genuine open question, never answering it or hinting at the answer."
)


def _is_canned_gap_question(q: dict) -> bool:
    """True only for a clarifying_question whose text is still byte-
    identical to what drafting._keyword_gap_question_text() would generate
    for its own skill/is_preferred right now - i.e. this app's own
    deterministic template, never touched by the AI's own phrasing. Never
    true for a question the AI already phrased in its own words, even one
    drafting._merge_keyword_gap_questions() backfilled a real point_value/
    keyword_verified flag onto (see that function's own docstring: the
    "match is not None" branch reuses the AI's own question text
    unchanged - only its fallback branches actually call
    _keyword_gap_question_text()). Comparing against a freshly regenerated
    copy of the template, rather than trusting a stored marker/flag, means
    this stays correct even if the template wording itself changes later
    without this module needing a matching update."""
    if q.get("type") != "skill_gap" or not q.get("skill"):
        return False
    return q.get("question") == _keyword_gap_question_text(q["skill"], is_preferred=bool(q.get("is_preferred")))


def _rephrase_prompt(job: dict, profile: dict, canned_questions: list[dict]) -> str:
    gap_terms = [
        {"skill": q["skill"], "is_preferred": bool(q.get("is_preferred"))}
        for q in canned_questions
    ]
    return "\n\n".join([
        _REPHRASE_SYSTEM_PROMPT,
        "CANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str),
        "JOB POSTING:\n" + json.dumps(job, indent=2, default=str),
        "GAP TERMS TO PHRASE A QUESTION FOR (already confirmed real gaps - do not add, remove, or change "
        "these):\n" + json.dumps(gap_terms, indent=2),
        "Reply with ONLY a single JSON object (no markdown code fence, no commentary before or after it) "
        "with exactly this key:\n"
        '- "rephrased_questions": array of objects {"skill": string (exactly matching one of the gap terms\' '
        '"skill" value above, byte for byte), "question": string (one natural, specific, interviewer-style '
        "question for that gap, grounded in the job posting's own wording and the candidate's real profile)}",
    ])


def rephrase_canned_gap_questions_via_llm(
    questions: list[dict], job: dict, profile: dict, on_progress=None,
) -> list[dict]:
    """Returns a NEW list (input list/items are never mutated) where every
    entry that is still the deterministic keyword-gap template
    (_is_canned_gap_question()) has its "question" text replaced by a real,
    JD-and-profile-grounded question from the reasoner, when the rephrase
    call succeeds and returns a usable result for that exact skill.
    Everything else - AI-authored free-form questions, and any canned entry
    the reply didn't cover - passes through completely unchanged.

    Intended to run AFTER rank_and_cap_questions() has already trimmed a
    round's candidate question list down to its final small (<=
    MAX_QUESTIONS_PER_ROUND) surfaced set - never against the full,
    unranked/uncapped list drafting.py's scorer produces, which can run to
    dozens of items on a real job. Called against the already-capped set,
    this is at most ONE additional bounded subscription call per round, and
    genuinely zero calls whenever nothing in that final set is still
    canned (the common case once the AI's own free-form questions already
    cover most real gaps in their own words).

    Never raises: a genuine reasoner failure here (ReasonerUnavailable -
    CLI not installed/not logged in; RuntimeError - timeout, non-JSON
    reply, no JSON object found) is caught and the original, unmodified
    questions are returned as-is. This is a deliberate departure from this
    codebase's usual "never swallow a real reasoner failure" discipline
    (see reasoner_cli.run_claude_cli's own docstring) - it's justified here
    specifically because this is a phrasing ENHANCEMENT layered onto an
    already-successful, already-persisted draft round, not a primary
    generation step; failing the whole round (and losing the resume draft/
    ATS score that already succeeded) over a rephrase-only failure would be
    a strictly worse outcome for the user than simply seeing the honest,
    if blunter, canned question they would have seen before this module
    existed. A malformed/partial reply (missing a skill, empty question
    text) is handled the same way per-item - whichever canned entries
    didn't get a usable rephrase just keep their original template text."""
    canned = [q for q in questions if _is_canned_gap_question(q)]
    if not canned:
        return questions

    if on_progress:
        on_progress("phrasing follow-up questions")
    try:
        reply = run_claude_cli(_rephrase_prompt(job, profile, canned))
        data = parse_json_reply(reply)
    except (ReasonerUnavailable, RuntimeError):
        return questions

    rephrased_by_skill = {}
    for item in data.get("rephrased_questions", []) or []:
        skill, new_question = item.get("skill"), item.get("question")
        if skill and new_question and new_question.strip():
            rephrased_by_skill[skill] = new_question.strip()

    result = []
    for q in questions:
        replacement = rephrased_by_skill.get(q.get("skill")) if _is_canned_gap_question(q) else None
        if replacement:
            result.append({**q, "question": replacement, "question_source": "llm_grounded"})
        else:
            result.append(q)
    return result
