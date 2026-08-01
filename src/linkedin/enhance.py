"""PRD §13: takes Zahir's pasted LinkedIn profile text + the master profile,
drafts gap analysis and suggested rewrites (headline/about/experience/skills)
plus a 0-100 profile strength score.

build_enhancement_context() bundles what the analysis needs; analyze_profile()
(native-packaging branch, 2026-07-31) does the actual reasoning via a direct
Anthropic API call - the old "bundle context, go ask Claude Code" flow was
the same friction point already fixed for document drafting and Prospector
Score. Results get persisted via linkedin.storage.save_analysis()."""

import json

from profile.storage import load_profile
from skills.lookup import load_role_skills
from linkedin.storage import load_linkedin_profile
from llm_client import call_structured, get_client


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


_ANALYZE_SYSTEM_PROMPT = """You are analyzing a candidate's LinkedIn profile export against his real master profile and target-role skill expectations, for his job-search tool "Panga". Find genuine gaps and draft specific suggested rewrites - never invent a fact, employer, title, or accomplishment that isn't in the master profile. The LinkedIn export's raw_text may have unstable PDF-export layout - use your judgment to identify which part is headline/about/experience/skills/certifications rather than expecting clean delimiters.

For each section that has room to improve, produce one suggestion: current_text (what's there now, or "" if the section is missing/empty), suggested_text (your proposed rewrite - grounded only in facts from the master profile), and rationale (why this is stronger). Skip sections that are already strong - don't manufacture a suggestion just to have one for every section.

Also assign a 0-100 profile_strength_score (be honest, not generous) and a rationale explaining what's working and what would raise it most."""

_ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "profile_strength_score": {"type": "integer", "description": "0-100, honest assessment of the current profile's strength."},
        "profile_strength_rationale": {"type": "string", "description": "2-4 sentences: what's working, what would raise the score most."},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": ["headline", "about", "experience", "skills", "certifications"]},
                    "current_text": {"type": "string"},
                    "suggested_text": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["section", "current_text", "suggested_text", "rationale"],
                "additionalProperties": False,
            },
            "description": "Only sections with a genuine improvement to suggest - empty list if the whole profile is already strong.",
        },
    },
    "required": ["profile_strength_score", "profile_strength_rationale", "suggestions"],
    "additionalProperties": False,
}


def analyze_profile(context: dict, on_progress=None) -> dict:
    """Computes the LinkedIn gap analysis + suggested rewrites + profile
    strength score directly via the Claude API. `context` is
    build_enhancement_context()'s return value. Returns
    {"profile_strength_score": int, "profile_strength_rationale": str,
    "suggestions": [{"section", "current_text", "suggested_text",
    "rationale"}]}. Raises LLMNotConfigured/LLMCallFailed (via
    llm_client)."""
    client = get_client()
    return call_structured(
        client,
        system=_ANALYZE_SYSTEM_PROMPT,
        user_content=json.dumps(context, indent=2, default=str),
        schema=_ANALYZE_SCHEMA,
        max_tokens=4000,
        effort="high",
        on_progress=on_progress,
        refusal_message="Claude declined to analyze this profile. Try again.",
    )
