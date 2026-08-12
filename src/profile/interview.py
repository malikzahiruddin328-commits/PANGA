"""Build step 2: gap-probing interview engine.

Compares the role/industry skill lookup table (src/skills/role_skills.json)
against the raw resume text to flag skills that are missing or only vaguely
implied. Producing the actual interview *questions* and interpreting nuanced
answers is reasoning work done by Claude directly (per PRD §11 LLM
architecture) — this module handles the mechanical parts: gap detection
against resume text, and persisting confirmed answers to the master profile.
"""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skills.lookup import skills_for  # noqa: E402
from skills.canonical_taxonomy import find_canonical_id, load_taxonomy, resolve_or_create_canonical_id  # noqa: E402
from profile.storage import load_profile, save_profile  # noqa: E402
from profile.ingest import resume_text as _shared_resume_text  # noqa: E402
from security.file_lock import locked  # noqa: E402
from skill_label_match import skills_match  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "profile" / "raw"


def _resume_text() -> str:
    return _shared_resume_text().lower()


def _already_answered(skill: str) -> bool:
    # Real bug found + fixed 2026-08-11 while wiring in the canonical
    # taxonomy: this used to early-return via `MASTER_PROFILE_PATH.exists()`,
    # a name bound at THIS module's import time via `from profile.storage
    # import MASTER_PROFILE_PATH` - stale the moment profile_storage.
    # MASTER_PROFILE_PATH is reassigned afterward (e.g. tests/conftest.py's
    # isolated_data fixture), since that only patches profile_storage's own
    # attribute, not this module's already-bound copy of it. Silently
    # always returned False under test isolation as a result - no earlier
    # test happened to call _already_answered() directly to catch it.
    # load_profile() already returns a safe {} default for a missing file
    # (security.crypto_store.read_json's own default=), so the explicit
    # existence check was redundant even before it went stale - removed
    # rather than "fixed" to a fresh re-read of the constant.
    profile = load_profile()
    answers = profile.get("gap_interview_answers", [])

    # Canonical-id match first (2026-08-11) - catches real same-fact
    # matches skills_match() alone can't, e.g. "CSAT/NPS numeric scores on
    # consulting engagements" vs "Customer satisfaction scores on
    # consulting engagements" (no shared vocabulary, confirmed live during
    # the real taxonomy audit) - as long as the taxonomy already knows
    # both are aliases of the same canonical entry, which
    # skills_match()'s pairwise word-boundary check never could on its
    # own. Falls back to skills_match() for legacy entries that predate
    # migration and have no canonical_skill_id yet, or when the query
    # skill itself isn't in the taxonomy at all - real coverage never
    # narrows versus before, only widens.
    taxonomy = load_taxonomy()
    query_canonical_id = find_canonical_id(skill, taxonomy)
    for a in answers:
        if query_canonical_id is not None and a.get("canonical_skill_id") == query_canonical_id:
            return True
        if skills_match(a["skill"], skill):
            return True
    return False


def _canonical_id_for(skill: str) -> str:
    """Deterministic, no AI call - resolves `skill` against the shared
    canonical taxonomy (skills/canonical_skills.json), creating a fresh
    entry under "Uncategorized" if nothing existing covers it yet (same
    "grows over time, never loses a real answer" guarantee as the bulk
    migration in tailoring.taxonomy_migration). Persists any newly
    created entry immediately so the next save_answer()/_already_answered()
    call sees it.

    Real bug found + fixed 2026-08-11 (RM caught it live, mid-merge,
    while Zahir was actively interviewing through a separate concurrent
    session): this used to call load_taxonomy()/add_canonical_entry()/
    save_taxonomy() with no locking at all - a genuine cross-process
    read-modify-write race (two concurrent save_answer() calls, or a
    live interview racing a migration/backfill script, could both read
    the same old taxonomy and whichever saved last would silently
    overwrite the other's new entry). resolve_or_create_canonical_id()
    wraps the whole thing in the same real, cross-process
    locked("canonical_taxonomy") primitive master_profile.json's own
    writes already use - see its own docstring for the full story."""
    return resolve_or_create_canonical_id(skill, "Uncategorized", aliases=[])


def detect_gaps(industry: str, role: str) -> list[dict]:
    """Returns role_skills entries that don't clearly appear in the resume text
    and haven't already been answered — i.e. worth asking about."""
    text = _resume_text()
    gaps = []
    for entry in skills_for(industry, role):
        keyword = entry["skill"].split("(")[0].split("/")[0].strip().lower()
        if keyword not in text and not _already_answered(entry["skill"]):
            gaps.append(entry)
    return gaps


def save_answer(
    skill: str, role_context: str, answer: str, date_captured: str,
    question: str = "", is_disqualifier: bool = False,
) -> None:
    """is_disqualifier marks this as a real, candidate-confirmed exclusion
    (a role type they've said they don't want/aren't qualified for despite
    otherwise-matching experience) rather than an ordinary skill-gap
    confirmation - drafting.SCORE_SYSTEM_PROMPT reads this flag and scores
    matching postings low regardless of subject-matter proximity.

    Updates the existing entry in place (matched via skill_label_match's
    normalized/word-boundary comparison, not exact string equality - real
    gap flagged by Mirror 2026-08-08: an exact match against a free-text,
    AI-generated label silently fails the moment the model phrases the
    same underlying fact differently across rounds, e.g. "AWS" vs "cloud
    infrastructure" - see skill_label_match.py's module docstring) if this
    skill was already answered before, rather than appending a duplicate -
    real bug fixed 2026-08-06 while building the "view/update a previously
    answered question" UI (Zahir's ask): answering the same skill_gap
    question again across rounds used to silently pile up duplicate,
    potentially conflicting entries for the same skill instead of
    reflecting the current, latest confirmed answer. question is the
    original clarifying_questions wording (optional - older callers/entries
    may not have one), stored so the answered-questions view can show what
    was actually asked, not just the short skill label.

    Wrapped in the same locked("master_profile") pattern every other
    multi-writer JSON store in this app already uses (job_store.py,
    applications.py, cta_emails.py, ...) - this module was the one store
    that had never adopted it despite being written from multiple places
    (this function, profile/storage.py's update_profile_field()), a real
    read-modify-write race this fixes while already in this exact
    function, not a new one introduced by it."""
    # Resolved before acquiring the lock - _canonical_id_for() does its
    # own real work (a taxonomy lookup, and possibly a taxonomy write) and
    # doesn't touch master_profile.json, so it doesn't need to happen
    # inside this function's own critical section.
    canonical_skill_id = _canonical_id_for(skill)

    with locked("master_profile"):
        profile = load_profile()
        answers = profile.setdefault("gap_interview_answers", [])
        for entry in answers:
            if skills_match(entry["skill"], skill):
                entry["role_context"] = role_context
                entry["answer"] = answer
                entry["date_captured"] = date_captured
                entry["is_disqualifier"] = is_disqualifier
                entry["canonical_skill_id"] = canonical_skill_id
                if question:
                    entry["question"] = question
                save_profile(profile)
                return
        answers.append({
            "skill": skill,
            "role_context": role_context,
            "answer": answer,
            "date_captured": date_captured,
            "is_disqualifier": is_disqualifier,
            "question": question,
            "canonical_skill_id": canonical_skill_id,
        })
        save_profile(profile)


def redirect_canonical_skill_id(old_id: str, new_id: str) -> int:
    """Updates every gap_interview_answers entry whose canonical_skill_id
    == old_id to new_id instead. Real, necessary step whenever two
    canonical taxonomy entries get merged (see
    skills.canonical_taxonomy.merge_canonical_entries()) - otherwise a
    real answer would be left pointing at an id that no longer exists in
    the taxonomy once the merged-away entry is removed.

    Caller ordering (2026-08-11, the auto-merge build): call this BEFORE
    merge_canonical_entries(), not after - see that function's own
    docstring for why the order matters (a crash between the two leaves
    the taxonomy holding both entries, which is harmless, rather than
    ever leaving a real answer orphaned).

    Locked (master_profile) for the whole read-modify-write, same as
    save_answer(). Returns the number of entries actually redirected (0
    is a valid, non-error result - just means nothing referenced old_id,
    e.g. a merge between two entries neither of which has been answered
    yet)."""
    with locked("master_profile"):
        profile = load_profile()
        count = 0
        for answer in profile.get("gap_interview_answers", []):
            if answer.get("canonical_skill_id") == old_id:
                answer["canonical_skill_id"] = new_id
                count += 1
        if count:
            save_profile(profile)
        return count


if __name__ == "__main__":
    for gap in detect_gaps("Lifesciences/Pharma", "Head of IT / CIO"):
        print(f"- {gap['skill']}  ({gap['why_it_matters']})")
