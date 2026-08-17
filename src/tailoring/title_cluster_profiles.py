"""Per-title-cluster keyword-coverage store (Feature 2, 2026-08-17).

Feature 1 (pre-flight score verification, already merged - see
tailoring.ats_score's keyword_literally_present()/
ensure_keyword_literally_present()) made each job's own ats_score
deterministic and literal: a keyword only ever counts for THAT job if it's
really, literally present in THAT job's drafted resume text. This module
sits alongside that, entirely on the QUESTION-GENERATION side, and must
never be allowed to leak into scoring:

Confirming a keyword-gap answer for one job (tailoring.drafting.
save_gap_answers) immediately seeds/updates this store for whatever title
cluster that job belongs to (search.title_cluster.resolve_title_cluster),
so a SECOND job in the same cluster never re-asks a near-duplicate
question about a fact already confirmed for the family. It is exclusively
a question-suppression shortcut - drafting._merge_keyword_gap_questions()
is the only real caller that reads from here (via
get_cluster_known_skills()), and it only ever uses this list to decide
whether to STOP ASKING a question, the same way it already uses the
candidate's own profile (_profile_supports_skill) for that purpose. A
cluster having a confirmed fact never touches ats_score.py's scoring
functions and never bypasses Feature 1's real per-job literal-keyword
verification - see tests/test_title_cluster_profiles.py's explicit
boundary test.

Shape: {"<cluster name>": [{"skill": str, "evidence": str,
"role_context": str, "confirmed_at": iso-str}, ...], ...}

Uses security.file_lock.locked() for every read-modify-write, same
discipline as tailoring.applications.py/profile.interview.py - this store
is a plain JSON file multiple processes (the Streamlit app, any future
scheduled task) can touch, so every write goes through the same
lock-the-whole-critical-section pattern, never a bare read-then-write.

Concurrency audit (2026-08-17, verified directly against source, not
assumed): ui/app.py's basket DOES run a real ThreadPoolExecutor (up to
BASKET_DRAFTALL_MAX_WORKERS=6, see `_draftall_worker`/`pool.submit(
_draftall_worker, job)` around app.py's "Draft & score all" handler) - but
that pool's workers only call subscription_resume_qa.run_subscription_
round()/recover_stale_qa_and_retry(), NEITHER of which calls
tailoring.drafting.save_gap_answers() (the only real caller of this
module's write side, record_cluster_fact()). save_gap_answers() is only
ever reached from two genuinely single-threaded paths: the Results tab's
per-job "answer & save" button, and the basket's "Submit answers &
redraft" handler (app.py, ~submit_answers_and_redraft call site) - a
plain sequential `for` loop over answered jobs, not a thread pool. So
record_cluster_fact()'s write is never actually reached from inside a
worker thread in this codebase today; get_cluster_known_skills()/
get_cluster_known_units() (read-only) ARE called from inside those
drafting worker threads (via drafting._merge_keyword_gap_questions), which
is safe without a lock the same way every other read in this app is. If a
future change ever routes save_gap_answers() through a thread pool, this
store's write path (locked() around the full read-modify-write, called
directly - no caller-provided pre-read data) stays correct as-is; only the
now-false assumption above (single-threaded callers) would need
re-verifying at that point.
"""

from datetime import datetime, timezone
from pathlib import Path

from security.crypto_store import read_json, write_json
from security.file_lock import locked

from skill_label_match import skills_match

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLUSTER_PROFILES_PATH = PROJECT_ROOT / "data" / "title_cluster_profiles.json"


def load_cluster_profiles() -> dict:
    return read_json(CLUSTER_PROFILES_PATH, default={})


def _save_all(profiles: dict) -> None:
    write_json(CLUSTER_PROFILES_PATH, profiles)


def get_cluster_known_skills(cluster_name: str | None) -> list[str]:
    """Returns the list of confirmed skill labels for this cluster (empty
    list if cluster_name is None/unknown/has no confirmed facts yet) - the
    shape drafting._merge_keyword_gap_questions() needs to fold into its
    existing _same_skill()-based dedup check, alongside the candidate's own
    profile via _profile_supports_skill()."""
    if not cluster_name:
        return []
    profiles = load_cluster_profiles()
    return [entry["skill"] for entry in profiles.get(cluster_name, []) if entry.get("skill")]


def get_cluster_known_units(cluster_name: str | None) -> list[str]:
    """Returns this cluster's confirmed facts as independent text UNITS
    (skill label and evidence text kept as separate units, never
    concatenated together) - the shape skill_label_match.skill_evidenced_
    in_text()/build_already_known_units() already expect. Extends
    drafting._merge_keyword_gap_questions()'s existing conjunctive,
    per-unit evidence check (built for the candidate's own profile text,
    see skill_label_match.py's module docstring item 3) to ALSO cover a
    fact confirmed for this job's title cluster on an earlier job - same
    function, same matching rules, a combined set of units, not a second
    parallel evidence-checking codepath."""
    if not cluster_name:
        return []
    profiles = load_cluster_profiles()
    units = []
    for entry in profiles.get(cluster_name, []):
        if entry.get("skill"):
            units.append(entry["skill"])
        if entry.get("evidence"):
            units.append(entry["evidence"])
    return units


def record_cluster_fact(cluster_name: str | None, skill: str, evidence: str, role_context: str = "") -> None:
    """Idempotently merges one confirmed fact into cluster_name's profile -
    a no-op if cluster_name is falsy (job's title didn't resolve to any
    configured cluster). Checked via skills_match() (the same normalized/
    word-boundary matcher every other skill-label dedup in this app uses,
    see skill_label_match.py's module docstring) before appending, so
    confirming the same fact twice (either the same job answered again, or
    a second job in the same cluster confirming an equivalently-worded
    fact) updates the existing entry in place instead of duplicating it -
    same "update in place, don't pile up duplicates" precedent as
    profile.interview.save_answer()."""
    if not cluster_name:
        return
    with locked("title_cluster_profiles"):
        profiles = load_cluster_profiles()
        entries = profiles.setdefault(cluster_name, [])
        for entry in entries:
            if skills_match(entry.get("skill", ""), skill):
                entry["evidence"] = evidence
                entry["role_context"] = role_context
                entry["confirmed_at"] = datetime.now(timezone.utc).isoformat()
                _save_all(profiles)
                return
        entries.append({
            "skill": skill,
            "evidence": evidence,
            "role_context": role_context,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_all(profiles)
