"""Build step 2: gap-probing interview engine.

Compares the role/industry skill lookup table (src/skills/role_skills.json)
against the raw resume text to flag skills that are missing or only vaguely
implied. Producing the actual interview *questions* and interpreting nuanced
answers is reasoning work done by Claude directly (per PRD §11 LLM
architecture) — this module handles the mechanical parts: gap detection
against resume text, and persisting confirmed answers to the master profile.
"""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skills.lookup import skills_for  # noqa: E402
from profile.storage import MASTER_PROFILE_PATH, load_profile, save_profile  # noqa: E402
from profile.ingest import resume_text as _shared_resume_text  # noqa: E402
from security.file_lock import locked  # noqa: E402
from skill_label_match import skills_match  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "profile" / "raw"


def _resume_text() -> str:
    return _shared_resume_text().lower()


def _already_answered(skill: str) -> bool:
    if not MASTER_PROFILE_PATH.exists():
        return False
    profile = load_profile()
    return any(skills_match(a["skill"], skill) for a in profile.get("gap_interview_answers", []))


def detect_gaps(industry: str, role: str) -> list[dict]:
    """Returns role_skills entries that don't clearly appear in the resume text
    and haven't already been answered — i.e. worth asking about."""
    text = _resume_text()
    gaps = []
    for entry in skills_for(industry, role):
        keyword = entry["skill"].split("(")[0].split("/")[0].strip().lower()
        if keyword not in text and not _already_answered(entry["skill"]):
            gaps.append(entry)
    return gaps


def save_answer(
    skill: str, role_context: str, answer: str, date_captured: str,
    question: str = "", is_disqualifier: bool = False,
) -> None:
    """is_disqualifier marks this as a real, candidate-confirmed exclusion
    (a role type they've said they don't want/aren't qualified for despite
    otherwise-matching experience) rather than an ordinary skill-gap
    confirmation - drafting.SCORE_SYSTEM_PROMPT reads this flag and scores
    matching postings low regardless of subject-matter proximity.

    Updates the existing entry in place (matched via skill_label_match's
    normalized/word-boundary comparison, not exact string equality - real
    gap flagged by Mirror 2026-08-08: an exact match against a free-text,
    AI-generated label silently fails the moment the model phrases the
    same underlying fact differently across rounds, e.g. "AWS" vs "cloud
    infrastructure" - see skill_label_match.py's module docstring) if this
    skill was already answered before, rather than appending a duplicate -
    real bug fixed 2026-08-06 while building the "view/update a previously
    answered question" UI (Zahir's ask): answering the same skill_gap
    question again across rounds used to silently pile up duplicate,
    potentially conflicting entries for the same skill instead of
    reflecting the current, latest confirmed answer. question is the
    original clarifying_questions wording (optional - older callers/entries
    may not have one), stored so the answered-questions view can show what
    was actually asked, not just the short skill label.

    Wrapped in the same locked("master_profile") pattern every other
    multi-writer JSON store in this app already uses (job_store.py,
    applications.py, cta_emails.py, ...) - this module was the one store
    that had never adopted it despite being written from multiple places
    (this function, profile/storage.py's update_profile_field()), a real
    read-modify-write race this fixes while already in this exact
    function, not a new one introduced by it."""
    with locked("master_profile"):
        profile = load_profile()
        answers = profile.setdefault("gap_interview_answers", [])
        for entry in answers:
            if skills_match(entry["skill"], skill):
                entry["role_context"] = role_context
                entry["answer"] = answer
                entry["date_captured"] = date_captured
                entry["is_disqualifier"] = is_disqualifier
                if question:
                    entry["question"] = question
                save_profile(profile)
                return
        answers.append({
            "skill": skill,
            "role_context": role_context,
            "answer": answer,
            "date_captured": date_captured,
            "is_disqualifier": is_disqualifier,
            "question": question,
        })
        save_profile(profile)


if __name__ == "__main__":
    for gap in detect_gaps("Lifesciences/Pharma", "Head of IT / CIO"):
        print(f"- {gap['skill']}  ({gap['why_it_matters']})")
