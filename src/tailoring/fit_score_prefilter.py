"""Pre-filter for fit_score's expensive paid call (Zahir's explicit build-now
authorization, 2026-08-11, following the day's spend-cap trip - $63.86
spent, 97% of it fit_score, on jobs scoring 0-9 that a cheap check could
have caught). Two layers run before scripts/run_search.py's
score_unscored_jobs() ever calls tailoring.drafting.score_job's paid Opus
call: a free deterministic title-exclusion list, then a cheap Haiku
domain-plausibility check for the dominant "right seniority, wrong field"
pattern found in earlier research (2026-08-11 fit_score distribution
research - CNO/SVP-DME/Country-Director-Humanitarian-style titles, not
catchable by any keyword rule).

Non-negotiable per Zahir: a job the pre-filter skips is NEVER silently
dropped. It stays in jobs.json exactly like any other unscored job (same
as a job that fails to score for any other reason - simply missing
fit_score, still visible to anything that doesn't filter on score), and
every skip is appended to data/jobs/prefilter_log.json so Zahir or a
session can spot-check what's being filtered out. Both layers are
deliberately conservative: a false negative (wrongly filtering a real
match, which never reaches Zahir to judge for himself) is worse than a
false positive (one extra real scoring call) - see this module's own
docstrings on each layer for how that was validated against real data."""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from llm_client import LLMCallFailed, LLMNotConfigured, call_structured, get_client as _client
from security.crypto_store import read_json, write_json
from security.file_lock import locked

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFILTER_LOG_PATH = PROJECT_ROOT / "data" / "jobs" / "prefilter_log.json"

# Cheapest priced model available (src/api_cost.py) - this call must stay
# far cheaper than the fit_score call it's gating, or the pre-filter
# defeats its own purpose.
PREFILTER_MODEL = "claude-haiku-4-5"

# Layer 1: free deterministic title exclusion. Validated 2026-08-11 against
# 2,751 real scored jobs in jobs.json: zero false positives (no matched
# title ever scored >=20, let alone >=40), catches 97/1289 (7.5%) of the
# near-zero-score (0-9) bucket. Deliberately narrow - only professions with
# no plausible path into Zahir's IT/data/cybersecurity-executive profile,
# never a seniority or industry guess (that's what layer 2 and the real
# fit_score rubric are for). A bare "MD" token was deliberately excluded
# from the clinical pattern after real data showed it collides with
# "Managing Director" in finance titles ("Data and AI Chief Operating
# Officer, MD", scored 32 - a real near-miss this module must not repeat).
_EXCLUSION_PATTERNS = [
    (re.compile(r"\bphysician\b|\bnurse practitioner\b|\bregistered nurse\b|\bRN\b|\bphysician assistant\b", re.I), "clinical/medical practitioner role"),
    (re.compile(r"\bfirefighter\b|\bparamedic\b|\bEMT\b", re.I), "emergency services role"),
    (re.compile(r"\bsocial worker\b", re.I), "social work role"),
    (re.compile(r"\bTITLE[- ]?5\b", re.I), "federal Title-5 listing"),
    (re.compile(r"\btechnician\b", re.I), "technician-level role"),
    (re.compile(r"\bvolunteer\b|\bintern(?:ship)?\b|\bstudent\b", re.I), "volunteer/student program"),
]


def _deterministic_exclude(job: dict) -> str | None:
    title = job.get("title") or ""
    for pattern, reason in _EXCLUSION_PATTERNS:
        if pattern.search(title):
            return reason
    return None


def _domain_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "plausible": {
                "type": "boolean",
                "description": "True unless the role is CLEARLY in a wrong professional field for this candidate. When in doubt, true.",
            },
            "reason": {"type": "string", "description": "One short phrase."},
        },
        "required": ["plausible", "reason"],
        "additionalProperties": False,
    }


_DOMAIN_SYSTEM_PROMPT = (
    "You do a fast, coarse plausibility check, NOT a real fit score. Given a "
    "candidate's professional domain summary and a job title/department, "
    "answer whether the job is CLEARLY in the wrong professional field for "
    "this candidate (e.g. a Chief Nursing Officer or VP Construction role "
    "for an IT/data/cybersecurity executive) - not whether it's a strong "
    "match, and not a seniority judgment. Default to plausible=true whenever "
    "there is any real doubt; only say plausible=false when the field "
    "mismatch is obvious from the title/department alone. This is a cost "
    "pre-filter ahead of a real scoring step, not the real score itself: a "
    "wrong 'true' just costs one extra real scoring call, but a wrong "
    "'false' means a real job never reaches the candidate at all to judge "
    "for himself - the far worse mistake. Be conservative."
)


def _domain_plausibility(job: dict, profile: dict) -> dict:
    """Cheap Haiku call - coarse "right field?" check only, not fit_score's
    real rubric. Deliberately minimal prompt (title/department plus one
    domain-summary paragraph), NOT fit_score's full profile-JSON-plus-
    job-JSON shape - the whole point of this layer is staying far cheaper
    than the call it's gating."""
    client = _client()
    framings = profile.get("target_title_framings") or []
    domain_summary = framings[0].get("summary", "") if framings else ""
    content = (
        f"CANDIDATE'S PROFESSIONAL DOMAIN:\n{domain_summary}\n\n"
        f"JOB TITLE:\n{job.get('title', '')}\n"
        f"JOB DEPARTMENT (if known):\n{job.get('department', '')}"
    )
    job_key = (job["source"], job["job_id"]) if job.get("source") and job.get("job_id") else None
    return call_structured(
        client,
        system=_DOMAIN_SYSTEM_PROMPT,
        user_content=content,
        schema=_domain_schema(),
        max_tokens=300,
        model=PREFILTER_MODEL,
        # claude-haiku-4-5 rejects the effort key in output_config outright
        # ("This model does not support the effort parameter") - a real
        # bug found live-validating this exact call 2026-08-11: every one
        # of 126 real domain-check calls in that run failed on this, each
        # silently caught by should_skip_scoring's own fail-open handling,
        # so layer 2 had never actually executed once. See
        # llm_client.call_structured's own docstring for the fix.
        effort=None,
        thinking=False,
        refusal_message="Claude declined to run the domain pre-check.",
        purpose="fit_score_prefilter",
        job_key=job_key,
    )


def should_skip_scoring(job: dict, profile: dict) -> dict | None:
    """Returns {"layer": ..., "reason": ...} if this job should skip the
    expensive fit_score call, or None if it should be scored normally.

    Never touches the job record itself - callers are responsible for
    leaving fit_score unset (identical to any other job that doesn't get
    scored today) and for calling log_prefilter_skip() so the decision is
    auditable, per Zahir's non-negotiable "never silently dropped"
    requirement."""
    reason = _deterministic_exclude(job)
    if reason:
        return {"layer": "deterministic", "reason": reason}

    try:
        result = _domain_plausibility(job, profile)
    except (LLMNotConfigured, LLMCallFailed) as exc:
        # Fail open: a broken/unconfigured/rate-limited pre-filter call must
        # never itself block a real score - the job just falls through to
        # normal scoring, same as if this module didn't exist.
        logger.warning("fit_score prefilter domain check failed, scoring normally: %s", exc)
        return None
    if not result.get("plausible", True):
        return {"layer": "domain_check", "reason": result.get("reason", "")}
    return None


def log_prefilter_skip(job: dict, skip: dict) -> None:
    """Appends a spot-check record. Non-negotiable per Zahir's 2026-08-11
    authorization: every skip decision is recorded here, in addition to the
    job record itself staying in jobs.json untouched, so the pre-filter's
    real-world behavior can always be audited later."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": job.get("source"),
        "job_id": job.get("job_id"),
        "title": job.get("title"),
        "organization": job.get("organization"),
        "layer": skip["layer"],
        "reason": skip["reason"],
    }
    with locked("prefilter_log"):
        existing = read_json(PREFILTER_LOG_PATH, default=[])
        existing.append(entry)
        write_json(PREFILTER_LOG_PATH, existing)
