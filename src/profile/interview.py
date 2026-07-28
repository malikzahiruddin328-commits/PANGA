"""Build step 2: gap-probing interview engine.

Compares the role/industry skill lookup table (src/skills/role_skills.json)
against the raw resume text to flag skills that are missing or only vaguely
implied. Producing the actual interview *questions* and interpreting nuanced
answers is reasoning work done by Claude directly (per PRD §11 LLM
architecture) — this module handles the mechanical parts: gap detection
against resume text, and persisting confirmed answers to the master profile.
"""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skills.lookup import skills_for  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "profile" / "raw"
MASTER_PROFILE_PATH = PROJECT_ROOT / "data" / "profile" / "structured" / "master_profile.json"


def _resume_text() -> str:
    manifest_result = json.loads((RAW_DIR / "manifest_result.json").read_text(encoding="utf-8"))
    chunks = []
    for entry in manifest_result:
        if entry["category"] == "resume":
            chunks.append(Path(PROJECT_ROOT / entry["extracted_to"]).read_text(encoding="utf-8"))
    return "\n".join(chunks).lower()


def _already_answered(skill: str) -> bool:
    if not MASTER_PROFILE_PATH.exists():
        return False
    profile = json.loads(MASTER_PROFILE_PATH.read_text(encoding="utf-8"))
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
    profile = json.loads(MASTER_PROFILE_PATH.read_text(encoding="utf-8"))
    profile.setdefault("gap_interview_answers", []).append({
        "skill": skill,
        "role_context": role_context,
        "answer": answer,
        "date_captured": date_captured,
    })
    MASTER_PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")


if __name__ == "__main__":
    for gap in detect_gaps("Lifesciences/Pharma", "Head of IT / CIO"):
        print(f"- {gap['skill']}  ({gap['why_it_matters']})")
