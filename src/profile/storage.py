"""Reads/writes the local master profile file, encrypted at rest (PRD §7)."""

from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MASTER_PROFILE_PATH = PROJECT_ROOT / "data" / "profile" / "structured" / "master_profile.json"


def load_profile() -> dict:
    return read_json(MASTER_PROFILE_PATH, default={})


def save_profile(profile: dict) -> None:
    write_json(MASTER_PROFILE_PATH, profile)


def update_profile_field(key: str, value) -> None:
    """Merges a single field into the master profile via a fresh load()
    taken immediately before the save, rather than reusing a profile dict
    read earlier at render time. Narrows the window in which a concurrent
    writer (e.g. interview.save_answer()) could have its update silently
    overwritten by a stale full-profile round trip."""
    profile = load_profile()
    profile[key] = value
    save_profile(profile)
