"""Build step 4b: standard job boards (ZipRecruiter, Dice, Indeed) via connected
MCP connectors.

Unlike usajobs.py, these sources have no plain HTTP API this script can call on
its own - they're only reachable as MCP tools inside a live Claude session
(Connect them at claude.ai, then Claude calls search_jobs on each). This module
only normalizes their raw results into the same job record shape
usajobs.search_jobs() produces, so downstream code (ranking, storage,
tailoring) doesn't need to know which source a job came from.

Indeed's search tool started working 2026-07-29 (didn't expose one as of
2026-07-28). The community "Jobs and Careers" connector still doesn't expose
a distinct search tool as of 2026-07-29 - revisit later.
"""

import re


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
    # "summary" is a real (if truncated, ~500 char) excerpt of the actual
    # posting text, already present in Dice's own search response - live-
    # confirmed 2026-08-06 while auditing why ATS keyword extraction was
    # coming back empty for non-USAJOBS jobs (see tailoring/ats_score.py).
    # Captured as "description" so drafting.py's _extract_ats_keywords()
    # picks it up the same way it already does for USAJOBS/LinkedIn jobs.
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
        "description": raw.get("summary"),
        "posting_url": raw.get("detailsPageUrl"),
        "apply_url": raw.get("detailsPageUrl"),
    }


def normalize_indeed_jobs(raw_text: str) -> list[dict]:
    """Unlike the other two, Indeed's search_jobs tool returns one formatted
    markdown string for the whole result set, not structured JSON - this
    parses it. The "Job Id" field (e.g. "JOBSEARCH_1") is just a per-response
    sequence number, not stable across searches, so the short code embedded
    in the View Job URL is used as job_id instead - the only stable
    identifier available for dedup."""
    blocks = re.split(r"\*\*Job Title:\*\*", raw_text)[1:]

    def field(block: str, label: str) -> str | None:
        m = re.search(rf"\*\*{label}:\*\*\s*(.+)", block)
        value = m.group(1).strip() if m else None
        return None if value in (None, "N/A") else value

    jobs = []
    for block in blocks:
        block = "**Job Title:**" + block
        url = field(block, "View Job URL")
        compensation = field(block, "Compensation") or ""
        # \.\d+ optional group keeps cents attached to the number they belong
        # to (e.g. "87,509.62" is one number, not "87509" + a stray "62").
        nums = [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", compensation)]
        pay_min = nums[0] if nums else None
        pay_max = nums[1] if len(nums) > 1 else pay_min

        jobs.append({
            "source": "Indeed",
            "job_id": url,
            "title": field(block, "Job Title"),
            "organization": field(block, "Company"),
            "department": None,
            "location": field(block, "Location"),
            "pay_min": pay_min,
            "pay_max": pay_max,
            "posting_url": url,
            "apply_url": url,
        })
    return jobs
