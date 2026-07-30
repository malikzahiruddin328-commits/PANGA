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
from security.crypto_store import read_json, read_text  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "profile" / "raw"


def _resume_text() -> str:
    manifest_result = read_json(RAW_DIR / "manifest_result.json", default=[])
    chunks = []
    for entry in manifest_result:
        if entry["category"] == "resume":
            chunks.append(read_text(PROJECT_ROOT / entry["extracted_to"]))
    return "\n".join(chunks).lower()


def _already_answered(skill: str) -> bool:
    if not MASTER_PROFILE_PATH.exists():
        return False
    profile = load_profile()
    return any(a["skill"] == skill for a in profile.get("gap_interview_answers", []))


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


def save_answer(skill: str, role_context: str, answer: str, date_captured: str) -> None:
    profile = load_profile()
    profile.setdefault("gap_interview_answers", []).append({
        "skill": skill,
        "role_context": role_context,
        "answer": answer,
        "date_captured": date_captured,
    })
    save_profile(profile)


if __name__ == "__main__":
    for gap in detect_gaps("Lifesciences/Pharma", "Head of IT / CIO"):
        print(f"- {gap['skill']}  ({gap['why_it_matters']})")
