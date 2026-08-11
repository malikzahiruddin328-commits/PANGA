"""Canonical skill/experience taxonomy (2026-08-11, Zahir's "know your
enemy" foundation - approved by General after a real audit found the
existing free-text skill labels genuinely drift across rounds for the
same real-world fact, e.g. "CSAT/NPS numeric scores on consulting
engagements" vs "Customer satisfaction scores on consulting engagements"
- no shared vocabulary at all, so skill_label_match.py's word-boundary
matching can never catch it).

This module is the deterministic data layer: load/save the taxonomy file
(same atomic-write convention as skills/lookup.py's role_skills.json - a
plain, unencrypted lookup table, not personal candidate data), and
find_canonical_id() for matching a free-text label against it (reused
by profile/interview.py's save_answer() and drafting.py's clarifying-
question generation, so both sides of the "have we asked/answered this
before" question go through the same real identity, not two different
heuristics).

The ONE genuinely AI-powered piece - clustering a pool of existing free-
text labels into an initial taxonomy - lives in
tailoring.taxonomy_migration (a separate module, kept apart from this
one so the taxonomy's own load/save/match logic has zero AI dependency
and stays trivially unit-testable)."""

import json
import os
import re
import tempfile
from pathlib import Path

from skill_label_match import skills_match

TAXONOMY_PATH = Path(__file__).resolve().parent / "canonical_skills.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def load_taxonomy() -> dict:
    if not TAXONOMY_PATH.exists():
        return {"_meta": {}}
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def save_taxonomy(data: dict) -> None:
    """Atomic write (temp file + os.replace), same pattern as skills/
    lookup.py's save_role_skills() - never leaves a half-written file
    for a crash or a concurrent reader to see."""
    fd, tmp_path = tempfile.mkstemp(dir=TAXONOMY_PATH.parent, prefix=".canonical_skills_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp_path, TAXONOMY_PATH)
    except BaseException:
        os.unlink(tmp_path)
        raise


def _categories(taxonomy: dict):
    return {k: v for k, v in taxonomy.items() if k != "_meta"}


def find_canonical_id(label: str, taxonomy: dict) -> str | None:
    """Returns the id of the taxonomy entry that best matches `label`
    (checked against its canonical_label and every stored alias via the
    app's existing skills_match() - normalized-equality or word-boundary
    substring), or None if nothing in the taxonomy covers it yet.

    Deterministic, no AI call - this is what both profile/interview.py's
    save path and drafting.py's clarifying-question generation use to
    check "is this a known concept" before ever proposing a new one."""
    if not label:
        return None
    for entries in _categories(taxonomy).values():
        for entry in entries:
            if skills_match(entry["canonical_label"], label):
                return entry["id"]
            for alias in entry.get("aliases", []):
                if skills_match(alias, label):
                    return entry["id"]
    return None


def _slugify(label: str, existing_ids: set[str]) -> str:
    base = _SLUG_RE.sub("_", label.lower()).strip("_")[:60] or "skill"
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def add_canonical_entry(taxonomy: dict, category: str, canonical_label: str, aliases: list[str] | None = None) -> str:
    """Adds a new canonical entry under `category`, OR - if an existing
    entry already covers this concept (checked via find_canonical_id
    against the canonical_label itself and every alias, same real check
    a caller should already have done before deciding to add) - merges
    the new aliases into that existing entry instead of creating a
    near-duplicate. Always returns the id actually holding this concept
    (new or pre-existing). Mutates `taxonomy` in place; caller saves."""
    aliases = aliases or []
    existing_id = find_canonical_id(canonical_label, taxonomy)
    if existing_id is None:
        for alias in aliases:
            existing_id = find_canonical_id(alias, taxonomy)
            if existing_id is not None:
                break

    if existing_id is not None:
        for entries in _categories(taxonomy).values():
            for entry in entries:
                if entry["id"] == existing_id:
                    existing_aliases = set(entry.get("aliases", []))
                    for alias in [canonical_label] + aliases:
                        if alias and alias != entry["canonical_label"]:
                            existing_aliases.add(alias)
                    entry["aliases"] = sorted(existing_aliases)
                    return existing_id

    all_ids = {e["id"] for entries in _categories(taxonomy).values() for e in entries}
    new_id = _slugify(canonical_label, all_ids)
    entries = taxonomy.setdefault(category, [])
    entries.append({"id": new_id, "canonical_label": canonical_label, "aliases": sorted(set(aliases))})
    return new_id
