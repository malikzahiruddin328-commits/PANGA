"""Rejection-pattern diagnosis (PRD §16c): assembles rejected and
not-interested-with-reason applications, enriched with their job's
channel/score/rationale, as input for Claude to reason over live in
conversation.

Same "Python gathers, Claude reasons" split as tailoring/scoring and the
existing skip-reason feedback loop (§13) - this module never decides
whether there's a real pattern, it just assembles the raw material. The
on-demand button on the Prospector tab can't run the analysis itself
(Streamlit has no path to a live Claude conversation, same constraint as
"Start tailoring"/"Request documents") - it prepares this data and tells
Zahir to ask Claude Code to read it.
"""

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
