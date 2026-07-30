"""Prospector KPI computations (PRD §16c/§17): Coverage/Activity/Outcome
metrics, computed live from jobs.json/applications.json - nothing new is
stored here.

Two known v1 limitations, both documented rather than hidden:
1. "Per week" trends only cover jobs/applications added since date_added/
   created_at started being recorded (2026-07-30) - older records have
   neither field and are counted in totals but excluded from "last 7 days"
   and any future week-over-week trend.
2. Outcome rates use each application's CURRENT status only - there's no
   per-status history yet, so an application that passed through
   "interview scheduled" before landing on "offer" is counted only under
   offer, not both. Good enough for now; a real history log is a candidate
   PRD §17 Learn Engine input if this simplification turns out to matter.
"""
from datetime import datetime, timedelta, timezone

from ranking.prioritize import weight_for

APPLIED_OR_LATER = {"applied", "interview scheduled", "offer", "rejected"}
RESPONDED = {"interview scheduled", "offer", "rejected"}

# (inclusive lower, exclusive upper, label)
SCORE_BANDS = [
    (90, 101, "90-100"),
    (70, 90, "70-89"),
    (50, 70, "50-69"),
    (30, 50, "30-49"),
    (0, 30, "<30"),
]


def _score_band(fit_score) -> str:
    if fit_score is None:
        return "not scored"
    for lo, hi, label in SCORE_BANDS:
        if lo <= fit_score < hi:
            return label
    return "not scored"


def _job_lookup(jobs: list[dict]) -> dict:
    return {(j.get("source"), j.get("job_id")): j for j in jobs}


def _recent(items: list[dict], date_field: str, days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for item in items:
        raw = item.get(date_field)
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if when >= cutoff:
            out.append(item)
    return out


def coverage_summary(jobs: list[dict]) -> dict:
    """Coverage: how many jobs Panga has found, overall and per channel."""
    by_channel: dict[str, int] = {}
    for j in jobs:
        source = j.get("source", "unknown")
        by_channel[source] = by_channel.get(source, 0) + 1
    return {
        "total_jobs": len(jobs),
        "by_channel": by_channel,
        "added_last_7_days": len(_recent(jobs, "date_added")),
        "untimestamped": sum(1 for j in jobs if not j.get("date_added")),
    }


def activity_summary(applications: list[dict]) -> dict:
    """Activity: how many applications exist, overall and by current status."""
    by_status: dict[str, int] = {}
    for a in applications:
        status = a.get("status") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "total_applications": len(applications),
        "by_status": by_status,
        "created_last_7_days": len(_recent(applications, "created_at")),
        "untimestamped": sum(1 for a in applications if not a.get("created_at")),
    }


def _rates(subset: list[dict]) -> dict:
    n = len(subset)
    if n == 0:
        return {"applied": 0, "response_rate": None, "interview_rate": None, "offer_rate": None, "rejection_rate": None}
    responded = sum(1 for a in subset if a["status"] in RESPONDED)
    interview = sum(1 for a in subset if a["status"] == "interview scheduled")
    offer = sum(1 for a in subset if a["status"] == "offer")
    rejected = sum(1 for a in subset if a["status"] == "rejected")
    return {
        "applied": n,
        "response_rate": round(responded / n, 2),
        "interview_rate": round(interview / n, 2),
        "offer_rate": round(offer / n, 2),
        "rejection_rate": round(rejected / n, 2),
    }


def outcome_summary(applications: list[dict], jobs: list[dict], target_roles: list[dict] | None = None) -> dict:
    """Outcome: response/interview/offer/rejection rates against everything
    that reached "applied" or later, sliced by channel, fit-score band, and
    target-role weight (reuses the same weight_for() lookup Results sorts
    by, so slices stay in sync with whatever's configured in Settings)."""
    lookup = _job_lookup(jobs)
    applied = [a for a in applications if a.get("status") in APPLIED_OR_LATER]

    by_channel: dict[str, list] = {}
    by_score_band: dict[str, list] = {}
    by_role_weight: dict[int, list] = {}
    for a in applied:
        job = lookup.get((a.get("source"), a.get("job_id")), {})
        by_channel.setdefault(a.get("source", "unknown"), []).append(a)
        by_score_band.setdefault(_score_band(job.get("fit_score")), []).append(a)
        tier = weight_for(job.get("title"), target_roles) if target_roles else 0
        by_role_weight.setdefault(tier, []).append(a)

    return {
        "overall": _rates(applied),
        "by_channel": {k: _rates(v) for k, v in by_channel.items()},
        "by_score_band": {k: _rates(v) for k, v in by_score_band.items()},
        "by_role_weight": {k: _rates(v) for k, v in by_role_weight.items()},
    }
