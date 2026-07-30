"""Cross-cutting feedback-loop data gathering for the Learn Engine (PRD
§17). Absorbs what were originally three separate analyses (rejection
diagnosis, KPI-adjacent outcome rates, strategy-tag correlation) into one
mechanism spanning every part of Panga that makes a prediction/decision,
not just Prospector's own tables.

Same "Python gathers, Claude reasons" split as rejection_diagnosis.py and
every other live-reasoning feature in Panga - this module never decides
whether a pattern is real, it just assembles the raw material. The
Prospector tab's "Run analysis" button can't do the reasoning itself
(Streamlit has no path to a live Claude conversation); it prepares this
data and tells Zahir to ask Claude Code to read it.

Known, disclosed gap: LinkedIn recruiter-contact-rate (how often a
recruiter reaches out after a profile edit) has no capture mechanism
anywhere in Panga - there's no way to log "got contacted on LinkedIn"
today, so that Learn Engine input from the original design (PRD §17) is
simply absent below, not silently faked. Would need a small new manual-log
feature to close - flagged, not built here (see PRD §17 build note).
"""
import re


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
