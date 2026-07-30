"""PRD §13: takes Zahir's pasted LinkedIn profile text + the master profile,
drafts gap analysis and suggested rewrites (headline/about/experience/skills)
plus a 0-100 profile strength score.

Same split as tailor.py/interview.py (per PRD §11 LLM architecture): the
actual gap analysis and rewrite drafting is reasoning work done by Claude
directly, in conversation with the user - not hardcoded here. This module
only bundles the context that reasoning needs; results get persisted via
linkedin.storage.save_analysis().
"""

from profile.storage import load_profile
from skills.lookup import load_role_skills
from linkedin.storage import load_linkedin_profile


def build_enhancement_context(
    snapshot: dict | None = None,
    profile: dict | None = None,
) -> dict:
    """Bundles the uploaded LinkedIn PDF's extracted raw text, master
    profile, and the full role/skill lookup table (PRD §4) into the context
    Claude needs to find gaps and draft suggested rewrites. Splitting
    raw_text into headline/about/experience/skills is Claude's job during
    analysis, not something parsed here - LinkedIn PDF export layout isn't
    stable enough to regex reliably."""
    return {
        "linkedin_snapshot": snapshot if snapshot is not None else load_linkedin_profile(),
        "profile": profile if profile is not None else load_profile(),
        "role_skills": load_role_skills(),
    }
