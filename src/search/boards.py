"""Build step 4b: standard job boards (ZipRecruiter, Dice) via connected MCP connectors.

Unlike usajobs.py, these sources have no plain HTTP API this script can call on
its own - ZipRecruiter and Dice are only reachable as MCP tools inside a live
Claude session (Connect them at claude.ai, then Claude calls search_jobs on
each). This module only normalizes their raw results into the same job record
shape usajobs.search_jobs() produces, so downstream code (ranking, storage,
tailoring) doesn't need to know which source a job came from.

Indeed and the community "Jobs and Careers" connector are listed as connected
but did not expose a working search tool as of 2026-07-28 - revisit later.
"""


def normalize_ziprecruiter_job(raw: dict) -> dict:
    salary = raw.get("salary") or {}
    return {
        "source": "ZipRecruiter",
        "job_id": raw.get("job_redirect_url"),
        "title": raw.get("title"),
        "organization": raw.get("company"),
        "department": None,
        "location": raw.get("location"),
        "pay_min": salary.get("min_annual"),
        "pay_max": salary.get("max_annual"),
        "posting_url": raw.get("job_redirect_url"),
        "apply_url": raw.get("job_redirect_url"),
    }


def normalize_dice_job(raw: dict) -> dict:
    return {
        "source": "Dice",
        "job_id": raw.get("guid"),
        "title": raw.get("title"),
        "organization": raw.get("companyName"),
        "department": None,
        "location": (raw.get("jobLocation") or {}).get("displayName"),
        "pay_min": None,
        "pay_max": None,
        "salary_text": raw.get("salary"),
        "posting_url": raw.get("detailsPageUrl"),
        "apply_url": raw.get("detailsPageUrl"),
    }
