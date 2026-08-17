"""Phase 2 of Zahir's confirmed "final set of questions" taxonomy-gap
build (2026-08-17, feature/jd-keyword-taxonomy-gaps): pure Python,
zero-AI-cost frequency analysis over every job's already-extracted
ats_required_keywords/ats_preferred_keywords (Phase 1's output - see
tailoring.jd_keyword_extraction / scripts/batch_extract_jd_keywords.py).

Answers the real question Zahir's own words posed: given everything the
system has already seen across real job postings, what are the real,
demonstrated gaps worth asking about ONE more time - not another
per-job round, a final consolidated one.

Two outputs, both filtered to a shared recurrence threshold
(DEFAULT_MIN_RECURRENCE, a named constant/parameter per Zahir's explicit
"don't hand-tune this further, but make it easy to change later" call -
never scattered as a magic number):

- confirmed_gaps: canonical taxonomy skill ids that recur in >= threshold
  DISTINCT job postings and have NO existing gap_interview_answers entry
  yet - real, demonstrated gaps the profile has never actually confirmed.
- new_concept_candidates: free-text terms that don't match any existing
  taxonomy entry at all, also recurring in >= threshold distinct
  postings - genuinely new concepts worth adding to the taxonomy, not
  noise from a single posting's idiosyncratic phrasing.

Deliberately reuses skills.canonical_taxonomy.find_canonical_id() (the
SAME deterministic matcher profile/interview.py's save path and
drafting.py's clarifying-question dedup already use) rather than a
second, parallel matching heuristic - "have we asked/answered this
before" must mean the same thing everywhere in this app."""

from collections import defaultdict

from profile.storage import load_profile
from search.job_store import load_jobs
from skills.canonical_taxonomy import _find_entry, find_canonical_id, load_taxonomy

# Zahir's explicit call, 2026-08-17: 3 distinct postings is the real
# threshold to ship with - not hand-tuned further, but kept as a named
# constant (and an overridable function parameter below) specifically so
# it's a one-line change later rather than a value re-typed in several
# places if it ever needs to move.
DEFAULT_MIN_RECURRENCE = 3

# How many example (title, organization) pairs to keep per gap/candidate,
# just enough for the review UI to show "recurs across roughly these
# postings" without holding onto (or rendering) an unbounded list.
_MAX_EXAMPLES = 5


def _flatten_keyword_term(item) -> list[str]:
    """A keyword entry is either a plain string, or an either/or group
    {"any_of": [...]} (drafting.ATS_KEYWORDS_SYSTEM_PROMPT's either/or /
    degree-field-list shape) - each alternative inside an any_of group is
    itself a real, independently checkable term worth tallying on its own
    (e.g. "Computer Science" and "Business" from the same degree-field
    any_of group are two distinct real facts, not one)."""
    if isinstance(item, str):
        term = item.strip()
        return [term] if term else []
    if isinstance(item, dict) and item.get("any_of"):
        return [str(m).strip() for m in item["any_of"] if str(m).strip()]
    return []


def _iter_job_keyword_terms(job: dict):
    for item in (job.get("ats_required_keywords") or []):
        for term in _flatten_keyword_term(item):
            yield term
    for item in (job.get("ats_preferred_keywords") or []):
        for term in _flatten_keyword_term(item):
            yield term


def analyze_recurring_gaps(min_recurrence: int = DEFAULT_MIN_RECURRENCE) -> dict:
    """Tallies keyword frequency across every job that has extracted ATS
    keywords (job["ats_required_keywords"] is not None - Phase 1's own
    completion marker), matches each distinct term against the canonical
    taxonomy, and returns the ranked confirmed_gaps/new_concept_candidates
    lists described in this module's own docstring.

    Counts DISTINCT JOB POSTINGS a term/concept appears in, not raw
    keyword-list occurrences - a job listing the same term in both
    required and preferred (or a term appearing twice in one list) counts
    once for that job, matching "recurs across N different real
    postings", the actual signal Zahir asked for, not "was mentioned N
    times total.\""""
    jobs = load_jobs()
    taxonomy = load_taxonomy()
    profile = load_profile()
    already_answered_ids = {
        a.get("canonical_skill_id")
        for a in (profile.get("gap_interview_answers") or [])
        if a.get("canonical_skill_id")
    }

    matched = defaultdict(lambda: {"job_keys": set(), "examples": []})
    unmatched = defaultdict(lambda: {"job_keys": set(), "examples": [], "sample_label": None})

    jobs_with_keywords = 0
    for job in jobs:
        if job.get("ats_required_keywords") is None:
            continue
        jobs_with_keywords += 1
        job_key = (job.get("source"), job.get("job_id"))
        example = {"title": job.get("title") or "Untitled role", "organization": job.get("organization") or "Unknown organization"}

        seen_matched_this_job = set()
        seen_unmatched_this_job = set()
        for term in _iter_job_keyword_terms(job):
            canonical_id = find_canonical_id(term, taxonomy)
            if canonical_id:
                if canonical_id in seen_matched_this_job:
                    continue
                seen_matched_this_job.add(canonical_id)
                entry = matched[canonical_id]
                entry["job_keys"].add(job_key)
                if len(entry["examples"]) < _MAX_EXAMPLES:
                    entry["examples"].append(example)
            else:
                norm = term.lower()
                if norm in seen_unmatched_this_job:
                    continue
                seen_unmatched_this_job.add(norm)
                entry = unmatched[norm]
                entry["job_keys"].add(job_key)
                entry["sample_label"] = entry["sample_label"] or term
                if len(entry["examples"]) < _MAX_EXAMPLES:
                    entry["examples"].append(example)

    confirmed_gaps = []
    for canonical_id, data in matched.items():
        job_count = len(data["job_keys"])
        if job_count < min_recurrence or canonical_id in already_answered_ids:
            continue
        taxonomy_entry = _find_entry(taxonomy, canonical_id)
        confirmed_gaps.append({
            "canonical_skill_id": canonical_id,
            "label": taxonomy_entry["canonical_label"] if taxonomy_entry else canonical_id,
            "job_count": job_count,
            "examples": data["examples"],
        })
    confirmed_gaps.sort(key=lambda g: (-g["job_count"], g["label"]))

    new_concept_candidates = []
    for norm, data in unmatched.items():
        job_count = len(data["job_keys"])
        if job_count < min_recurrence:
            continue
        new_concept_candidates.append({
            "label": data["sample_label"],
            "job_count": job_count,
            "examples": data["examples"],
        })
    new_concept_candidates.sort(key=lambda c: (-c["job_count"], c["label"]))

    return {
        "total_jobs": len(jobs),
        "jobs_with_keywords": jobs_with_keywords,
        "min_recurrence": min_recurrence,
        "confirmed_gaps": confirmed_gaps,
        "new_concept_candidates": new_concept_candidates,
    }


def _examples_phrase(examples: list[dict]) -> str:
    shown = [f"{e['title']} at {e['organization']}" for e in examples[:3]]
    return "; ".join(shown)


def build_review_questions(analysis: dict) -> list[dict]:
    """Turns analyze_recurring_gaps()'s ranked output into the actual
    question objects the review UI renders (ui/app.py's "Review recurring
    profile gaps" panel) - deterministic templating, not another AI call
    (Phase 2's whole point is this analysis is $0/pure Python; phrasing a
    question from data this structured doesn't need reasoning either, and
    a templated question can't hallucinate a fact the data doesn't
    support).

    Each item: {"skill": str, "question": str, "type": "confirmed_gap"|
    "new_concept", "job_count": int, "examples": [...]}. "skill" for a
    confirmed_gap is the taxonomy entry's own canonical_label (so
    save_profile_gap_review_answers()'s save_answer() call resolves back
    to the SAME canonical_skill_id, never creates a near-duplicate entry
    for a concept that already exists); "skill" for a new_concept_
    candidate is the raw recurring term itself (so confirming it creates a
    genuinely new taxonomy entry via resolve_or_create_canonical_id() -
    the intended outcome for a real, demonstrated new concept).

    No suggested_answer is ever templated here, unlike the per-job
    clarifying-questions flow (drafting.py's AI-authored hedged guesses) -
    this module has no actual signal about whether the candidate has a
    given skill, only that it recurs in postings, so per this repo's HCI
    standard ("things that need the user's real reasoning, not a guess at
    all, stay an empty box with a placeholder, never a prefill") every
    box here starts empty; the UI supplies the placeholder text."""
    questions = []
    for gap in analysis["confirmed_gaps"]:
        examples_phrase = _examples_phrase(gap["examples"])
        questions.append({
            "skill": gap["label"],
            "canonical_skill_id": gap["canonical_skill_id"],
            "type": "confirmed_gap",
            "job_count": gap["job_count"],
            "examples": gap["examples"],
            "question": (
                f"Do you have real, genuine experience with \"{gap['label']}\"? It shows up as a required or "
                f"preferred qualification in {gap['job_count']} of your saved job postings so far (e.g. {examples_phrase})."
            ),
        })
    for candidate in analysis["new_concept_candidates"]:
        examples_phrase = _examples_phrase(candidate["examples"])
        questions.append({
            "skill": candidate["label"],
            "canonical_skill_id": None,
            "type": "new_concept",
            "job_count": candidate["job_count"],
            "examples": candidate["examples"],
            "question": (
                f"\"{candidate['label']}\" shows up as a required or preferred term in {candidate['job_count']} of "
                f"your saved job postings (e.g. {examples_phrase}) but isn't in your profile yet. Do you have real "
                f"experience with it? If so, briefly describe it - if not, leave this blank."
            ),
        })
    return questions
