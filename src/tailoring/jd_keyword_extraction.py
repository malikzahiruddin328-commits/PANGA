"""Standalone, subscription-covered ($0) ATS keyword extraction for a job
posting on its own - no resume draft, no candidate profile needed
(2026-08-17, feature/jd-keyword-taxonomy-gaps, phase 1 of Zahir's
"final set of questions" taxonomy-gap build).

Reuses the SAME extraction logic the paid drafting path already has and
already had to fix multiple times (drafting.ATS_KEYWORDS_SYSTEM_PROMPT,
drafting._ats_keywords_schema(), and its three deterministic post-
processing backstops - drop-years-experience / drop-generic-soft-skill /
strip-degree-in-prefix, CLAUDE.md's known failure pattern #3) - NOT
subscription_resume_qa.py's _resume_prompt() keyword schema lines, which
are a simpler, unfiltered copy with none of those backstops applied to
their output. "Reuse the same extraction schema/prompt shape" is honored
by reusing the actual, currently-correct extraction pipeline (system
prompt + schema + backstop filters) rather than a leaner duplicate of it -
building a second, unfiltered extractor here would silently reintroduce
bugs the paid path already had to fix live (rank-prefix/years-experience/
soft-skill/degree-field over-extraction).

Invoked via tailoring.reasoner_cli's `claude` CLI subprocess mechanism
(same one subscription_resume_qa.py and discuss_and_draft.py already use)
- runs against the Max subscription's own quota, never touches
llm_client.py's paid-API spend cap or cost_log.py's daily cap.

Used by scripts/batch_extract_jd_keywords.py (see that script's own
docstring for the resumable/capped batch runner built around this)."""

import json

from tailoring.drafting import (
    ATS_KEYWORDS_EXTRACTOR_VERSION,
    ATS_KEYWORDS_SYSTEM_PROMPT,
    _ats_keywords_schema,
    _drop_generic_soft_skill_keywords,
    _drop_years_experience_keywords,
    _strip_degree_in_prefix_keywords,
)
from tailoring.reasoner_cli import parse_json_reply, run_claude_cli


def posting_text_for(job: dict) -> str:
    """Same source fields drafting._extract_ats_keywords() reads (title +
    qualification_summary + description, whichever this job record has) -
    kept as its own function so the batch script can cheaply check "is
    there enough real JD text to bother calling the reasoner for" (the
    435-of-1450 filter from the audit) without duplicating this list."""
    return "\n".join(filter(None, [
        job.get("title"), job.get("qualification_summary"), job.get("description"),
    ]))


def _extraction_prompt(posting_text: str) -> str:
    return "\n\n".join([
        ATS_KEYWORDS_SYSTEM_PROMPT,
        f"JOB POSTING:\n{posting_text}",
        (
            "Reply with ONLY a single JSON object (no markdown code fence, no commentary before or after it) "
            "matching this schema:\n" + json.dumps(_ats_keywords_schema(), indent=2)
        ),
    ])


def extract_keywords_via_subscription(job: dict) -> tuple[list, list]:
    """Runs one subscription-covered reasoner call to extract this job's
    own required/preferred ATS keywords, applying the exact same
    deterministic post-processing backstops the paid path applies before
    the result is ever trusted (years-of-experience drop, generic-soft-
    skill drop, degree-in-prefix strip) - so a job extracted through this
    $0 path is held to the identical quality bar as one extracted through
    drafting._extract_ats_keywords(), not a lower one.

    Returns ([], []) without calling the reasoner at all if this job has
    no real posting text (mirrors the paid path's same short-circuit) -
    the caller (batch script) should already be filtering to jobs with
    substantial description text before calling this, but this is a safe
    no-op either way, not a wasted subprocess call.

    Raises ReasonerUnavailable (the `claude` CLI itself unusable - not
    installed/not logged in - a systemic condition, not a per-job one) or
    RuntimeError (this one call's own failure: timeout, non-JSON reply,
    no JSON object found) - neither is swallowed here; the batch script
    decides how to handle each."""
    posting_text = posting_text_for(job)
    if not posting_text.strip():
        return [], []
    reply = run_claude_cli(_extraction_prompt(posting_text))
    data = parse_json_reply(reply)
    required = _strip_degree_in_prefix_keywords(
        _drop_generic_soft_skill_keywords(_drop_years_experience_keywords(data.get("required_keywords") or []))
    )
    preferred = _strip_degree_in_prefix_keywords(
        _drop_generic_soft_skill_keywords(_drop_years_experience_keywords(data.get("preferred_keywords") or []))
    )
    return required, preferred


# Re-exported so callers (the batch script) can stamp the same version
# marker update_job_ats_keywords() expects without importing drafting.py
# directly just for this one constant.
EXTRACTOR_VERSION = ATS_KEYWORDS_EXTRACTOR_VERSION
