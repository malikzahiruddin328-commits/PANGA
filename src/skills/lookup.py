"""Industry/role -> skill lookup table (PRD §4). Starts as a flat JSON file
(src/skills/role_skills.json) and grows over time as new roles/industries are
encountered — populated by reasoning (Claude), not a fixed rule set.
"""

import json
import os
import re
import tempfile
from pathlib import Path

# Real personal data - which industries/roles have been probed and what
# skills matter for them is derived from this candidate's own real target
# roles, same category as profile/storage.py's master_profile.json - so
# it lives under data/ like every other personal store, not tracked in
# git (2026-08-11, moved out after being git-tracked from initial build;
# see canonical_taxonomy.py's TAXONOMY_PATH for the same fix applied
# there, and the same commit's history note about already-committed
# content).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "data" / "skills" / "role_skills.json"


def load_role_skills() -> dict:
    # Real consequence of moving this file under gitignored data/: unlike
    # before (seeded via git on every checkout), a fresh checkout or a
    # worktree with no data/ directory of its own genuinely has no file
    # here yet. Same graceful-missing-file default as
    # canonical_taxonomy.load_taxonomy() - an empty table (no industries
    # known yet), not a crash.
    if not DATA_PATH.exists():
        return {}
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _normalize(value: str) -> str:
    folded = value.casefold()
    folded = re.sub(r"\s*/\s*", "/", folded)  # "X / Y" and "X/Y" are the same key
    return " ".join(folded.split())


def _resolve_key(container: dict, key: str) -> str:
    """Returns the existing key in `container` that matches `key` case/
    whitespace-insensitively, if one exists - so minor LLM formatting drift
    across generations (e.g. "Financial Services / Banking" vs "Financial
    services/Banking") updates the existing entry instead of silently
    creating a near-duplicate. Falls back to `key` unchanged (preserving
    its original casing) when nothing matches yet."""
    normalized = _normalize(key)
    for existing in container:
        if _normalize(existing) == normalized:
            return existing
    return key


def skills_for(industry: str, role: str) -> list[dict]:
    data = load_role_skills()
    roles = data.get(_resolve_key(data, industry), {})
    return roles.get(_resolve_key(roles, role), [])


def save_role_skills(industry: str, role: str, entries: list[dict]) -> None:
    """Writes/overwrites the title-ladder entry for one industry/role pair -
    called when live reasoning generates a fresh ladder for a vertical not
    yet in this file (or refreshes an existing one). Extends the file in
    place; never touches other industries/roles."""
    data = load_role_skills()
    roles = data.setdefault(_resolve_key(data, industry), {})
    roles[_resolve_key(roles, role)] = entries

    # Atomic write (temp file + os.replace) so a crash or concurrent reader
    # never sees a half-written file - this store just got its first runtime
    # writer (Settings tab's generate_target_roles()) after being static.
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_PATH.parent, prefix=".role_skills_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp_path, DATA_PATH)
    except BaseException:
        os.unlink(tmp_path)
        raise
