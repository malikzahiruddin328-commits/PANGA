"""Reads/writes the local master profile file. Encryption is backlogged (see docs/job-search-automation-prd.md §7)."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MASTER_PROFILE_PATH = PROJECT_ROOT / "data" / "profile" / "structured" / "master_profile.json"


def load_profile() -> dict:
    return json.loads(MASTER_PROFILE_PATH.read_text(encoding="utf-8"))


def save_profile(profile: dict) -> None:
    MASTER_PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
