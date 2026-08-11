"""Rejection-pattern diagnosis (PRD §16c): assembles rejected and
not-interested-with-reason applications, enriched with their job's
channel/score/rationale, then reasons over it via a direct Anthropic API
call (native-packaging branch, 2026-07-31 - same conversion, same reason,
as tailoring/drafting.py and prospector/prospector_score.py: the old
"prepare data, go ask Claude Code" button was the exact friction point
already fixed for document drafting and Prospector Score).

gather_diagnosis_input() still never decides whether there's a real
pattern on its own - it just assembles the raw material; diagnose() is the
one place that reasoning happens now, and it never invents a pattern that
isn't in the data (a thin/no-pattern result is a correct, honest
diagnosis, not a bug).
"""

import json

from llm_client import call_structured, get_client

NOT_INTERESTED = ("not interested", "not-interested")  # handle both forms - see applications.py note


def _job_lookup(jobs: list[dict]) -> dict:
    return {(j.get("source"), j.get("job_id")): j for j in jobs}


def _enrich(app: dict, job: dict) -> dict:
    return {
        "source": app.get("source"),
        "job_id": app.get("job_id"),
        "title": job.get("title"),
        "organization": job.get("organization"),
        "channel": app.get("source"),
        "fit_score": job.get("fit_score"),
        "fit_rationale": job.get("fit_rationale"),
        "status": app.get("status"),
        "skip_reason": app.get("skip_reason"),
        "status_updated_at": app.get("status_updated_at"),
    }


def gather_diagnosis_input(applications: list[dict], jobs: list[dict]) -> dict:
    """Returns the rejected applications and the not-interested-with-reason
    ones, each enriched with job details, plus rollup counts. Deliberately
    no "is this enough data" threshold here - that judgment belongs to
    Claude's live reasoning pass, not a hardcoded Python rule."""
    lookup = _job_lookup(jobs)

    rejected = [
        _enrich(a, lookup.get((a.get("source"), a.get("job_id")), {}))
        for a in applications if a.get("status") == "rejected"
    ]
    not_interested_with_reason = [
        _enrich(a, lookup.get((a.get("source"), a.get("job_id")), {}))
        for a in applications if a.get("status") in NOT_INTERESTED and a.get("skip_reason")
    ]

    return {
        "rejected": rejected,
        "not_interested_with_reason": not_interested_with_reason,
        "rejected_count": len(rejected),
        "not_interested_with_reason_count": len(not_interested_with_reason),
    }


_DIAGNOSIS_SYSTEM_PROMPT = """You are looking for real clustering/patterns in why a job-search candidate's applications aren't landing - by fit-score band, channel, role type/title pattern, organization type, or the reasons he's given for marking postings not-interested. Reason over the actual data given, never invent a pattern that isn't there - with few real data points, an honest "not enough data yet to see a real pattern" is the correct answer, not something to work around by overfitting to noise. Be specific and plain-language, not generic career advice."""

_DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {
            "type": "string",
            "description": "2-5 sentences: what pattern(s), if any, are visible in this real data, and how confident that reading is given how much data exists.",
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "0-5 concrete, specific things to actually try differently, grounded in the pattern found - empty list if no real pattern is visible yet.",
        },
    },
    "required": ["narrative", "recommendations"],
    "additionalProperties": False,
}


def diagnose(diagnosis_input: dict, on_progress=None) -> dict:
    """Computes the rejection-pattern diagnosis directly via the Claude API.
    Returns {"narrative": str, "recommendations": [str]}. Raises
    LLMNotConfigured/LLMCallFailed (via llm_client) same as every other
    direct-API module in Panga."""
    client = get_client()
    return call_structured(
        client,
        system=_DIAGNOSIS_SYSTEM_PROMPT,
        user_content=json.dumps(diagnosis_input, indent=2, default=str),
        schema=_DIAGNOSIS_SCHEMA,
        max_tokens=1500,
        effort="medium",
        thinking=False,
        on_progress=on_progress,
        refusal_message="Claude declined to run the diagnosis. Try again.",
        purpose="rejection_diagnosis",
    )
