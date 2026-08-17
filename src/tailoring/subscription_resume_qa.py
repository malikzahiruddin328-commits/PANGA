"""In-app, subscription-covered basket document build (2026-08-13,
feature/in-app-subscription-qa) - Zahir's confirmed real design for step 4
of basket document generation, REPLACING "Discuss & draft"'s message-
board/external-Claude-Code-session shape, which he explicitly rejected:
"the user will remain in the app they will not come to claude screens."

Three real steps, all happening inside Panga's own Streamlit UI:

1. run_subscription_round() drafts (or re-drafts) a resume for a job via
   the SAME subscription-covered `claude` CLI subprocess mechanism
   tailoring.reasoner_cli implements (proven out 2026-08-13 on both
   feature/resume-reasoner-path's reasoner_pipeline.py and this repo's own
   tailoring.discuss_and_draft) - never the paid Anthropic API. The exact
   same deterministic safety gates run on the result as the paid path:
   tailoring.drafting._finalize_resume_draft() (real keyword-overlap ATS
   scoring, never the AI's own self-report; the unhedged-claims check).
   ATS keyword extraction (drafting._extract_ats_keywords, otherwise a
   paid call and one of the four calls blocked when the spend cap is hit)
   is folded into this SAME subscription call rather than a second,
   separate one - the resume-drafting prompt asks for the posting's own
   required/preferred keywords in the same reply whenever this job doesn't
   have them cached yet, identical to feature/resume-reasoner-path's own
   reasoner_pipeline.draft_via_reasoner() - so steps 1-2 of Zahir's design
   really are one $0 subscription call, not two.

2. generate_questions_via_subscription() reuses tailoring.discuss_and_draft.
   _generate_questions_via_subscription() UNCHANGED (same real prompt/
   schema drafting.request_additional_gap_questions() already uses,
   already subscription-covered, already tested) - not a second
   near-duplicate implementation of the same question-generation logic.
   The caller renders the returned questions as an in-app form (see
   ui/app.py's render_subscription_qa_panel()) instead of posting them to
   the shared message board.

3. The user answers in-app; those answers save via drafting.
   save_gap_answers() (unchanged - the same store every other gap-answer
   flow in this app already writes to) and the caller calls
   run_subscription_round() again for a re-draft that picks up the newly
   confirmed facts (they're part of the profile dict re-serialized into
   every subscription prompt) and a fresh real ATS score.

The user decides when to stop (no automatic threshold, per Zahir's
design) - see ui/app.py's "Fire final API build" button, which fires
tailoring.bulk_generate.generate_for_job() (the existing, already-tested,
safety-gated paid pipeline - self-correction loop, DRAFT-UNCONFIRMED hedge
detection, claim verification, all reused as-is) exactly once, whenever
the user is satisfied with a subscription round's score.

Real, persisted state (applications.py's subscription_qa_status/
subscription_qa_round/subscription_ats_score_history/resume_
clarifying_questions - never bare Streamlit session_state, which vanishes
on reload) so the loop survives page reloads and a second browser tab sees
real progress, not silence, while a multi-minute subscription call is in
flight - matches this app's standing "not a black box" requirement.

Uses the SAME per-(source, job_id) generation lock
(applications.try_acquire_generation_lock/release_generation_lock) the
paid path already uses - a real, reachable race otherwise: a subscription
re-draft and a paid "Generate"/"Fire final API build" click for the exact
same job, close together, would both write resume_text with no ordering
guarantee and no warning, exactly the class of bug the paid path's own
2026-08-11 concurrent-generate fix already covers - this flow must not be
a second, unguarded way to hit the same shared field."""

import json

from search.job_store import update_job_ats_keywords
from tailoring.applications import (
    MAX_STALE_QA_AUTO_RETRIES,
    clear_stale_qa_status,
    get_application,
    increment_qa_stale_retry_count,
    record_subscription_qa_round,
    release_generation_lock,
    reset_qa_stale_retry_count,
    set_qa_status,
    try_acquire_generation_lock,
    upsert_application,
)
from tailoring.baseline_resume import select_baseline_resume_text
from tailoring.discuss_and_draft import _generate_questions_via_subscription
from tailoring.dossier import sync_workspace_documents
from tailoring.drafting import ATS_KEYWORDS_EXTRACTOR_VERSION, SYSTEM_PROMPT, _finalize_resume_draft, _resume_spec_for_job, save_gap_answers
from tailoring.reasoner_cli import ReasonerUnavailable, parse_json_reply, run_claude_cli


def _resume_prompt(job: dict, profile: dict) -> str:
    """Same resume-drafting instructions/schema the paid path's
    drafting._resume_schema() asks for (SYSTEM_PROMPT, _resume_spec_for_job
    - both reused unchanged, not rewritten), executed via the reasoner CLI
    instead. Asks for this posting's own ats_required_keywords/preferred_
    keywords in the SAME reply, but only when this job doesn't already
    have them cached - mirrors feature/resume-reasoner-path's reasoner_
    pipeline._resume_prompt() so a single subscription call covers both
    step 1 (draft) and step 2 (ATS keyword extraction) of Zahir's design,
    never a second call for the keywords alone."""
    need_keywords = job.get("ats_required_keywords") is None
    schema_lines = [
        '- "text": string - the resume itself. ' + _resume_spec_for_job(job),
        '- "target_seniority_at_least_vp": boolean - true only if the JOB POSTING ITSELF (not the candidate\'s past titles) is VP-level or higher (VP, SVP, EVP, President, C-suite/Chief-*-Officer); false otherwise.',
        '- "suggested_strategy_tag": string - a short (3-6 word) hyphenated label for what is distinctive about this draft\'s approach for this posting (e.g. "concise-2-page-ats-focused").',
        '- "clarifying_questions": array of objects {"type": "skill_gap"|"disqualifier_check", "skill": string, "question": string, "suggested_answer": string} - 3 to 10 genuine gaps a real fact would close; never invent an answer, ask instead; suggested_answer must read as a hedged guess, not a stated fact, or "" if you have no basis to propose one.',
        '- "unconfirmed_claims": array of objects {"skill": string, "text": string} - one entry for EVERY hedged, unconfirmed guess written into "text" with a trailing "?" (per the ground rules below); empty list if none.',
    ]
    if need_keywords:
        schema_lines.append(
            '- "ats_required_keywords": array of short (1-4 word) strings - this posting\'s own required/must-have terms, taken directly from its wording; empty array if none are genuinely stated.'
        )
        schema_lines.append(
            '- "ats_preferred_keywords": array of short (1-4 word) strings - this posting\'s own preferred/nice-to-have terms, taken directly from its wording; empty array if none are genuinely stated.'
        )
    return "\n\n".join([
        SYSTEM_PROMPT,
        "CANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str),
        "JOB POSTING:\n" + json.dumps(job, indent=2, default=str),
        (
            "Draft this candidate's resume for this job, following the ground rules and writing-voice rules above "
            "precisely and honestly. Before you finalize your answer, silently self-review the draft against the "
            "job posting's own required and preferred qualifications, and revise anything weak - you get exactly "
            "one reply, there is no second turn to fix it afterward, so do all of that checking now, within this "
            "same turn."
        ),
        "Reply with ONLY a single JSON object (no markdown code fence, no commentary before or after it) with exactly these keys:\n" + "\n".join(schema_lines),
    ])


def draft_resume_via_subscription(job: dict, profile: dict, on_progress=None) -> dict:
    """The subscription-path equivalent of drafting.generate_documents()
    for the resume alone - same finalized-dict shape ("text", "ats_score",
    "ats_rationale", "ats_next_actions", "clarifying_questions",
    "unconfirmed_claims", ...) because it's produced by the exact same
    tailoring.drafting._finalize_resume_draft() the paid path calls, not a
    re-implementation of its safety gates.

    on_progress(stage: str), if given, fires with real stage labels
    ("drafting resume", "scoring") as they happen - satisfies the standing
    "real incremental state, not a silent multi-minute wait" requirement,
    same as every other subscription call in this app.

    Raises ReasonerUnavailable if the `claude` CLI itself can't be invoked
    (not installed/not logged in), or RuntimeError on a genuine per-call
    failure (timeout, non-JSON reply, no JSON object found) - neither is
    swallowed."""
    def _progress(stage):
        if on_progress:
            on_progress(stage)

    _progress("drafting resume")
    reply = run_claude_cli(_resume_prompt(job, profile))
    data = parse_json_reply(reply)

    if job.get("ats_required_keywords") is None:
        required = data.get("ats_required_keywords") or []
        preferred = data.get("ats_preferred_keywords") or []
        job["ats_required_keywords"] = required
        job["ats_preferred_keywords"] = preferred
        job["ats_keywords_extractor_version"] = ATS_KEYWORDS_EXTRACTOR_VERSION
        if job.get("source") and job.get("job_id"):
            update_job_ats_keywords(job["source"], job["job_id"], required, preferred, ATS_KEYWORDS_EXTRACTOR_VERSION)

    _progress("scoring")
    return _finalize_resume_draft(data, job, profile)


def run_subscription_round(job: dict, profile: dict, on_progress=None) -> dict:
    """Runs one full subscription-covered draft round for this job -
    step 1+2 of Zahir's confirmed design (draft + ATS score, $0) - and
    persists the result the same way the paid path's bulk_generate.
    generate_for_job() does (upsert_application + sync_workspace_
    documents), plus records this round in subscription_ats_score_history
    via applications.record_subscription_qa_round().

    Acquires/releases the SAME generation lock the paid path uses (see
    this module's own top docstring for why) - a lock collision here means
    a paid Generate/Fire-final-build or another subscription round is
    already in flight for this exact job, not a failure of this round.

    Returns:
    - {"ok": True, "locked": False, "round": int, "resume_draft": dict,
      "error": None}
    - {"ok": False, "locked": True, "round": None, "resume_draft": None,
      "error": None} - lock collision, nothing was drafted.
    - {"ok": False, "locked": False, "round": None, "resume_draft": None,
      "error": str} - the subscription call itself failed (CLI missing/
      not logged in, timeout, malformed reply)."""
    source, job_id = job.get("source"), job.get("job_id")
    if not try_acquire_generation_lock(source, job_id):
        return {"ok": False, "locked": True, "round": None, "resume_draft": None, "error": None}
    set_qa_status(source, job_id, "drafting")
    try:
        try:
            resume_draft = draft_resume_via_subscription(job, profile, on_progress=on_progress)
        except (ReasonerUnavailable, RuntimeError) as exc:
            set_qa_status(source, job_id, "failed", error=str(exc))
            return {"ok": False, "locked": False, "round": None, "resume_draft": None, "error": str(exc)}

        app_record = get_application(source, job_id) or {}
        upsert_application(
            source, job_id, status=app_record.get("status", "under review"),
            resume_text=resume_draft["text"],
            resume_draft_source="subscription",
            resume_ats_score=resume_draft["ats_score"],
            resume_ats_rationale=resume_draft["ats_rationale"],
            resume_ats_next_actions=resume_draft["ats_next_actions"],
            resume_clarifying_questions=resume_draft["clarifying_questions"],
            suggested_strategy_tag=resume_draft["suggested_strategy_tag"],
            resume_unconfirmed_claims_ai_reported=resume_draft.get("unconfirmed_claims", []),
        )
        sync_workspace_documents(source, job_id, ["resume"], {"resume": resume_draft}, profile, job)
        round_number = record_subscription_qa_round(source, job_id, resume_draft["ats_score"])
        set_qa_status(source, job_id, None)
        # A real completed round means this job isn't actually stuck -
        # clear any stale-auto-retry history so a FUTURE, unrelated stall
        # gets its own fresh 2 attempts rather than inheriting whatever was
        # left over from a past incident (see reset_qa_stale_retry_count()'s
        # own docstring for why this is "2 in a row", not a lifetime cap).
        reset_qa_stale_retry_count(source, job_id)
        return {"ok": True, "locked": False, "round": round_number, "resume_draft": resume_draft, "error": None}
    finally:
        release_generation_lock(source, job_id)


def recover_stale_qa_and_retry(job: dict, profile: dict, on_progress=None) -> dict:
    """Real fix for the 2026-08-17 production incident: a job stuck at
    subscription_qa_status == "drafting" after the app process was killed
    mid-call, with no way to recover except a manual
    `applications.set_qa_status(source, job_id, None)` outside the UI.
    Bounded (CLAUDE.md "check for infinite loops", applied to retries, not
    just loops) auto-recovery for a job whose status is genuinely stale
    (applications.is_qa_status_stale() - active status held far longer than
    any real single subscription call takes): resets it and retries the
    draft/score round exactly once per call here, up to MAX_STALE_QA_AUTO_
    RETRIES total before requiring manual attention.

    Callers (the per-job Retry button once it detects a stale, not just a
    "failed", status; the basket-wide "Draft & score all" loop before it
    calls run_subscription_round() for each job) - never called
    unconditionally, only once the caller has already confirmed via is_qa_
    status_stale() that this job actually looks stuck, so a normal
    genuinely-in-flight draft is never touched.

    Returns the same shape as run_subscription_round() when it actually
    retries, plus "stale_recovered": True/False so the caller can show a
    real, visible "this looked stuck, retried automatically" message
    instead of it happening silently. Two other real outcomes:
    - retry cap already hit: sets subscription_qa_status to "failed" (so it
      surfaces through the SAME existing failed-job UI/Retry path, not a
      new one) with a message stating the real retry count and cap, and
      returns that as an ordinary failed outcome.
    - the status turned out not to be stale anymore by the time this ran
      (clear_stale_qa_status()'s own atomic re-check under lock) - the real
      call must have just finished, or another caller already recovered it
      in the gap between this caller's own check and this call. Reported as
      {"locked": True} (the same shape every OTHER genuine in-flight
      collision already returns) rather than fabricating a retry that could
      race the call that's actually still finishing."""
    source, job_id = job.get("source"), job.get("job_id")
    app_record = get_application(source, job_id) or {}
    retry_count = app_record.get("subscription_qa_stale_retry_count", 0)
    if retry_count >= MAX_STALE_QA_AUTO_RETRIES:
        msg = (
            f"Stalled while drafting/scoring and already auto-retried {retry_count} "
            f"time(s) (max {MAX_STALE_QA_AUTO_RETRIES}) - needs manual attention."
        )
        set_qa_status(source, job_id, "failed", error=msg)
        return {"ok": False, "locked": False, "round": None, "resume_draft": None, "error": msg, "stale_recovered": False}

    cleared = clear_stale_qa_status(source, job_id)
    if not cleared:
        return {"ok": False, "locked": True, "round": None, "resume_draft": None, "error": None, "stale_recovered": False}

    increment_qa_stale_retry_count(source, job_id)
    outcome = run_subscription_round(job, profile, on_progress=on_progress)
    outcome["stale_recovered"] = True
    return outcome


def generate_questions_via_subscription(job: dict, profile: dict, on_progress=None) -> dict:
    """Thin wrapper around tailoring.discuss_and_draft.
    _generate_questions_via_subscription() (reused unchanged - same real
    prompt/dedup logic, not a second implementation), persists the merged
    question list, and stamps subscription_qa_status for real progress
    visibility around the call. Returns that function's own
    {"added_count", "new_questions", "merged_clarifying_questions"} shape.

    Note: run_subscription_round() already returns a fresh
    clarifying_questions list from the resume-draft call itself (the same
    reply that drafted the resume also proposes gaps) - this function is
    for the EXPLICIT "check for more questions" case (mirroring the
    existing paid "Answer more questions" button), asking again given
    everything already asked/answered/covered, not the first round's
    questions (those already came back with run_subscription_round() and
    don't need a second call)."""
    source, job_id = job.get("source"), job.get("job_id")
    set_qa_status(source, job_id, "generating_questions")
    try:
        app_record = get_application(source, job_id) or {}
        try:
            result = _generate_questions_via_subscription(job, profile, app_record, on_progress=on_progress)
        except (ReasonerUnavailable, RuntimeError) as exc:
            set_qa_status(source, job_id, "failed", error=str(exc))
            raise
        upsert_application(
            source, job_id, status=app_record.get("status", "under review"),
            resume_clarifying_questions=result["merged_clarifying_questions"],
        )
        set_qa_status(source, job_id, "awaiting_answers" if result["new_questions"] else None)
        return result
    except (ReasonerUnavailable, RuntimeError):
        raise


def submit_answers_and_redraft(job: dict, profile: dict, answered_questions: list[dict], on_progress=None) -> dict:
    """Step 3 of Zahir's design: saves the user's real, confirmed in-app
    answers (drafting.save_gap_answers(), unchanged - never an invented
    answer, same discipline every other gap-answer flow already holds to)
    then re-drafts via run_subscription_round() so the new facts actually
    get incorporated into the next resume version and a fresh real ATS
    score. answered_questions is the subset the user actually typed a real
    answer for - blank ones are the caller's job to filter out first,
    matching save_gap_answers()'s own documented contract.

    Real bug caught while building this (same class already on record in
    CLAUDE.md - "verify against real state before reporting something as
    fact"): save_gap_answers() writes straight to the on-disk profile
    store, so the `profile` dict the CALLER already had in memory (e.g.
    loaded once at the top of a Streamlit rerun) does NOT reflect the
    answer just saved a few lines above. Passing that same stale dict
    into the re-draft would silently draft against the OLD profile,
    defeating the entire point of this function - the whole reason to
    redraft is to pick up the newly confirmed fact. This function reloads
    the profile itself, from disk, after saving, rather than trusting
    whatever `profile` the caller happened to pass in - `profile` is still
    accepted (for a consistent signature with the other functions here and
    so a caller with no live profile reference doesn't need one), but its
    value is never used for the actual re-draft call."""
    if on_progress:
        on_progress("applying your answers")
    save_gap_answers(job, answered_questions)
    from profile.storage import load_profile as _load_profile_fresh
    fresh_profile = _load_profile_fresh()
    return run_subscription_round(job, fresh_profile, on_progress=on_progress)
