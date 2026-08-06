"""Reads/writes the local master profile file, encrypted at rest (PRD §7)."""

from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MASTER_PROFILE_PATH = PROJECT_ROOT / "data" / "profile" / "structured" / "master_profile.json"


def load_profile() -> dict:
    return read_json(MASTER_PROFILE_PATH, default={})


def save_profile(profile: dict) -> None:
    write_json(MASTER_PROFILE_PATH, profile)


def update_profile_field(key: str, value) -> None:
    """Merges a single field into the master profile under the same
    locked("master_profile") critical section interview.save_answer() uses
    (2026-08-06 - this was the one multi-writer JSON store in the app that
    had never adopted the locking pattern job_store.py/applications.py/
    cta_emails.py all already use) - closes the read-modify-write race this
    function's own docstring used to only "narrow" (a fresh load() right
    before save still lets two concurrent callers both read-then-write and
    silently drop one update) rather than eliminate."""
    with locked("master_profile"):
        profile = load_profile()
        profile[key] = value
        save_profile(profile)
