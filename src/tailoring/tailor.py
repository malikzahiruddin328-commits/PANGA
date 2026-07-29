"""Build step 5: takes a JD + master profile, drafts tailored resume/cover
letter (guided, back-and-forth).

Same split as interview.py (per PRD §11 LLM architecture): actually drafting
tailored resume/cover-letter prose is reasoning work done by Claude directly,
in conversation with the user, job by job - not something to hardcode as a
template in Python. This module only handles the mechanical part: bundling
the job + master profile into one clean context for that conversation, and
persisting the resulting draft via applications.upsert_application().
"""

from profile.storage import load_profile


def build_tailoring_context(job: dict, profile: dict | None = None) -> dict:
    """Bundles a job record (from job_store) with the master profile into the
    context Claude needs to draft a tailored resume/cover letter for it."""
    return {
        "job": job,
        "profile": profile if profile is not None else load_profile(),
    }
