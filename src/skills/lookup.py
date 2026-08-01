"""Industry/role -> skill lookup table (PRD §4). Starts as a flat JSON file
(src/skills/role_skills.json) and grows over time as new roles/industries are
encountered — populated by reasoning (Claude), not a fixed rule set.
"""

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "role_skills.json"


def load_role_skills() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def skills_for(industry: str, role: str) -> list[dict]:
    data = load_role_skills()
    return data.get(industry, {}).get(role, [])


def save_role_skills(industry: str, role: str, entries: list[dict]) -> None:
    """Writes/overwrites the title-ladder entry for one industry/role pair -
    called when live reasoning generates a fresh ladder for a vertical not
    yet in this file (or refreshes an existing one). Extends the file in
    place; never touches other industries/roles."""
    data = load_role_skills()
    data.setdefault(industry, {})[role] = entries
    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
