"""Baseline resume selection for the score-first resume flow (spec:
docs/score-first-resume-flow-spec.md, item 1). Before any resume has been
drafted for a job, Step 1 ("analyze fit" - no document written) still
needs some text to score that job's keywords against. This picks the most
similar PREVIOUSLY DRAFTED resume from another application when a good
match exists, falling back to the generic base resume
(profile.ingest.resume_text()) otherwise.

Deliberately pure arithmetic over keyword lists already cached on job
records (search.job_store's ats_required_keywords/ats_preferred_keywords)
- the spec is explicit that this must not trigger a new AI call. A past
application whose own job record has no cached keyword list simply isn't
in the comparison pool; it is never backfilled here. In practice (checked
against real data 2026-08-08) only a small fraction of past job records
have been through keyword extraction yet, so this will often fall back to
the generic resume today - that's expected to improve over time as more
jobs go through Step 1, not something this function can force.
"""

from search.job_store import load_jobs
from tailoring.applications import load_applications

# Tuned against real profile/job data (see tests/test_baseline_resume.py) -
# high enough that an unrelated role (different domain, only a couple of
# generic terms like "leadership" or "vendor management" in common) can't
# win, low enough that genuinely similar postings for the same kind of
# role (even from different industries) still match.
MIN_JACCARD_OVERLAP = 0.2


def _flatten_keywords(job: dict) -> set[str]:
    """Lowercased union of a job's cached required+preferred keywords.
    Deliberately coarse about either/or groups (item 2) - every
    alternative in a group contributes its own term to the set - since
    this is only picking a topically-similar past resume, not doing the
    precise literal-match scoring ats_score.py does."""
    flat: set[str] = set()
    for keyword in (job.get("ats_required_keywords") or []) + (job.get("ats_preferred_keywords") or []):
        if isinstance(keyword, dict):
            flat.update(str(member).lower() for member in keyword.get("any_of", []))
        else:
            flat.add(str(keyword).lower())
    return flat


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _generic_baseline() -> tuple[str, str]:
    from profile.ingest import resume_text

    return resume_text(), "your general profile resume"


def select_baseline_resume_text(job: dict) -> tuple[str, str]:
    """Returns (baseline_text, baseline_source_label). baseline_source_label
    is a short, human-readable description of what was picked - surfaced
    by the UI for transparency (e.g. "your drafted resume for BD -
    Director, Digital Solutions Architect" vs "your general profile
    resume") - this is never a silent choice.
    """
    target_key = (job.get("source"), job.get("job_id"))
    target_keywords = _flatten_keywords(job)
    if not target_keywords:
        return _generic_baseline()

    jobs_by_key = {(j.get("source"), j.get("job_id")): j for j in load_jobs()}

    best_overlap = 0.0
    best_match: tuple[dict, dict] | None = None
    for app in load_applications():
        # Real gap found live 2026-08-08: if this job already has its own
        # drafted resume (Step 1 is only meant to run before one exists,
        # but nothing stops a caller from invoking this on a job that
        # already has one), it would trivially "match" itself at overlap
        # 1.0 and hand back its own current resume as its own baseline -
        # meaningless for "what should this score against before a resume
        # exists." Excluded regardless of caller behavior.
        if (app.get("source"), app.get("job_id")) == target_key:
            continue
        resume_text = app.get("resume_text")
        if not resume_text:
            continue
        candidate_job = jobs_by_key.get((app.get("source"), app.get("job_id")))
        if not candidate_job:
            continue
        overlap = _jaccard(target_keywords, _flatten_keywords(candidate_job))
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = (app, candidate_job)

    if best_match and best_overlap >= MIN_JACCARD_OVERLAP:
        app, candidate_job = best_match
        title = candidate_job.get("title") or "a previous role"
        organization = candidate_job.get("organization")
        label = (
            f"your drafted resume for {organization} - {title}" if organization
            else f"your drafted resume for {title}"
        )
        return app["resume_text"], label

    return _generic_baseline()
