"""Local encrypted-at-rest store for interview prep (PRD §10/§13): one record
per job that's reached interview stage, holding a list of rounds (phone
screen, panel, final, etc.). start_round()/save_round() handle the
mechanical parts: starting a round, and persisting generated content back
to it.

The actual interviewer research and persona-aware question drafting is now
generate_prep() (native-packaging branch, 2026-07-31) - a direct Anthropic
API call, same conversion as tailoring/drafting.py's document generation:
one call with the web-search server tool to research the company/
interviewers, then one structured call to turn that research plus the
job+profile context into company_snapshot/interviewers/likely_questions/
questions_to_ask. The Streamlit UI calls generate_prep() then save_round()
with its result - no live Claude Code conversation needed anymore.

Only the Streamlit UI ever touches this file (start_round()/save_round()
both called from app.py now) - user-paced, not a concurrent scheduled task,
so this follows the same unlocked load-modify-save convention as
applications.py rather than the tighter race handling cta_emails.py needs
against its 4x/day + 10-min scheduled tasks.
"""

import json
from pathlib import Path

from llm_client import call_structured, call_with_web_search, get_client
from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERVIEW_PREP_PATH = PROJECT_ROOT / "data" / "applications" / "interview_prep.json"


def load_interview_prep() -> list[dict]:
    return read_json(INTERVIEW_PREP_PATH, default=[])


def _save_all(records: list[dict]) -> None:
    write_json(INTERVIEW_PREP_PATH, records)


def get_interview_prep(source: str, job_id: str) -> dict | None:
    for record in load_interview_prep():
        if record["source"] == source and record["job_id"] == job_id:
            return record
    return None


def _get_or_create_record(records: list[dict], source: str, job_id: str) -> dict:
    for record in records:
        if record["source"] == source and record["job_id"] == job_id:
            return record
    record = {"source": source, "job_id": job_id, "rounds": []}
    records.append(record)
    return record


def _get_or_create_round(record: dict, round_label: str) -> dict:
    for round_ in record["rounds"]:
        if round_["round_label"] == round_label:
            return round_
    round_ = {
        "round_label": round_label,
        "date": None,
        "format": None,
        "interviewers": [],
        "company_snapshot": None,
        "likely_questions": [],
        "questions_to_ask": [],
        "status": "in_progress",
        "outcome": None,
        "outcome_notes": None,
    }
    record["rounds"].append(round_)
    return round_


def _write_dossier(source: str, job_id: str) -> None:
    # Lazy import - dossier.py reads from this module, so importing it at
    # the top would be circular. Safe here since both modules are fully
    # loaded by the time any of these functions actually runs.
    from tailoring.dossier import write_dossier
    write_dossier(source, job_id)


def record_round_outcome(source: str, job_id: str, round_label: str, outcome: str, notes: str | None = None) -> None:
    """PRD §16d/§17: how the round actually went, self-reported by Zahir -
    the ONLY way this can ever be known, there's no automated signal for
    it. outcome is one of "went well", "went okay", "went poorly" (no
    fixed enum enforced, just the convention the UI offers). Feeds the
    Learn Engine's interview-prep-approach-vs-outcome input; added 2026-07-30,
    doesn't exist on rounds created before then."""
    records = load_interview_prep()
    round_ = _get_or_create_round(_get_or_create_record(records, source, job_id), round_label)
    round_["outcome"] = outcome
    round_["outcome_notes"] = notes
    _save_all(records)
    _write_dossier(source, job_id)


def start_round(
    source: str,
    job_id: str,
    round_label: str,
    date: str | None = None,
    format: str | None = None,
    interviewers: list[dict] | None = None,
) -> None:
    """Creates the round if it doesn't exist yet - idempotent if "Prep for
    this interview" is clicked twice for the same round_label, it just
    updates logistics rather than duplicating. interviewers entries look like
    {"name", "title", "found_via": "email" | "manual"} - this is only what's
    known before research; save_round() below fills in each interviewer's
    research_summary/persona once Claude has looked them up."""
    records = load_interview_prep()
    round_ = _get_or_create_round(_get_or_create_record(records, source, job_id), round_label)
    if date is not None:
        round_["date"] = date
    if format is not None:
        round_["format"] = format
    if interviewers is not None:
        round_["interviewers"] = interviewers
    _save_all(records)
    _write_dossier(source, job_id)


def save_round(
    source: str,
    job_id: str,
    round_label: str,
    interviewers: list[dict] | None = None,
    company_snapshot: str | None = None,
    likely_questions: list[dict] | None = None,
    questions_to_ask: list[dict] | None = None,
    status: str | None = None,
) -> None:
    """Claude calls this once research + persona-aware question drafting for
    a round is done. Fields left as None don't overwrite previously saved
    values (same convention as applications.upsert_application()).
    interviewers is replaced wholesale rather than merged, since by this
    point Claude holds the full researched list, including anyone added
    since start_round(). likely_questions/questions_to_ask are lists of
    dicts - see docs/job-search-automation-prd.md §10 for the field shape."""
    records = load_interview_prep()
    round_ = _get_or_create_round(_get_or_create_record(records, source, job_id), round_label)
    if interviewers is not None:
        round_["interviewers"] = interviewers
    if company_snapshot is not None:
        round_["company_snapshot"] = company_snapshot
    if likely_questions is not None:
        round_["likely_questions"] = likely_questions
    if questions_to_ask is not None:
        round_["questions_to_ask"] = questions_to_ask
    if status is not None:
        round_["status"] = status
    _save_all(records)
    _write_dossier(source, job_id)


def build_prep_context(job: dict, profile: dict | None = None) -> dict:
    """Bundles a job record (from job_store) with the master profile into the
    context Claude needs to research interviewers and draft persona-aware
    prep for it - same shape as tailor.build_tailoring_context(). Claude can
    pull role/industry skill gaps itself via skills.lookup during the
    conversation, same as it does for tailoring; no need to duplicate that
    lookup mechanically here."""
    from profile.storage import load_profile

    return {
        "job": job,
        "profile": profile if profile is not None else load_profile(),
    }


_RESEARCH_SYSTEM_PROMPT = """Research a company and its interviewers ahead of a real job interview. Search the web for: the company's recent news/products/priorities (for a company snapshot), and for each named interviewer, their professional background and likely focus/persona in the interview (e.g. "technical depth", "culture fit", "budget/ROI"). If an interviewer isn't found or no interviewers are named yet, just cover the company. Never invent a fact you can't find - say so plainly if a specific interviewer isn't findable. Reply as plain findings notes (not a final answer format) - another pass will structure this into the final prep content."""

_PREP_SCHEMA = {
    "type": "object",
    "properties": {
        "company_snapshot": {"type": "string", "description": "2-4 sentences: what's current/relevant about the company right now, grounded in the research notes."},
        "interviewers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "research_summary": {"type": "string", "description": "What the research found about this person - honest 'not much found' if that's the truth."},
                    "persona": {"type": "string", "description": "Likely interview focus/style, e.g. 'technical depth', 'culture fit', 'budget/ROI'."},
                    "research_links": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "title", "research_summary", "persona", "research_links"],
                "additionalProperties": False,
            },
        },
        "likely_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "asked_by": {"type": "string", "description": "Which interviewer is likely to ask this, or '' if unclear."},
                    "why": {"type": "string", "description": "Why this question is likely, given the role/company/interviewer."},
                    "talking_point": {"type": "string", "description": "A grounded talking point from the candidate's real profile - never an invented accomplishment."},
                },
                "required": ["question", "asked_by", "why", "talking_point"],
                "additionalProperties": False,
            },
            "description": "5-10 persona-aware likely questions for this role.",
        },
        "questions_to_ask": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "best_for": {"type": "string", "description": "Which interviewer/round this question best suits, or '' if any."},
                },
                "required": ["question", "best_for"],
                "additionalProperties": False,
            },
            "description": "3-6 genuinely useful questions the candidate could ask them.",
        },
    },
    "required": ["company_snapshot", "interviewers", "likely_questions", "questions_to_ask"],
    "additionalProperties": False,
}


def generate_prep(job: dict, profile: dict, interviewers: list[dict] | None = None, on_progress=None) -> dict:
    """Researches the company/interviewers via a direct-API web-search call,
    then structures that research plus the job+profile context into prep
    content via a second direct-API call. Returns {"company_snapshot": str,
    "interviewers": [...], "likely_questions": [...], "questions_to_ask":
    [...]} - same shape save_round() expects. Raises LLMNotConfigured/
    LLMCallFailed (via llm_client)."""
    client = get_client()
    interviewer_names = ", ".join(
        f"{i.get('name')} ({i['title']})" if i.get("title") else i.get("name")
        for i in (interviewers or []) if i.get("name")
    ) or "not yet known"

    research_notes, _cost = call_with_web_search(
        client,
        system=_RESEARCH_SYSTEM_PROMPT,
        user_content=f"Company: {job.get('organization')}\nRole: {job.get('title')}\nInterviewers: {interviewer_names}",
        max_tokens=1500,
        max_uses=6,
    )

    content = json.dumps({
        "job": job, "profile": profile, "interviewers": interviewers or [], "research_notes": research_notes,
    }, indent=2, default=str)
    return call_structured(
        client,
        system="Turn the research notes and job+profile context below into structured interview prep content, via the schema.",
        user_content=content,
        schema=_PREP_SCHEMA,
        max_tokens=4000,
        effort="high",
        on_progress=on_progress,
        refusal_message="Claude declined to generate interview prep. Try again.",
    )
