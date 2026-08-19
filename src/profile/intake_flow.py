"""profile.intake_flow (2026-08-19, feature/resume-jd-intake-redesign) -
Zahir's confirmed, thrice-explained design for a real, one-time, upfront
intake, replacing the standing complaint that Panga's per-job clarifying
questions read as generic keyword lookups instead of "a skilled
interviewer who's actually read the resume and the target JDs." His own
words: "end goal is after the initial intake there should be absolutely no
new questions ... the number of questions will be very less."

Five real steps (his spec, verbatim, 2026-08-19), each a function here:

1. Full ingestion, not a summary - generate_role_examples() and
   generate_probing_questions() both read profile.ingest.all_documents_text()
   (every ingested document, every category - see that function's own
   docstring for why this is a deliberately wider cut than resume_text()),
   never a pre-summarized version.
2a. generate_role_examples() - one reasoner call proposing example role/
    title candidates from the full docs + whatever profile facts already
    exist, for the user to confirm/reject in the UI (ui/app.py's Intake
    tab). Confirming or rejecting each one simultaneously reveals real
    industry/sector preference, not just title matching - the UI records
    both the confirmed titles AND which ones were rejected.
2b. sample_jds_for_role() / sample_jds_for_confirmed_roles() - for each
    CONFIRMED role, pulls a real sample of live job postings (target
    DEFAULT_JD_SAMPLE_TARGET = 10, a judgment call - see that function's own
    docstring for why 10 and what happens when a niche role can't reach it)
    via the SAME direct-scrape board fetchers scoring/drafting already use
    (search.boards.fetch_dice_jobs/fetch_dice_job_description/
    fetch_built_in_jobs/fetch_simplyhired_jobs) - no new JD-fetch pipeline,
    no paid search API (Adzuna/aggregators.py is deliberately NOT used here,
    to avoid spending any part of its own daily call-budget cap on this
    flow - see this module's cost-sizing note in the PR description).
3. generate_probing_questions() - ONE reasoner call grounded in (full docs +
   confirmed roles + the real JD sample text pulled in step 2b) inferring a
   likely answer for each real gap, rather than a blind keyword list - the
   candidate confirms/edits the inference in the UI rather than answering
   from a blank box, per Zahir's explicit "infer likely answers ... the
   candidate confirms/corrects" instruction.
4. The UI (ui/app.py's Intake tab) is the "one continuous conversational
   flow, one thread, a visible todo/progress list" piece of the spec (his
   own reference: Claude's "Design" planning-tool interaction model) - this
   module supplies the backend calls the UI's session-state-tracked wizard
   steps through, it doesn't render anything itself.
5. save_intake_probing_answers() persists confirmed facts through
   profile.interview.save_answer() - the SAME store/canonical-id-resolution/
   locking every other gap-answer path in this app already uses (never a
   second, parallel "already known" mechanism). This is also the entire
   mechanism by which "after intake, no new questions" becomes true in
   practice: tailoring.drafting.py's per-job clarifying-question generation
   already reads profile["gap_interview_answers"] into
   previously_answered_skills and skips anything matching (drafting.py
   lines ~728/2006/2742), and skills.gap_frequency_analysis.
   analyze_recurring_gaps() already excludes any canonical_skill_id already
   present there (already_answered_ids) - both existing suppression checks
   apply automatically the moment this module calls save_answer(), with NO
   changes needed to either module. See tests/test_intake_flow_suppression.py
   for the integration test proving this end-to-end rather than just
   asserting it in a docstring.

Real, lightweight audit trail only - record_intake_completed() persists a
compact summary (date, confirmed/rejected role titles, per-role JD sample
counts, question count) to profile["intake_history"], never the raw JD
text itself (that would bloat master_profile.json, an AES-256-GCM-
encrypted store re-read/re-written on every profile write across this
whole app, with content this flow only ever needs transiently during one
session - see sample_jds_for_confirmed_roles()'s own docstring)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from profile.ingest import all_documents_text
from profile.interview import save_answer
from profile.storage import load_profile, update_profile_field
from search.boards import (
    fetch_built_in_jobs,
    fetch_dice_job_description,
    fetch_dice_jobs,
    fetch_simplyhired_jobs,
)
from tailoring.reasoner_cli import ReasonerUnavailable, parse_json_reply, run_claude_cli

# Judgment call (Zahir's brief explicitly delegated this): 8 example roles
# is enough real breadth to reveal genuine sector/title preference (the
# spec's own stated purpose for step 2a) without turning one intake screen
# into an unreadable wall of checkboxes - a candidate confirming/rejecting
# 8 real, resume-grounded titles is a fast, legible step; 20+ would not be.
DEFAULT_ROLE_EXAMPLE_COUNT = 8

# Zahir's own spec number ("at least 10 real job descriptions ... a sample
# large enough to see common points ... and the nuances/outliers").
DEFAULT_JD_SAMPLE_TARGET = 10

# Circuit breaker (CLAUDE.md "check for infinite loops", applied to a
# scrape loop hunting for enough real postings): a genuinely niche role
# (e.g. a narrow technical specialty in a small market) may never yield 10
# real postings across all 3 boards - this bounds how many raw candidate
# postings this function will ever look at for ONE role before giving up
# with whatever real sample it found, rather than looping indefinitely or
# hammering a board with ever-larger limit= values.
MAX_JD_CANDIDATES_PER_ROLE = 40

# Bounds how much raw text one job description contributes to a reasoner
# prompt - a real posting's full text can run several thousand characters;
# uncapped, DEFAULT_JD_SAMPLE_TARGET x (up to a handful of confirmed roles)
# postings could push a single prompt into the tens of thousands of tokens
# needlessly (see this module's own cost-sizing note). 1500 chars keeps the
# real substance (responsibilities/requirements) most postings front-load,
# without needing per-posting summarization (another reasoner call this
# flow deliberately avoids).
MAX_JD_TEXT_CHARS_IN_PROMPT = 1500

# Same reasoning as MAX_JD_TEXT_CHARS_IN_PROMPT, applied to the combined
# resume+supporting-docs text - real profiles observed in this codebase run
# up to ~98,000 characters (see reasoner_cli.run_claude_cli's own docstring
# on the Windows argv-length bug that number came from); capped here so one
# intake prompt can't balloon unboundedly on an unusually document-heavy
# profile. A cap this size still comfortably holds a full multi-page resume
# plus several supporting documents in practice.
MAX_DOCS_TEXT_CHARS = 40000

# Judgment call: caps how many probing questions one intake round surfaces,
# matching this repo's existing "rank and cap, never dump an exhaustive
# list on the user" convention (tailoring.subscription_resume_qa.
# MAX_QUESTIONS_PER_ROUND applies the same instinct per-job-round; this is
# the once-per-intake equivalent, deliberately larger since this is meant
# to be the one real round that makes later per-job rounds close to empty).
MAX_INTAKE_QUESTIONS = 15


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


# --- Step 2a: role/title examples ---

_ROLE_EXAMPLES_SYSTEM_PROMPT = (
    "You are a skilled career advisor who has just read this candidate's full resume and every "
    "supporting document on file. Propose a real, grounded list of specific job titles/role types this "
    "candidate could genuinely apply for right now, based on their actual demonstrated experience - not "
    "generic titles, not aspirational titles they have no real basis for. For each one, briefly say why "
    "(which real part of their background supports it). Include a genuine range - their most obvious next "
    "title, plus a few adjacent/lateral options across different industries or company types where their "
    "underlying skills would transfer - so confirming or rejecting these also reveals their real sector "
    "preference, not just title matching."
)


def _role_examples_prompt(docs_text: str, profile: dict, count: int) -> str:
    existing_titles = [r.get("title") for r in (profile.get("intake_confirmed_roles") or []) if r.get("title")]
    parts = [
        _ROLE_EXAMPLES_SYSTEM_PROMPT,
        "CANDIDATE'S FULL RESUME AND SUPPORTING DOCUMENTS:\n" + _truncate(docs_text, MAX_DOCS_TEXT_CHARS),
    ]
    if existing_titles:
        parts.append(
            "Roles already confirmed in an earlier intake round (do not repeat these, propose genuinely "
            "different ones):\n" + json.dumps(existing_titles, indent=2)
        )
    parts.append(
        f"Propose exactly {count} example roles. Reply with ONLY a single JSON object (no markdown code "
        "fence, no commentary before or after it) with exactly this key:\n"
        '- "example_roles": array of objects {"title": string (a specific, real job title), "why": string '
        "(1-2 sentences grounding this in the candidate's actual real experience)}"
    )
    return "\n\n".join(parts)


def generate_role_examples(profile: dict | None = None, count: int = DEFAULT_ROLE_EXAMPLE_COUNT) -> list[dict]:
    """One $0 subscription-CLI reasoner call (never the paid API - see
    tailoring.reasoner_cli's own docstring for why this is the mechanism
    every LLM call in this app is required to reuse). Returns
    [{"title": str, "why": str}, ...], length <= count (the reasoner is
    ASKED for exactly `count`; this trims defensively rather than trusting
    it, the same "AI output feeding a downstream loop needs a real cap, not
    just a prompt instruction" principle this codebase applies everywhere
    else). Raises ReasonerUnavailable/RuntimeError on a genuine failure -
    never swallowed, this is the FIRST real step of the flow, so a silent
    empty-list fallback here would just look like "nothing to confirm" to
    the user rather than a real, actionable error."""
    profile = profile if profile is not None else load_profile()
    docs_text = all_documents_text()
    reply = run_claude_cli(_role_examples_prompt(docs_text, profile, count))
    data = parse_json_reply(reply)
    examples = data.get("example_roles") or []
    cleaned = []
    for item in examples:
        title = (item.get("title") or "").strip()
        if title:
            cleaned.append({"title": title, "why": (item.get("why") or "").strip()})
    return cleaned[:count]


def save_confirmed_roles(confirmed_titles: list[str], rejected_titles: list[str] | None = None) -> None:
    """Persists the user's real yes/no decisions on the example roles (step
    2a) - both sides, not just the confirmed list, since a rejected title is
    itself real signal about industry/sector preference the spec explicitly
    calls out ("simultaneously reveals their real industry/sector
    preference"). Merges into any roles already confirmed in an earlier
    intake round rather than overwriting - a second intake pass (e.g. after
    new documents are uploaded) adds to the candidate's real confirmed-role
    set, it doesn't erase a prior real confirmation."""
    profile = load_profile()
    existing = profile.get("intake_confirmed_roles") or []
    existing_titles = {r["title"] for r in existing}
    now = datetime.now(timezone.utc).isoformat()
    merged = list(existing)
    for title in confirmed_titles:
        title = (title or "").strip()
        if title and title not in existing_titles:
            merged.append({"title": title, "confirmed_at": now})
            existing_titles.add(title)
    update_profile_field("intake_confirmed_roles", merged)

    if rejected_titles:
        existing_rejected = profile.get("intake_rejected_roles") or []
        existing_rejected_titles = {r["title"] for r in existing_rejected}
        merged_rejected = list(existing_rejected)
        for title in rejected_titles:
            title = (title or "").strip()
            if title and title not in existing_rejected_titles:
                merged_rejected.append({"title": title, "rejected_at": now})
                existing_rejected_titles.add(title)
        update_profile_field("intake_rejected_roles", merged_rejected)


# --- Step 2b: real JD sampling per confirmed role ---


def sample_jds_for_role(role_title: str, target_count: int = DEFAULT_JD_SAMPLE_TARGET) -> list[dict]:
    """Pulls up to target_count real, live postings for role_title from the
    SAME direct-scrape board fetchers (Dice/Built In/SimplyHired) the daily
    job search already uses - no new fetch pipeline. Dice is tried first
    because it's the only one of the three with a real full-JD-text fetcher
    (fetch_dice_job_description() - Built In/SimplyHired's search-result
    cards carry no inline description, confirmed in boards.py's own module
    docstrings); Built In/SimplyHired only backfill the sample toward
    target_count when Dice alone can't reach it, and their entries are
    marked "thin_text": True (title/organization/location only, no real JD
    body) so generate_probing_questions() can weight them appropriately
    rather than treating them as equally grounded evidence.

    Bounded by MAX_JD_CANDIDATES_PER_ROLE total raw postings looked at
    across all three boards - a genuinely niche role may never reach
    target_count; this returns whatever real sample it found rather than
    looping/retrying indefinitely (this module's own docstring explains
    why - CLAUDE.md's "check for infinite loops" applied to a scrape loop).

    Each per-board fetch is individually try/except-wrapped - one board
    being down or blocking scrapers (a real, observed failure mode for
    these direct-scrape fetchers) must not take out the other two or the
    whole sampling step; a board that fails contributes zero postings and
    is silently skipped, same "one item's failure shouldn't stop the rest"
    principle CLAUDE.md calls out for reasoner_cli.parse_json_reply().

    No paid API involved anywhere in this function - deliberately does NOT
    use search.aggregators.fetch_adzuna_jobs (Adzuna), which is gated by its
    own persisted daily call-budget cap shared with the real daily job
    search; spending any of that budget on this flow's own JD sampling
    would eat into a shared, separately-tracked resource for a different
    purpose than the one it exists for."""
    samples: list[dict] = []
    seen_keys = set()
    candidates_examined = 0

    def _add(job: dict, description: str | None, thin: bool):
        key = (job.get("source"), job.get("job_id"))
        if key in seen_keys:
            return False
        seen_keys.add(key)
        samples.append({
            "source": job.get("source"),
            "title": job.get("title"),
            "organization": job.get("organization"),
            "location": job.get("location"),
            "posting_url": job.get("posting_url"),
            "description": description or "",
            "thin_text": thin,
        })
        return True

    # Dice first - the only board with a real full-text fetcher.
    try:
        dice_jobs = fetch_dice_jobs(role_title, limit=min(MAX_JD_CANDIDATES_PER_ROLE, target_count * 2))
    except Exception:
        dice_jobs = []
    for job in dice_jobs:
        if len(samples) >= target_count or candidates_examined >= MAX_JD_CANDIDATES_PER_ROLE:
            break
        candidates_examined += 1
        description = None
        if job.get("posting_url"):
            try:
                description = fetch_dice_job_description(job["posting_url"])
            except Exception:
                description = None
        _add(job, description, thin=not bool(description))

    # Backfill toward target_count from Built In / SimplyHired only if Dice
    # alone didn't reach it - both have no full-text fetcher, so every entry
    # they contribute is marked thin_text.
    for fetcher in (fetch_built_in_jobs, fetch_simplyhired_jobs):
        if len(samples) >= target_count or candidates_examined >= MAX_JD_CANDIDATES_PER_ROLE:
            break
        try:
            extra_jobs = fetcher(role_title, limit=min(MAX_JD_CANDIDATES_PER_ROLE, target_count * 2))
        except Exception:
            extra_jobs = []
        for job in extra_jobs:
            if len(samples) >= target_count or candidates_examined >= MAX_JD_CANDIDATES_PER_ROLE:
                break
            candidates_examined += 1
            _add(job, None, thin=True)

    return samples


def sample_jds_for_confirmed_roles(
    role_titles: list[str], target_count: int = DEFAULT_JD_SAMPLE_TARGET,
) -> dict[str, list[dict]]:
    """Runs sample_jds_for_role() for every confirmed role. Returns
    {role_title: [sample, ...]}. Deliberately not persisted to the profile
    store by this function - see this module's own top docstring for why
    (raw JD text is transient prompt context for THIS intake session, not a
    permanent profile fact; record_intake_completed() below persists only
    the compact counts). The caller (ui/app.py's Intake tab) is expected to
    hold this dict in st.session_state for the remainder of the flow."""
    return {title: sample_jds_for_role(title, target_count) for title in role_titles}


# --- Step 3: grounded probing questions ---

_PROBING_QUESTIONS_SYSTEM_PROMPT = (
    "You are a skilled interviewer who has read this candidate's full resume and supporting documents, "
    "the specific roles they've confirmed real interest in, and a real sample of live job postings for "
    "each of those roles. Your job is to identify the genuine, real gaps between what the candidate's own "
    "documents show and what these actual postings commonly require or prefer - looking across the whole "
    "sample for what's COMMON (not a one-off from a single posting) as well as real, notable outliers worth "
    "asking about. For each real gap, propose a likely answer the candidate can confirm or correct, inferred "
    "from context in their own documents (an adjacent skill, a similar project, a related tool) - never "
    "invent a fact with no basis; if you truly have no basis for a guess, leave the inferred answer blank "
    "and mark confidence low. Never ask about anything already clearly evidenced in the candidate's own "
    "documents. Ask real, specific questions the way an interviewer who actually read everything would - "
    "never a bare keyword echoed back as a yes/no prompt."
)


def _jd_samples_block(jd_samples_by_role: dict[str, list[dict]]) -> str:
    lines = []
    for role, samples in jd_samples_by_role.items():
        lines.append(f"--- Real job postings sampled for confirmed role \"{role}\" ({len(samples)} postings) ---")
        for s in samples:
            desc = _truncate(s.get("description") or "(no full text available for this posting)", MAX_JD_TEXT_CHARS_IN_PROMPT)
            lines.append(
                f"* {s.get('title')} at {s.get('organization')} ({s.get('source')}):\n{desc}"
            )
    return "\n\n".join(lines)


def _probing_questions_prompt(
    docs_text: str, profile: dict, confirmed_roles: list[str], jd_samples_by_role: dict[str, list[dict]],
) -> str:
    already_answered = [
        {"skill": a.get("skill"), "answer": a.get("answer")}
        for a in (profile.get("gap_interview_answers") or [])
        if a.get("skill")
    ]
    parts = [
        _PROBING_QUESTIONS_SYSTEM_PROMPT,
        "CANDIDATE'S FULL RESUME AND SUPPORTING DOCUMENTS:\n" + _truncate(docs_text, MAX_DOCS_TEXT_CHARS),
        "CONFIRMED ROLES OF INTEREST:\n" + json.dumps(confirmed_roles, indent=2),
        "REAL SAMPLED JOB POSTINGS FOR THESE ROLES:\n" + _jd_samples_block(jd_samples_by_role),
    ]
    if already_answered:
        parts.append(
            "Facts already confirmed in an earlier round (never ask about any of these again, even "
            "reworded):\n" + json.dumps(already_answered, indent=2)
        )
    parts.append(
        f"Propose at most {MAX_INTAKE_QUESTIONS} real, distinct probing questions, ranked with the most "
        "important/highest-impact gaps first. Reply with ONLY a single JSON object (no markdown code fence, "
        "no commentary before or after it) with exactly this key:\n"
        '- "questions": array of objects {"skill": string (short label for the underlying fact/skill), '
        '"question": string (the real, specific interview-style question), "inferred_answer": string (a '
        "hedged, editable guess at the real answer grounded in the candidate's documents, or \"\" if there "
        'is no real basis for one), "confidence": "high"|"medium"|"low" (how confident the inference is)}'
    )
    return "\n\n".join(parts)


def generate_probing_questions(
    profile: dict | None,
    confirmed_roles: list[str],
    jd_samples_by_role: dict[str, list[dict]],
) -> list[dict]:
    """Step 3 of the spec: ONE reasoner call over (full docs + confirmed
    roles + real JD sample) inferring likely answers rather than a blind
    keyword list. Returns [{"skill","question","inferred_answer",
    "confidence"}, ...], capped defensively to MAX_INTAKE_QUESTIONS (same
    "don't trust the prompt cap alone" discipline as
    generate_role_examples()). Raises ReasonerUnavailable/RuntimeError on a
    genuine failure - never swallowed, same reasoning as
    generate_role_examples()."""
    profile = profile if profile is not None else load_profile()
    docs_text = all_documents_text()
    reply = run_claude_cli(_probing_questions_prompt(docs_text, profile, confirmed_roles, jd_samples_by_role))
    data = parse_json_reply(reply)
    questions = data.get("questions") or []
    cleaned = []
    for q in questions:
        skill = (q.get("skill") or "").strip()
        question = (q.get("question") or "").strip()
        if skill and question:
            confidence = q.get("confidence") if q.get("confidence") in ("high", "medium", "low") else "low"
            cleaned.append({
                "skill": skill,
                "question": question,
                "inferred_answer": (q.get("inferred_answer") or "").strip(),
                "confidence": confidence,
            })
    return cleaned[:MAX_INTAKE_QUESTIONS]


# --- Step 5: persist confirmed facts ---

INTAKE_ROLE_CONTEXT = "Upfront resume+JD-grounded intake (2026-08-19 redesign)"


def save_intake_probing_answers(answered_questions: list[dict]) -> int:
    """Persists the user's real, confirmed-or-edited answers through
    profile.interview.save_answer() - the SAME store/canonical-id/locking
    mechanism every other gap-answer path in this app already uses (see
    this module's own top docstring for why this alone is what makes
    per-job question suppression work automatically, with no changes
    needed to drafting.py or gap_frequency_analysis.py).

    answered_questions: the subset the user actually confirmed a real
    answer for (a confirmed inferred_answer, or their own edited/typed
    text) - blank ones are the caller's job to filter out first, matching
    save_gap_answers()'s own documented contract elsewhere in this app.
    Each item: {"skill":, "answer":, "question":}. Returns the count of
    answers actually saved."""
    today = datetime.now(timezone.utc).date().isoformat()
    saved = 0
    for q in answered_questions:
        answer = (q.get("answer") or "").strip()
        skill = (q.get("skill") or "").strip()
        if not answer or not skill:
            continue
        save_answer(
            skill=skill, role_context=INTAKE_ROLE_CONTEXT, answer=answer,
            date_captured=today, question=q.get("question", ""), is_disqualifier=False,
        )
        saved += 1
    return saved


def record_intake_completed(
    confirmed_roles: list[str], rejected_roles: list[str], jd_sample_counts: dict[str, int], questions_saved: int,
) -> None:
    """Appends one compact, auditable record to profile["intake_history"] -
    date, which roles were confirmed/rejected, how many real postings were
    actually sampled per role, and how many facts got saved - never the raw
    JD text itself (see this module's own top docstring for why). Lets the
    UI (and Zahir) see real intake history without re-deriving it from
    gap_interview_answers' role_context strings alone."""
    profile = load_profile()
    history = profile.get("intake_history") or []
    history.append({
        "date": datetime.now(timezone.utc).isoformat(),
        "confirmed_roles": list(confirmed_roles),
        "rejected_roles": list(rejected_roles),
        "jd_sample_counts": dict(jd_sample_counts),
        "questions_saved": questions_saved,
    })
    update_profile_field("intake_history", history)
