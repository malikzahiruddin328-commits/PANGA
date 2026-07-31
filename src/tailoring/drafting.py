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
- Write in a natural, confident, professional voice - not generic AI phrasing, not stuffed with buzzwords.
- Return ONLY the documents requested via the structured output schema. No extra commentary."""


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
        },
        "required": ["text", "ats_score", "ats_rationale", "ats_next_actions"],
        "additionalProperties": False,
    }


def _draft_one(client: "anthropic.Anthropic", shared_context: list[dict], doc_key: str, model: str | None):
    schema = _resume_schema() if doc_key == "resume" else _schema([doc_key])
    try:
        response = client.messages.create(
            model=model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
            max_tokens=6000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": shared_context + [{"type": "text", "text": f"\n\nDraft: {doc_key}"}],
            }],
        )
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
    If given, on_progress(i, total, doc_key) is called right before drafting
    starts on the i-th (1-indexed) of `total` documents.
    Returns {doc_key: drafted_text}, except "resume" maps to
    {"text": ..., "ats_score": int, "ats_rationale": str, "ats_next_actions":
    [...]} instead of a plain string, since the resume is ATS-scored against
    this posting as part of the same drafting pass. Raises
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
    for i, doc_key in enumerate(doc_keys, start=1):
        if on_progress:
            on_progress(i, len(doc_keys), doc_key)
        results[doc_key] = _draft_one(client, shared_context, doc_key, model)
    return results
