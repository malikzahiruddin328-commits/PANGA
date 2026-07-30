"""Local encrypted-at-rest store for interview prep (PRD §10/§13): one record
per job that's reached interview stage, holding a list of rounds (phone
screen, panel, final, etc.). Producing the actual interviewer research and
persona-aware questions is reasoning + live web-search work done by Claude
directly (same split as tailor.py/interview.py) - this module only handles
the mechanical parts: starting a round, and persisting the generated content
back to it.

Only two writers ever touch this file: the Streamlit UI (start_round(), when
the user clicks "Prep for this interview") and a live Claude Code
conversation (save_round(), once research + drafting is done) - both
user-paced, not concurrent scheduled tasks, so this follows the same
unlocked load-modify-save convention as applications.py rather than the
tighter race handling cta_emails.py needs against its 4x/day + 10-min
scheduled tasks.
"""

from pathlib import Path

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
