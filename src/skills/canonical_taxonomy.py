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

from security.file_lock import locked
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


def resolve_or_create_canonical_id(label: str, category: str, aliases: list[str] | None = None) -> str:
    """Real, cross-process-safe entry point for the live one-at-a-time
    case (profile/interview.py's save path, hit for real every time
    Zahir answers a gap question) - found and fixed 2026-08-11 (RM
    caught this live, mid-merge, while Zahir was actively interviewing
    through a separate concurrent session): load_taxonomy()/
    add_canonical_entry()/save_taxonomy() called separately with no
    locking is a genuine read-modify-write race - two concurrent callers
    (e.g. two live interview sessions, or a live interview racing a
    migration/backfill script) can both read the same old taxonomy, both
    decide to add their own new entry, and whichever calls save_taxonomy()
    second silently overwrites the first caller's addition with no error.
    master_profile.json's own writes were already protected by
    locked("master_profile") - this file had no equivalent.

    Wraps the whole load -> find-or-create -> save sequence in
    locked("canonical_taxonomy") (security.file_lock's real, cross-
    process advisory lock, same primitive master_profile.json's own
    writes already use) so the check-then-create is genuinely atomic -
    no other process can interleave between this call's read and its
    write. Every caller should go through this function (or
    run_locked_bulk_mutation() below for a batch of many labels in one
    critical section) rather than calling load_taxonomy()/save_taxonomy()
    directly for a mutation - those two stay available for read-only
    callers (find_canonical_id() consumers like drafting.py's clarifying-
    question dedup), which don't need the lock since save_taxonomy()'s
    atomic temp-file+os.replace write already guarantees a reader never
    sees a half-written file."""
    with locked("canonical_taxonomy"):
        taxonomy = load_taxonomy()
        canonical_id = find_canonical_id(label, taxonomy)
        if canonical_id is None:
            canonical_id = add_canonical_entry(taxonomy, category, label, aliases=aliases)
            save_taxonomy(taxonomy)
        return canonical_id


def run_locked_bulk_mutation(mutate_fn):
    """Same real cross-process safety as resolve_or_create_canonical_id()
    above, for a batch operation that needs to load once, mutate many
    times in memory, and save once as a single critical section (e.g. a
    migration/backfill pass over many labels) rather than one lock
    acquisition per label. `mutate_fn(taxonomy)` mutates the taxonomy
    dict in place and may return a result; that result (plus the final
    taxonomy) is returned to the caller. Every real migration/backfill
    script must go through this rather than calling load_taxonomy()/
    save_taxonomy() directly around its own loop - the exact gap RM
    caught live 2026-08-11."""
    with locked("canonical_taxonomy"):
        taxonomy = load_taxonomy()
        result = mutate_fn(taxonomy)
        save_taxonomy(taxonomy)
        return taxonomy, result
