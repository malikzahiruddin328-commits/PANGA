"""Build step: one-click document drafting via a direct Anthropic API call
(Zahir's explicit choice 2026-07-30, over the alternative of a background
Claude Code task - see PRD §11 discussion). This is a deliberate exception
to "Python only orchestrates, Claude reasons live via a Claude Code
conversation" - every other judgment-requiring feature in this app (fit
score, LinkedIn scoring, Prospector Score, interview prep) still follows
that pattern. Drafting needed a synchronous in-app result, which only a
direct API call can give.

Reads the key from .env (ANTHROPIC_API_KEY), same pattern as
search/usajobs.py's USAJOBS_API_KEY. Requires Zahir to get his own key from
console.anthropic.com - this module never creates or manages the key itself.
"""

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_MODEL = "claude-opus-5"

RESUME_SPEC = (
    "Tailored resume text for this specific job, written to be ATS-perfect - "
    "it must parse cleanly in an Applicant Tracking System, not just read "
    "well to a human. Plain text only - no markdown (no **, no #, no "
    "markdown bullets, no tables). Use standard, literal section headers "
    "(e.g. PROFESSIONAL EXPERIENCE, EDUCATION, SKILLS, CERTIFICATIONS) that "
    "ATS parsers recognize - not creative or merged headers. Reverse "
    "chronological order, consistent 'Month YYYY - Month YYYY' date format. "
    "Contact info as plain text at the very top, not in a table or header/"
    "footer. Naturally work in the exact keywords and skill terms the job "
    "posting itself uses (its required/preferred qualifications, tools, "
    "certifications) wherever the candidate genuinely has that experience - "
    "ATS systems match on literal keyword overlap, not paraphrases. Spell "
    "out acronyms at first use with the acronym in parentheses so both "
    "keyword matching and human readers catch it (e.g. 'Master Data "
    "Management (MDM)'). If this posting is a US federal government job "
    "(USAJOBS or similar), follow federal resume conventions: full "
    "chronological work history with month/year date ranges, hours worked "
    "per week, and specific, quantifiable accomplishments for each role - "
    "federal resumes are longer and more detailed than private-sector ones, "
    "there is no one-page limit. Otherwise write a concise, "
    "achievement-focused resume. Use plain '- ' dashes for bullet points, "
    "blank lines between sections."
)

DOC_SPECS = {
    "resume": RESUME_SPEC,
    "cover_letter": (
        "Cover letter body text only - no letterhead, no date, no address "
        "block, no 'Dear Hiring Manager' salutation formatting beyond a plain "
        "greeting line. Plain text, no markdown. 3-5 paragraphs explaining "
        "specifically why this candidate fits this role."
    ),
    "exec_bio": (
        "Third-person executive biography, roughly 150-250 words, suitable "
        "for a board packet or conference bio. Plain text, no markdown."
    ),
    "leadership_summary": (
        "First-person leadership philosophy / summary statement tailored to "
        "what this specific role and organization value, roughly 150-250 "
        "words. Plain text, no markdown."
    ),
}

SYSTEM_PROMPT = """You are drafting real job-application documents for a real candidate applying to a real job. Accuracy matters - these documents may be submitted as-is.

Ground rules:
- Only use employers, titles, dates, degrees, certifications, and accomplishments that are actually present in the candidate's master profile provided below. Never invent or embellish facts, metrics, employers, or credentials that aren't there.
- If the job posting calls for a qualification the profile doesn't clearly evidence, do not fabricate it - either omit it or honestly bridge from the closest real, transferable experience in the profile.
- Tailor every document specifically to this job posting and organization - reference the actual role, organization name, and what the posting emphasizes. Do not write generic, could-apply-to-any-job text.
- Return ONLY the documents requested via the structured output schema. No extra commentary.

Writing voice - this must read as a real senior executive's own writing, not AI output:
- British English prose conventions - phrasing, idiom, and a measured, understated register - but American spellings throughout (e.g. "color" not "colour", "organize" not "organise", "center" not "centre"), since this is for US employers.
- Vary sentence length and structure line to line; never fall into a uniform rhythm.
- Do not use these overused AI-writing tells: corporate buzzwords (leverage, spearhead, synergy, robust, cutting-edge, seamless, dynamic, passionate, game-changer); repetitive three-item lists; formulaic openers ("In today's fast-paced environment...", "I am thrilled to apply..."); "not just X, but Y" constructions; excessive em dashes; and stacking multiple adjectives before a noun.
- Every claim should sound like something this specific person would actually say about his own work - concrete, specific, a little understated rather than oversold."""


class DraftingNotConfigured(Exception):
    pass


class DraftingFailed(Exception):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client() -> "anthropic.Anthropic":
    if not is_configured():
        raise DraftingNotConfigured(
            "ANTHROPIC_API_KEY must be set in .env for one-click document "
            "drafting. Get a key at console.anthropic.com, then add it to "
            "the .env file in the Panga folder (copy the same line USAJOBS_API_KEY "
            "uses) and restart the app."
        )
    return anthropic.Anthropic()


SCORE_SYSTEM_PROMPT = """You are scoring how well one job posting fits this specific candidate, for his personal job-search tool. Read the candidate's master profile below and reason genuinely about fit - never a keyword count.

Consider:
- Seniority match: this candidate is executive/leadership level (25+ years, CIO/Head of IT background), not an individual-contributor or hands-on technical role.
- Domain/functional match: IT/technology/data leadership vs. unrelated fields (clinical/medical, legal, HR, finance, military operations, food service, construction, sales, etc.) - a senior-sounding title alone does not mean relevance.
- Non-US locations count against fit somewhat (relocation/visa impractical) unless the posting is explicitly remote.
- The candidate has explicitly said he does NOT consider himself qualified for CISO-titled or other specialized security-officer-titled roles (e.g. SISO, "IT Security Officer"), despite broader cybersecurity oversight experience - score these LOW regardless of subject-matter proximity, this is a real disqualifier, not just a preference.
- Roles requiring current military/National Guard membership (e.g. "Title 32" postings) score 0 regardless of IT relevance - not eligible.
- Entry-level/intern/student/recent-graduate programs score 0 regardless of subject matter - wrong seniority.
- Staffing-firm or retained-executive-search-firm postings should be scored on the underlying role itself, same criteria as any direct posting.

Assign a 0-100 fit_score and a one-sentence, specific, plain-language fit_rationale explaining why (not generic filler) via the structured output schema."""


def _score_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "fit_score": {"type": "integer", "description": "0-100 fit score for this candidate against this posting."},
            "fit_rationale": {"type": "string", "description": "One sentence, specific to this role, plain language."},
        },
        "required": ["fit_score", "fit_rationale"],
        "additionalProperties": False,
    }


def score_job(job: dict, profile: dict, model: str | None = None) -> dict:
    """Live-scores a single job against the master profile via the direct
    API - for jobs that need an immediate score outside the daily scheduled
    task or a live Claude Code conversation (namely, jobs added manually via
    the Results tab intake form, which would otherwise sit invisible - the
    Results tab hides any job with no fit_score at all). Mirrors the exact
    rubric panga-daily-job-search's SKILL.md step 5 already uses, so scores
    stay comparable across the app rather than following a different rubric.
    Returns {"fit_score": int, "fit_rationale": str}. Raises
    DraftingNotConfigured/DraftingFailed same as generate_documents()."""
    client = _client()
    content = (
        "JOB POSTING:\n" + json.dumps(job, indent=2, default=str) +
        "\n\nCANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str)
    )
    try:
        response = client.messages.create(
            model=model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": _score_schema()}},
            system=SCORE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIStatusError as exc:
        raise DraftingFailed(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise DraftingFailed("Couldn't reach the Claude API - check your internet connection.") from exc

    if response.stop_reason == "refusal":
        raise DraftingFailed("Claude declined to score this job. Try again.")
    if response.stop_reason == "max_tokens":
        raise DraftingFailed("The response was cut off before finishing. Try again.")

    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if not text_block:
        raise DraftingFailed("Claude returned no score.")

    try:
        data = json.loads(text_block)
    except json.JSONDecodeError as exc:
        raise DraftingFailed("Claude's response wasn't valid - try again.") from exc

    return {"fit_score": data["fit_score"], "fit_rationale": data["fit_rationale"]}


def _schema(doc_keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": "string", "description": DOC_SPECS[k]} for k in doc_keys},
        "required": doc_keys,
        "additionalProperties": False,
    }


def _resume_schema() -> dict:
    # The resume gets a richer schema than the other doc types: alongside
    # the text itself, Claude self-assesses how well that exact text would
    # score in a real ATS keyword/structure match against this posting -
    # same "score + why + how to raise it" shape as Prospector Score and
    # LinkedIn's profile-strength score elsewhere in this app, computed in
    # the same pass so the assessment is grounded in the text actually
    # produced, not a separate guess.
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": RESUME_SPEC},
            "ats_score": {
                "type": "integer",
                "description": (
                    "0-100 estimate of how well THIS resume text would score when "
                    "parsed by a typical Applicant Tracking System and matched "
                    "against this specific job posting - keyword/skill overlap "
                    "with the posting's stated requirements, standard parseable "
                    "structure, title alignment. Be honest, not generous."
                ),
            },
            "ats_rationale": {
                "type": "string",
                "description": "1-3 sentences: what matched well, what's weak or missing.",
            },
            "ats_next_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "2-5 concrete, specific actions that would raise the score - "
                    "e.g. a specific keyword/skill from the posting to add IF the "
                    "candidate's real profile supports it, or honestly note that a "
                    "gap can't be closed without real experience they don't have."
                ),
            },
            "clarifying_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "Short label for this gap (e.g. 'SK Life Science IT team size/budget'), matching the master profile's gap-tracking convention.",
                        },
                        "question": {
                            "type": "string",
                            "description": "A direct, specific question whose answer is a genuine, checkable fact (a number, a name, a date) that would close this gap - not vague or stylistic.",
                        },
                    },
                    "required": ["skill", "question"],
                    "additionalProperties": False,
                },
                "description": (
                    "0-5 specific, directly answerable questions for real facts "
                    "this resume is currently missing that would raise the score "
                    "if the candidate actually has them - never invent the answer "
                    "yourself, ask instead. Only include questions a real number/"
                    "name/date could answer. Skip anything already answered "
                    "elsewhere in the profile or that's purely about wording/"
                    "structure rather than a missing fact."
                ),
            },
        },
        "required": ["text", "ats_score", "ats_rationale", "ats_next_actions", "clarifying_questions"],
        "additionalProperties": False,
    }


def _draft_one(
    client: "anthropic.Anthropic",
    shared_context: list[dict],
    doc_key: str,
    model: str | None,
    on_progress=None,
    doc_index: int = 1,
    doc_total: int = 1,
):
    schema = _resume_schema() if doc_key == "resume" else _schema([doc_key])
    # The resume schema carries the text itself plus ats_score/rationale/
    # next_actions/clarifying_questions, and federal-format resumes alone
    # can run 3000+ tokens - give it real headroom rather than truncating
    # (hit for real during testing at 6000 with a federal-length resume).
    max_tokens = 20000 if doc_key == "resume" else 6000
    try:
        with client.messages.stream(
            model=model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": shared_context + [{"type": "text", "text": f"\n\nDraft: {doc_key}"}],
            }],
        ) as stream:
            # One level deeper than "which document is drafting" (Zahir's
            # ask): surface the thinking->writing transition and a live,
            # throttled character count from the raw stream, so the progress
            # bar keeps moving DURING a single document's call, not just
            # between calls. The streamed text is the raw structured-output
            # JSON as it's built, not the final prose - a character count is
            # still an honest, real progress signal, just not a literal
            # preview of the finished text.
            char_count = 0
            last_reported = 0
            for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "thinking":
                    if on_progress:
                        on_progress(doc_index, doc_total, doc_key, "thinking...")
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    char_count += len(event.delta.text)
                    if on_progress and char_count - last_reported >= 150:
                        on_progress(doc_index, doc_total, doc_key, f"writing... ({char_count:,} characters so far)")
                        last_reported = char_count
            response = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        raise DraftingFailed(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise DraftingFailed("Couldn't reach the Claude API - check your internet connection.") from exc

    if response.stop_reason == "refusal":
        raise DraftingFailed(
            "Claude declined to draft this document. This is unusual for resume "
            "content - try again, or check the job posting text for anything unusual."
        )
    if response.stop_reason == "max_tokens":
        raise DraftingFailed("The response was cut off before finishing. Try again.")

    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if not text_block:
        raise DraftingFailed("Claude returned no draftable text.")

    try:
        data = json.loads(text_block)
    except json.JSONDecodeError as exc:
        raise DraftingFailed("Claude's response wasn't valid - try again.") from exc

    if doc_key == "resume":
        return {
            "text": data.get("text", ""),
            "ats_score": data.get("ats_score"),
            "ats_rationale": data.get("ats_rationale", ""),
            "ats_next_actions": data.get("ats_next_actions", []),
            "clarifying_questions": data.get("clarifying_questions", []),
        }
    return data.get(doc_key, "")


def generate_documents(
    job: dict,
    profile: dict,
    doc_keys: list[str],
    model: str | None = None,
    on_progress=None,
) -> dict:
    """Drafts real, tailored document text for exactly the requested doc_keys
    (subset of "resume", "cover_letter", "exec_bio", "leadership_summary"),
    one API call per document type so progress is real, not simulated. The
    job+profile context is identical across those calls and marked
    cacheable, so only the first call pays full price for it - subsequent
    ones in the same batch read it back at ~10% cost.
    If given, on_progress(i, total, doc_key, substatus=None) is called right
    before drafting starts on the i-th (1-indexed) of `total` documents
    (substatus=None), then repeatedly during that document's own generation
    with a live sub-status ("thinking...", "writing... (N characters so
    far)") as the response streams in.
    Returns {doc_key: drafted_text}, except "resume" maps to {"text": ...,
    "ats_score": int, "ats_rationale": str, "ats_next_actions": [...],
    "clarifying_questions": [{"skill": ..., "question": ...}]} instead of a
    plain string, since the resume is ATS-scored against this posting as
    part of the same drafting pass - clarifying_questions are gaps Claude
    couldn't close honestly without more real facts (never invented; ask,
    don't fabricate - see profile/interview.py's save_answer(), the same
    mechanism this feeds back into via the Results tab). Raises
    DraftingNotConfigured if no API key is set, DraftingFailed on
    refusal/truncation/API error."""
    if not doc_keys:
        return {}

    client = _client()
    shared_context = [{
        "type": "text",
        "text": (
            "JOB POSTING:\n" + json.dumps(job, indent=2, default=str) +
            "\n\nCANDIDATE'S MASTER PROFILE:\n" + json.dumps(profile, indent=2, default=str)
        ),
        "cache_control": {"type": "ephemeral"},
    }]

    results = {}
    total = len(doc_keys)
    for i, doc_key in enumerate(doc_keys, start=1):
        if on_progress:
            on_progress(i, total, doc_key)
        results[doc_key] = _draft_one(client, shared_context, doc_key, model, on_progress, i, total)
    return results


def save_gap_answers(job: dict, answers: dict[str, str]) -> None:
    """Persists confirmed answers to a resume's clarifying_questions into the
    master profile's gap_interview_answers - the same store/shape
    profile/interview.py's save_answer() already writes to, so a fact
    confirmed here (e.g. "SK Life Science IT team size/budget") becomes
    available to every future job's drafting, not just this one. answers
    maps each question's "skill" label to the candidate's typed answer;
    blank/whitespace-only answers are skipped rather than saved as empty
    facts. Never called with an invented answer - the Results tab UI only
    calls this with what Zahir actually typed."""
    from datetime import date

    from profile.interview import save_answer

    role_context = f"{job.get('title', 'Unknown role')} at {job.get('organization', 'Unknown organization')}"
    today = date.today().isoformat()
    for skill, answer in answers.items():
        if answer and answer.strip():
            save_answer(skill=skill, role_context=role_context, answer=answer.strip(), date_captured=today)
