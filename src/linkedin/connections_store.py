"""Local encrypted store for parsed LinkedIn connections (PRD §16b). Full
replace on each upload (Zahir re-exports and re-uploads periodically),
same as linkedin/storage.py's save_snapshot() for the profile PDF -
connections data goes stale, there's no reason to keep merging old rows.
Encrypted at rest (PRD §7) via security.crypto_store - this is PII about
other people (Zahir's connections), not just Zahir himself.
"""
from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONNECTIONS_PATH = PROJECT_ROOT / "data" / "linkedin" / "connections.json"


def load_connections_snapshot() -> dict:
    return read_json(CONNECTIONS_PATH, default={"connections": [], "source_file": None, "last_saved": None})


def save_connections(connections: list[dict], source_file: str, saved_at: str) -> None:
    write_json(CONNECTIONS_PATH, {
        "connections": connections,
        "source_file": source_file,
        "last_saved": saved_at,
    })
