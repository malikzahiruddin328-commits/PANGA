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
