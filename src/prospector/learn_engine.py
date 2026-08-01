"""Cross-cutting feedback-loop data gathering for the Learn Engine (PRD
§17). Absorbs what were originally three separate analyses (rejection
diagnosis, KPI-adjacent outcome rates, strategy-tag correlation) into one
mechanism spanning every part of Panga that makes a prediction/decision,
not just Prospector's own tables.

gather_learn_engine_input() never decides whether a pattern is real, it
just assembles the raw material; analyze() (native-packaging branch,
2026-07-31) reasons over it via a direct Anthropic API call, same
conversion as rejection_diagnosis.py's diagnose() - the old "prepare data,
go ask Claude Code" button was the exact friction point already fixed for
document drafting and Prospector Score.

Known, disclosed gap: LinkedIn recruiter-contact-rate (how often a
recruiter reaches out after a profile edit) has no capture mechanism
anywhere in Panga - there's no way to log "got contacted on LinkedIn"
today, so that Learn Engine input from the original design (PRD §17) is
simply absent below, not silently faked. Would need a small new manual-log
feature to close - flagged, not built here (see PRD §17 build note).
"""
import json
import re

from llm_client import call_structured, get_client


def _normalize_company(name: str) -> str:
    """Light punctuation/whitespace normalization for company-name
    matching - same approach as linkedin/connections.py's helper (kept
    separate rather than a cross-package import for two near-identical
    tiny functions)."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[.,]", "", name.lower())).strip()


def _job_lookup(jobs: list[dict]) -> dict:
    return {(j.get("source"), j.get("job_id")): j for j in jobs}


def gather_learn_engine_input(
    applications: list[dict],
    jobs: list[dict],
    target_accounts: list[dict],
    outreach: list[dict],
    interview_prep_records: list[dict],
) -> dict:
    lookup = _job_lookup(jobs)

    scoring_vs_outcome = []
    for a in applications:
        job = lookup.get((a.get("source"), a.get("job_id")), {})
        if "fit_score" not in job:
            continue
        scoring_vs_outcome.append({
            "title": job.get("title"), "organization": job.get("organization"),
            "channel": a.get("source"), "fit_score": job.get("fit_score"),
            "status": a.get("status"), "strategy_tag": a.get("strategy_tag"),
        })

    job_orgs_normalized = {_normalize_company(j.get("organization")) for j in jobs if j.get("organization")}
    target_account_vs_outcome = []
    for acc in target_accounts:
        norm = _normalize_company(acc["company_name"])
        has_real_posting = any(norm in jo or jo in norm for jo in job_orgs_normalized if jo)
        target_account_vs_outcome.append({
            "company_name": acc["company_name"],
            "status": acc["status"],
            "signal_types": sorted({s["signal_type"] for s in acc["signals"]}),
            "signal_count": len(acc["signals"]),
            "real_posting_appeared_since": has_real_posting,
        })

    outreach_vs_outcome = [{
        "channel": o["channel"], "status": o["status"], "strategy_tag": o.get("strategy_tag"),
        "anchor": o.get("target_account_name") or f"{o.get('job_source')}:{o.get('job_id')}",
    } for o in outreach]

    interview_outcomes = []
    for record in interview_prep_records:
        job = lookup.get((record.get("source"), record.get("job_id")), {})
        for round_ in record.get("rounds", []):
            if round_.get("outcome"):
                interview_outcomes.append({
                    "organization": job.get("organization"),
                    "round_label": round_["round_label"],
                    "outcome": round_["outcome"],
                    "outcome_notes": round_.get("outcome_notes"),
                })

    return {
        "scoring_vs_outcome": scoring_vs_outcome,
        "target_account_vs_outcome": target_account_vs_outcome,
        "outreach_vs_outcome": outreach_vs_outcome,
        "interview_outcomes": interview_outcomes,
        "known_gaps": [
            "LinkedIn recruiter-contact-rate has no capture mechanism yet - "
            "there's no way to log 'got contacted on LinkedIn after a profile "
            "edit', so that input from the original design is absent here, "
            "not silently faked.",
        ],
    }


_ANALYZE_SYSTEM_PROMPT = """You are a self-learning feedback loop over every prediction a job-search tool makes for its candidate - fit scoring, target-account qualification, outreach channel choice, strategy tags, interview prep approach. Reason over the real correlation data given (scoring vs. outcome, target-account vs. real postings, outreach vs. response, interview outcomes) and surface genuine cross-cutting patterns - never invent one that isn't in the data. With few real outcomes tracked so far, a thin, low-confidence result that says so honestly is correct, not something to inflate. This is recommend-only: never propose changing a score threshold or setting as if you'd actually done it - only suggest it as something the candidate could consider."""

_ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {
            "type": "string",
            "description": "2-5 sentences: what cross-cutting pattern(s), if any, are visible across scoring/target-accounts/outreach/interviews, and how confident that reading is given how much real data exists.",
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "0-5 concrete, specific things to consider adjusting (e.g. a scoring weight, an outreach channel, a strategy tag) - empty list if no real pattern is visible yet. Recommend-only phrasing, never stated as already done.",
        },
    },
    "required": ["narrative", "recommendations"],
    "additionalProperties": False,
}


def analyze(learn_input: dict, on_progress=None) -> dict:
    """Computes the Learn Engine's cross-cutting analysis directly via the
    Claude API. Returns {"narrative": str, "recommendations": [str]}.
    Raises LLMNotConfigured/LLMCallFailed (via llm_client)."""
    client = get_client()
    return call_structured(
        client,
        system=_ANALYZE_SYSTEM_PROMPT,
        user_content=json.dumps(learn_input, indent=2, default=str),
        schema=_ANALYZE_SCHEMA,
        max_tokens=1500,
        effort="medium",
        thinking=False,
        on_progress=on_progress,
        refusal_message="Claude declined to run the analysis. Try again.",
    )
