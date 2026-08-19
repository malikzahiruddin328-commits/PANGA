"""Canonical skill/experience taxonomy (2026-08-11, Zahir's "know your
enemy" foundation - approved by General after a real audit found the
existing free-text skill labels genuinely drift across rounds for the
same real-world fact, e.g. "CSAT/NPS numeric scores on consulting
engagements" vs "Customer satisfaction scores on consulting engagements"
- no shared vocabulary at all, so skill_label_match.py's word-boundary
matching can never catch it).

This module is the deterministic data layer: load/save the taxonomy file
(same atomic-write convention as skills/lookup.py's role_skills.json -
both real personal data now, see TAXONOMY_PATH below), and
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
from skill_label_match import normalize_skill_label, skills_match

# Real personal data (labels/aliases are derived from the candidate's own
# real answers - e.g. "CSAT/NPS numeric scores on consulting engagements"
# is a real fragment of a real answer), same category as
# profile/storage.py's master_profile.json - so it lives under data/ like
# every other personal store, not tracked in git (2026-08-11, Zahir's
# explicit "why would user-specific metadata go into product git" call -
# this file was git-tracked from its initial build and had to be moved out
# after the fact; see the same commit's history note about already-
# committed content).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = PROJECT_ROOT / "data" / "skills" / "canonical_skills.json"

# Append-only audit trail for auto-applied merges (2026-08-11, Zahir's
# explicit call to let panga-taxonomy-fallback-consolidation auto-apply
# high-confidence merges rather than wait for approval - see
# log_taxonomy_merge() below). Real personal data (labels), same as
# TAXONOMY_PATH itself - under data/, not tracked in git.
MERGE_LOG_PATH = PROJECT_ROOT / "data" / "skills" / "taxonomy_merge_log.jsonl"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


# In-process cache for load_taxonomy()/find_canonical_id() (2026-08-18,
# real perf fix - see docs/perf-fixes.md "Profile Gaps tab" entry).
# Real incident: the Profile Gaps tab's 32-job loop was measured taking
# 26-70s PER JOB (10+ minutes for the full loop, never completing before
# being killed) - cProfile/manual timing traced ~100% of that to
# find_canonical_id() alone, called ~2000 times per job via _same_skill()
# in tailoring.drafting._merge_keyword_gap_questions(), each call doing a
# full linear regex-based scan of the ENTIRE taxonomy (436 real entries,
# canonical_label + every alias) with no memoization at all - and the
# exact same (label, taxonomy-content) pairs recur constantly, both
# within one job's loop (the same previously_answered_skills list is
# re-scanned once per clarifying_question) and across every job in the
# batch (previously_answered_skills and the taxonomy itself don't change
# job to job). This is the classic "O(n) scan repeated on every call
# inside a per-job loop" pattern CLAUDE.md's own standing performance
# principle warns about, just one level removed - the taxonomy scan was
# hiding inside a helper (find_canonical_id) called from a helper
# (_same_skill) called from a helper (_merge_keyword_gap_questions), not
# a job-store/profile-store reload itself.
#
# Fix: load_taxonomy() now caches its parsed dict in-process, keyed by
# the file's own mtime - repeated calls within one process (e.g. once per
# job in a batch loop) return the exact SAME dict object as long as the
# file hasn't changed underneath it, so find_canonical_id()'s own
# memoization below (keyed on that object's identity) stays warm and
# valid across the whole loop instead of recomputing from scratch every
# single call. The moment the file's on-disk mtime changes (a real
# save_taxonomy() write from ANY process/session), the next load_taxonomy()
# call detects the mismatch, re-reads fresh, and clears the
# find_canonical_id memoization cache below - so a stale in-memory copy
# is never served past a real on-disk change. add_canonical_entry() and
# merge_canonical_entries() (the two functions that mutate a taxonomy
# dict in place, before it's saved) also clear the memoization cache
# directly, closing the narrow window where an in-place mutation could
# otherwise leave a stale "not found" result cached for a label that a
# same-process caller queries again before the next load_taxonomy() call
# notices the file changed.
#
# memoization alone still leaves one real cost unpaid: the FIRST time any
# given label is looked up against a given taxonomy, find_canonical_id()
# still has to do a real O(taxonomy size) scan - and with 315 real
# previously_answered_skills each needing that scan at least once, even
# the memoized version measured ~9s for the FIRST job in a cold run
# (subsequent jobs reuse the warm cache and drop to well under a second).
# The dominant cost in that first scan wasn't the comparison logic itself
# but skills_match() re-deriving a fresh normalized string AND compiling a
# fresh regex pattern for every taxonomy entry's canonical_label/alias, on
# every single query - real work that's identical from query to query
# since the taxonomy's OWN text never changes between queries.
# _taxonomy_index() below precomputes that once per taxonomy object
# (normalized text + compiled \b-bounded pattern for every canonical_label
# and alias), cached the same identity-keyed way as _canonical_id_cache,
# so find_canonical_id() only ever pays for normalizing/compiling the
# QUERY label fresh (unavoidable - it's genuinely new each time) and reuses
# the taxonomy's precomputed side for every comparison. This reproduces
# skills_match()'s exact matching rule (normalized equality, or either
# side found as a real word-boundary phrase within the other) - it is a
# faster implementation of the identical logic, not a behavior change.
#
# This only changes the SPEED of find_canonical_id() - every input still
# produces the exact same output as the uncached linear scan (verified by
# tests/test_canonical_taxonomy.py's cache-correctness tests and by a
# byte-for-byte comparison of analyze_fit_before_drafting()'s real
# open_questions output before/after this fix, see
# tests/test_drafting_profile_gaps_perf.py).
_taxonomy_cache: dict = {"cache_key": None, "data": None}
_canonical_id_cache: dict = {}
_taxonomy_index_cache: dict = {}


def load_taxonomy() -> dict:
    """Cache key is (str(TAXONOMY_PATH), mtime) - not mtime alone. Real
    bug caught while writing this fix's own tests: TAXONOMY_PATH itself is
    monkeypatched to a different tmp_path per test (see
    tests/test_canonical_taxonomy.py's existing pattern) - a cache keyed
    on mtime alone could serve a PREVIOUS test's cached taxonomy dict for
    an entirely different file, if the two files' mtimes ever happened to
    land on the same value (float precision on some filesystems is coarse
    enough for this to be a real, not just theoretical, risk within a
    single fast-running test session). Including the path in the cache
    key makes a path change always a genuine cache miss, regardless of
    mtime coincidence."""
    if not TAXONOMY_PATH.exists():
        cache_key = (str(TAXONOMY_PATH), None)
        if _taxonomy_cache["cache_key"] != cache_key:
            _taxonomy_cache["cache_key"] = cache_key
            _taxonomy_cache["data"] = {"_meta": {}}
            _canonical_id_cache.clear()
            _taxonomy_index_cache.clear()
        return _taxonomy_cache["data"]
    cache_key = (str(TAXONOMY_PATH), TAXONOMY_PATH.stat().st_mtime)
    if _taxonomy_cache["cache_key"] != cache_key:
        _taxonomy_cache["data"] = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        _taxonomy_cache["cache_key"] = cache_key
        _canonical_id_cache.clear()
        _taxonomy_index_cache.clear()
    return _taxonomy_cache["data"]


def _taxonomy_index(taxonomy: dict) -> list[tuple[str, list[tuple[str, "re.Pattern"]]]]:
    """Precomputed, identity-cached (per taxonomy object) list of
    (entry_id, [(normalized_term, compiled_\\b-pattern), ...]) pairs - one
    entry per taxonomy row, one (normalized_term, pattern) per
    canonical_label/alias on that row. Built once per taxonomy object (not
    once per query), see the module-level comment above."""
    key = id(taxonomy)
    idx = _taxonomy_index_cache.get(key)
    if idx is not None:
        return idx
    idx = []
    for entries in _categories(taxonomy).values():
        for entry in entries:
            terms = [entry.get("canonical_label") or ""] + list(entry.get("aliases") or [])
            compiled = []
            for term in terms:
                norm = normalize_skill_label(term)
                if not norm:
                    continue
                compiled.append((norm, re.compile(r"\b" + re.escape(norm) + r"\b")))
            idx.append((entry["id"], compiled))
    _taxonomy_index_cache[key] = idx
    return idx


def save_taxonomy(data: dict) -> None:
    """Atomic write (temp file + os.replace), same pattern as skills/
    lookup.py's save_role_skills() - never leaves a half-written file
    for a crash or a concurrent reader to see."""
    TAXONOMY_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    check "is this a known concept" before ever proposing a new one.

    Memoized in-process (2026-08-18, real perf fix - see the module-level
    comment above _taxonomy_cache) keyed on (id(taxonomy), label): a full
    linear scan of every canonical_label/alias in the taxonomy is real
    work (436 real entries today, growing over time) that's wasteful to
    repeat for the exact same (label, taxonomy-content) pair - which
    happens constantly in practice, both within one caller's own loop
    (drafting._merge_keyword_gap_questions() re-scans the same
    previously_answered_skills list once per clarifying_question) and
    across repeated calls with the SAME taxonomy object load_taxonomy()
    now returns while the underlying file is unchanged (a batch loop
    scoring many jobs back to back, e.g. the Profile Gaps tab). Safe
    because the cache key includes the taxonomy object's own identity,
    not just its label - a caller passing a different/freshly-built
    taxonomy dict (tests, or a taxonomy about to be mutated in place by
    add_canonical_entry()/merge_canonical_entries(), both of which clear
    this cache themselves before returning) simply gets a fresh cache
    key and correct, uncached results either way."""
    if not label:
        return None
    cache_key = (id(taxonomy), label)
    if cache_key in _canonical_id_cache:
        return _canonical_id_cache[cache_key]
    norm_label = normalize_skill_label(label)
    result = None
    if norm_label:
        label_pattern = re.compile(r"\b" + re.escape(norm_label) + r"\b")
        for entry_id, terms in _taxonomy_index(taxonomy):
            for norm_term, term_pattern in terms:
                # Exact reproduction of skills_match()'s own rule -
                # normalized equality, or either side found as a real
                # word-boundary phrase within the other - just against
                # the taxonomy side's precomputed normalized text/pattern
                # instead of recomputing it fresh on every query.
                if norm_term == norm_label or term_pattern.search(norm_label) or label_pattern.search(norm_term):
                    result = entry_id
                    break
            if result is not None:
                break
    _canonical_id_cache[cache_key] = result
    return result


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
    (new or pre-existing). Mutates `taxonomy` in place; caller saves.

    Clears find_canonical_id()'s in-process memoization cache
    (2026-08-18 perf fix, see _taxonomy_cache's own module-level comment)
    before returning on every path that actually mutates `taxonomy` -
    this function is the ONLY place besides merge_canonical_entries() a
    taxonomy dict's real content changes in place ahead of a save, so a
    label that was correctly cached as "not found" moments ago must not
    keep returning that stale answer to some other same-process caller
    querying the same (now-mutated) taxonomy object before the next
    load_taxonomy() call notices the file itself changed on disk."""
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
                    _canonical_id_cache.clear()
                    _taxonomy_index_cache.clear()
                    return existing_id

    all_ids = {e["id"] for entries in _categories(taxonomy).values() for e in entries}
    new_id = _slugify(canonical_label, all_ids)
    entries = taxonomy.setdefault(category, [])
    entries.append({"id": new_id, "canonical_label": canonical_label, "aliases": sorted(set(aliases))})
    _canonical_id_cache.clear()
    _taxonomy_index_cache.clear()
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


def _find_entry(taxonomy: dict, entry_id: str) -> dict | None:
    for entries in _categories(taxonomy).values():
        for entry in entries:
            if entry["id"] == entry_id:
                return entry
    return None


def merge_canonical_entries(survivor_id: str, merged_away_id: str) -> dict:
    """Deterministically merges merged_away_id into survivor_id: folds
    merged_away's canonical_label and every alias into survivor's own
    aliases, then removes the merged_away entry from the taxonomy
    entirely. Returns the updated taxonomy.

    This is the EXECUTION half of an auto-merge (2026-08-11, Zahir's
    explicit call for panga-taxonomy-fallback-consolidation to auto-apply
    high-confidence merges) - the DECISION (which pair, how confident) is
    real judgment made by the caller (an AI reasoning pass, not this
    function); this function only does the mechanical part, the same
    "AI decides, deterministic code executes" split every other
    consequential action in this app already follows (is_disqualifier,
    ats_score, etc.) - so a merge is always the same safe, tested
    operation regardless of which run or which reasoning pass decided it.

    Raises ValueError if survivor_id == merged_away_id (nothing to
    merge), or if either id isn't actually in the taxonomy - a caller
    passing a stale/already-merged id is a real bug worth a loud failure,
    not a silent no-op.

    Locked (canonical_taxonomy) for the whole read-modify-write, same
    real cross-process safety as resolve_or_create_canonical_id().

    IMPORTANT ordering for callers: any master_profile.json
    gap_interview_answers referencing merged_away_id must be redirected
    to survivor_id FIRST (see profile.interview.redirect_canonical_skill_id())
    - BEFORE calling this function, not after. That order means a crash
    between the two steps leaves the taxonomy still holding both entries
    (harmless - just means the merge isn't fully applied yet) rather than
    ever leaving a real answer pointing at an id that no longer exists
    anywhere."""
    if survivor_id == merged_away_id:
        raise ValueError("survivor_id and merged_away_id must be different - nothing to merge")

    with locked("canonical_taxonomy"):
        taxonomy = load_taxonomy()
        survivor = _find_entry(taxonomy, survivor_id)
        merged_away = _find_entry(taxonomy, merged_away_id)
        if survivor is None:
            raise ValueError(f"survivor_id {survivor_id!r} not found in taxonomy")
        if merged_away is None:
            raise ValueError(f"merged_away_id {merged_away_id!r} not found in taxonomy")

        new_aliases = set(survivor.get("aliases", []))
        new_aliases.add(merged_away["canonical_label"])
        new_aliases.update(merged_away.get("aliases", []))
        new_aliases.discard(survivor["canonical_label"])
        survivor["aliases"] = sorted(new_aliases)

        for category in list(_categories(taxonomy).keys()):
            taxonomy[category] = [e for e in taxonomy[category] if e["id"] != merged_away_id]

        save_taxonomy(taxonomy)
        # Same in-place-mutation cache-staleness reasoning as
        # add_canonical_entry() above - merged_away_id no longer exists in
        # this taxonomy object once the loop above runs, so any cached
        # find_canonical_id() result that used to resolve to it must not
        # keep being served.
        _canonical_id_cache.clear()
        _taxonomy_index_cache.clear()
        return taxonomy


def log_taxonomy_merge(
    survivor_id: str, survivor_label: str, merged_away_id: str, merged_away_label: str,
    reasoning: str, answers_redirected: int, timestamp: str,
) -> None:
    """Appends one real audit record for an auto-applied merge - every
    auto-merge must call this (in addition to reporting to the hub), so
    the merge is auditable after the fact even though there's no human
    approval gate before it happens anymore. `timestamp` is the caller's
    responsibility (real ISO8601 string) rather than computed here, same
    reasoning as every other real-world timestamp in this codebase being
    passed in rather than self-generated - keeps this function itself
    trivially testable without mocking time.

    A distinct lock name ("canonical_taxonomy_merge_log") from
    "canonical_taxonomy" itself - appending a log entry for one merge
    should never block or be blocked by a concurrent merge's own taxonomy
    read-modify-write."""
    MERGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": timestamp,
        "survivor_id": survivor_id,
        "survivor_label": survivor_label,
        "merged_away_id": merged_away_id,
        "merged_away_label": merged_away_label,
        "reasoning": reasoning,
        "gap_interview_answers_redirected": answers_redirected,
    }
    with locked("canonical_taxonomy_merge_log"):
        with open(MERGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
    caught live 2026-08-11.

    Clears find_canonical_id()'s in-process memoization cache after
    mutate_fn runs (2026-08-18 perf fix) - mutate_fn is caller-supplied
    and arbitrary, so this can't assume it only ever calls
    add_canonical_entry()/merge_canonical_entries() (which already clear
    the cache themselves); clearing here too is the safe default for any
    bulk mutation."""
    with locked("canonical_taxonomy"):
        taxonomy = load_taxonomy()
        result = mutate_fn(taxonomy)
        save_taxonomy(taxonomy)
        _canonical_id_cache.clear()
        _taxonomy_index_cache.clear()
        return taxonomy, result
