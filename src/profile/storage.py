"""Reads/writes the local master profile file, encrypted at rest (PRD §7)."""

from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MASTER_PROFILE_PATH = PROJECT_ROOT / "data" / "profile" / "structured" / "master_profile.json"


def load_profile() -> dict:
    return read_json(MASTER_PROFILE_PATH, default={})


def save_profile(profile: dict) -> None:
    write_json(MASTER_PROFILE_PATH, profile)
