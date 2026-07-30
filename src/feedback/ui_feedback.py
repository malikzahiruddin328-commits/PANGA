"""Local JSON-backed store for "point and talk" UI feedback (Zahir's request
2026-07-30): a note tagged to whichever tab/section it's about, recorded by
voice or typed, so a later Claude Code session can read exactly what to
change instead of Zahir typing out a UI change request from scratch.

Encrypted at rest via security.crypto_store, same as every other store in
data/ (PRD §7).
"""

import uuid
from datetime import datetime
from pathlib import Path

from security.crypto_store import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FEEDBACK_PATH = PROJECT_ROOT / "data" / "ui_feedback" / "ui_feedback.json"


def load_feedback() -> list[dict]:
    return read_json(FEEDBACK_PATH, default=[])


def _save_all(entries: list[dict]) -> None:
    write_json(FEEDBACK_PATH, entries)


def add_feedback(section: str, note: str) -> str:
    entries = load_feedback()
    entry_id = uuid.uuid4().hex[:12]
    entries.append({
        "id": entry_id,
        "section": section,
        "note": note,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "resolved": False,
    })
    _save_all(entries)
    return entry_id


def get_open_feedback() -> list[dict]:
    open_entries = [e for e in load_feedback() if not e.get("resolved")]
    return sorted(open_entries, key=lambda e: e.get("created_at") or "", reverse=True)


def mark_resolved(feedback_id: str) -> None:
    entries = load_feedback()
    for e in entries:
        if e["id"] == feedback_id:
            e["resolved"] = True
    _save_all(entries)


def delete_feedback(feedback_id: str) -> None:
    # Filter-and-resave, not blank-and-resave - see project memory on why
    # _save_all(whole list) makes that distinction matter.
    _save_all([e for e in load_feedback() if e["id"] != feedback_id])
