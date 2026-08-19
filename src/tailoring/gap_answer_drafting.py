"""Phase 4 of Zahir's "final set of questions" taxonomy-gap build
(2026-08-19): draft an answer to a recurring Profile Gaps question directly
from the candidate's OWN already-documented master profile, via the same $0
subscription `claude` CLI mechanism this codebase already uses elsewhere
(tailoring.reasoner_cli.run_claude_cli) - never the paid Anthropic API.

Proven out first as a one-off throwaway script
(scripts/_zahir_draft_recurring_gap_answers.py, 2026-08-19, already deleted -
its job was only to prove the approach, not to become the real feature) on
Zahir's real profile: 27 of 37 real recurring-gap questions were answerable
directly and correctly from real profile facts (work_history, certifications,
education, etc.) with zero fabrication, by instructing the model to answer
ONLY from facts actually present in the profile and to explicitly decline
(never guess) when the profile genuinely doesn't support a confident answer.
This module turns that proof into the real, permanent mechanism ui/app.py's
"Review recurring profile gaps" panel calls.

Zero fabrication, same standing discipline as tailoring.claim_verification
and drafting.no_reference_found_answer(): a drafted answer is ALWAYS shown to
Zahir as a pre-filled but editable box before anything saves - this module
itself never calls profile.interview.save_profile_gap_review_answers(), only
ui/app.py does, and only after Zahir explicitly confirms (see that module's
own docstring for why an unhedged AI suggestion must never be mistaken for a
confirmed fact). This module's own contract ends at "draft a candidate
answer, or honestly decline" - it has no save path of its own.

One reasoner call per question (never a batch array reply) - deliberately
avoids the known parse_json_reply() bug around top-level JSON arrays
(reasoner_cli.py, being fixed in parallel on fix/parse-json-reply-arrays,
not merged as of this build) by always requesting a single, object-wrapped
{"can_answer":..., "answer":...} reply per call, same known-working shape
run_subscription_round()/discuss_and_draft.py already rely on. Concurrency
for a multi-question batch happens at THIS module's own ThreadPoolExecutor
layer (draft_gap_answers_concurrently), not by asking the reasoner to answer
several questions in one reply."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from concurrency.adaptive_throttle import effective_worker_count
from tailoring.reasoner_cli import ReasonerUnavailable, parse_json_reply, run_claude_cli

logger = logging.getLogger(__name__)

# Same shape/spirit as ui/app.py's BASKET_DRAFTALL_MAX_WORKERS (2026-08-17
# precedent for a multi-item real-cost concurrent AI-call batch in this
# codebase) - a ceiling, backed off downward by
# concurrency.adaptive_throttle.effective_worker_count() right before the
# pool starts, never a guarantee every run gets exactly this many.
GAP_ANSWER_DRAFTALL_MAX_WORKERS = 6

GAP_ANSWER_SYSTEM_PROMPT = """You are helping a real job candidate answer a recurring ATS-keyword profile-gap question, using ONLY the facts already documented in their own master profile below. This is a zero-fabrication task.

Rules, no exceptions:
- Answer ONLY using facts explicitly present in the profile JSON (real employers, dates, titles, certifications, education, skills, tools, projects, responsibilities). Never guess, never infer beyond what the profile actually states, never invent a credential, employer, tool, or accomplishment that isn't literally supported by the profile.
- If the profile genuinely supports a confident, specific answer, set "can_answer" to true and write "answer" as a short, natural, declarative first-person answer that draws directly on the real profile facts that support it (mention the real employer/role/timeframe/detail where relevant).
- If the profile does NOT contain real support for this question - e.g. it asks about a certification, tool, or skill that simply isn't documented anywhere in the profile - set "can_answer" to false and write "answer" as a brief, honest note about what's missing. Do NOT invent a plausible-sounding answer just because one is expected; an honest "not documented" is the correct and required output when that's the truth.
- Never pad, hedge with a "?" you don't mean, or answer a different question than the one asked.

Respond with ONLY a bare JSON object of the exact shape {"can_answer": true or false, "answer": "..."} - no markdown code fence, no other text before or after it."""


def build_gap_answer_prompt(question: dict, profile: dict) -> str:
    """question: one item from skills.gap_frequency_analysis.
    build_review_questions() - {"skill":, "question":, "type":, ...}. Only
    "question" (the real, human-readable question text already phrased by
    that deterministic templating) and "skill" (for context) are used here;
    the rest of that dict is UI/analysis metadata this prompt has no use
    for.

    Reuses the SAME "CANDIDATE'S MASTER PROFILE:\\n" + json.dumps(profile,
    indent=2, default=str) framing every other subscription-covered prompt
    in this codebase already uses (discuss_and_draft.py, subscription_
    resume_qa.py, drafting.py) - one real, consistent way this app hands a
    profile to the reasoner, not a new one invented for this feature."""
    return (
        GAP_ANSWER_SYSTEM_PROMPT
        + "\n\nCANDIDATE'S MASTER PROFILE:\n"
        + json.dumps(profile, indent=2, default=str)
        + "\n\nQUESTION TO ANSWER:\n"
        + f"Skill/term: {question.get('skill', '')}\n"
        + f"Question: {question.get('question', '')}"
    )


def draft_gap_answer(question: dict, profile: dict) -> dict:
    """Single reasoner call, one question in, one answer out. Returns
    {"can_answer": bool, "answer": str, "error": None} on a real reply, or
    {"can_answer": False, "answer": "", "error": str} if the call itself
    failed or came back unparsable - callers (the UI, or
    draft_gap_answers_concurrently below) can render `error` as an honest
    failure message rather than mistaking a failed call for a genuine
    "profile doesn't support this" decline.

    Raises ReasonerUnavailable unchanged (not caught here) if the `claude`
    CLI itself isn't installed/on PATH/logged in - same contract as every
    other run_claude_cli() caller in this codebase, so a single per-item
    try/except at the call site (draft_gap_answers_concurrently, or the UI
    for a single-question "Draft with AI" click) is the one place that
    decides how to surface it, not buried here."""
    prompt = build_gap_answer_prompt(question, profile)
    reply_text = run_claude_cli(prompt)
    try:
        reply = parse_json_reply(reply_text)
    except RuntimeError as exc:
        logger.warning("gap_answer_drafting: unparsable reasoner reply for skill %r: %s", question.get("skill"), exc)
        return {"can_answer": False, "answer": "", "error": str(exc)}

    can_answer = bool(reply.get("can_answer"))
    answer = str(reply.get("answer") or "").strip()
    return {"can_answer": can_answer, "answer": answer, "error": None}


def draft_gap_answers_concurrently(
    items: list[tuple[str, dict]], profile: dict, max_workers: int | None = None,
) -> dict[str, dict]:
    """Batch variant for a "Draft all with AI" action - reuses the SAME
    ThreadPoolExecutor + concurrency.adaptive_throttle.effective_worker_count
    pattern ui/app.py's basket-wide "Draft & score all" button already
    established for exactly this shape of work (several independent,
    expensive AI calls that must run concurrently, not a sequential loop -
    see feedback_parallelize_batch_operations.md for the real incident that
    rule exists to prevent), rather than a bare `for` loop.

    items: list of (key, question) pairs - `key` is caller-defined (the
    UI's own per-question widget key) and is only ever used to route each
    result back to the right caller-side slot, never touched otherwise.

    Returns {key: result} for EVERY item, one entry each, in whatever order
    they complete - never raises. Each result is the same shape
    draft_gap_answer() returns, except a ReasonerUnavailable or any other
    unexpected exception for one item is caught here and reported as
    {"can_answer": False, "answer": "", "error": str(exc)} rather than
    taking the whole batch down - matching this codebase's standing "one
    item's failure shouldn't stop the rest" rule (see _draftall_worker's
    own docstring in ui/app.py for the same principle applied to the
    resume-drafting pool)."""
    if not items:
        return {}

    def _worker(key: str, question: dict) -> tuple[str, dict]:
        try:
            return key, draft_gap_answer(question, profile)
        except ReasonerUnavailable as exc:
            return key, {"can_answer": False, "answer": "", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - defense in depth, same as _draftall_worker in ui/app.py
            logger.exception("gap_answer_drafting: unexpected failure drafting an answer for key %r", key)
            return key, {"can_answer": False, "answer": "", "error": str(exc)}

    worker_count = effective_worker_count(
        min(len(items), max_workers or GAP_ANSWER_DRAFTALL_MAX_WORKERS)
    )
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_worker, key, question): key for key, question in items}
        for future in as_completed(futures):
            key, result = future.result()
            results[key] = result
    return results
