"""Build step 3: USAJOBS.gov API client (public API, no scraping risk).

Docs: https://developer.usajobs.gov/api-reference/get-api-search
Requires a free API key - see README for signup steps. Reads credentials
from .env (USAJOBS_API_KEY, USAJOBS_USER_AGENT_EMAIL).
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_URL = "https://data.usajobs.gov/api/search"


class USAJobsNotConfigured(Exception):
    pass


def _headers() -> dict:
    api_key = os.environ.get("USAJOBS_API_KEY")
    user_agent_email = os.environ.get("USAJOBS_USER_AGENT_EMAIL")
    if not api_key or not user_agent_email:
        raise USAJobsNotConfigured(
            "USAJOBS_API_KEY and USAJOBS_USER_AGENT_EMAIL must be set in .env "
            "(copy .env.example to .env and fill in your free USAJOBS API key)."
        )
    return {
        "Host": "data.usajobs.gov",
        "User-Agent": user_agent_email,
        "Authorization-Key": api_key,
    }


def search_jobs(
    keyword: str | None = None,
    location: str | None = None,
    results_per_page: int = 25,
    who_may_apply: str | None = "public",
    job_category_code: str | None = None,
) -> list[dict]:
    """who_may_apply defaults to "public" (matches the "Open to the public"
    filter on USAJOBS.gov) since Zahir isn't a current federal employee -
    "status" (internal-only) postings aren't ones he's eligible for anyway.
    Pass None to remove the filter and see everything.

    job_category_code filters by USAJOBS job series (e.g. "2210" =
    Information Technology Management) - much more precise than a keyword
    like "Director", which matches any federal director role regardless of
    field (real estate, communications, etc.)."""
    params = {"ResultsPerPage": results_per_page}
    if keyword:
        params["Keyword"] = keyword
    if location:
        params["LocationName"] = location
    if who_may_apply:
        params["WhoMayApply"] = who_may_apply
    if job_category_code:
        params["JobCategoryCode"] = job_category_code

    response = requests.get(API_URL, headers=_headers(), params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for item in data.get("SearchResult", {}).get("SearchResultItems", []):
        d = item.get("MatchedObjectDescriptor", {})
        remuneration = d.get("PositionRemuneration") or [{}]
        pay = remuneration[0]
        apply_uris = d.get("ApplyURI") or [None]
        jobs.append({
            "source": "USAJOBS",
            "job_id": d.get("PositionID"),
            "title": d.get("PositionTitle"),
            "organization": d.get("OrganizationName"),
            "department": d.get("DepartmentName"),
            "location": d.get("PositionLocationDisplay"),
            "pay_min": pay.get("MinimumRange"),
            "pay_max": pay.get("MaximumRange"),
            "posting_url": d.get("PositionURI"),
            "apply_url": apply_uris[0],
            "qualification_summary": d.get("QualificationSummary"),
        })
    return jobs


def search_jobs_by_series_and_grade(
    job_category_codes: list[str],
    pay_grade_low: str,
    pay_grade_high: str,
    location: str | None = None,
    results_per_page: int = 500,
) -> list[dict]:
    """Real, live-validated alternative to the broad keyword search above
    (2026-08-12). search_jobs()'s keyword approach (role names like "CIO",
    "Director") pulls in a lot of same-titled-but-wrong-domain noise (see
    run_search.py's _USAJOBS_SKIP_KEYWORDS comment on "Director" alone).
    This restricts by USAJOBS job series (JobCategoryCode, semicolon-
    joined for multiple series in one call - confirmed live 2026-08-12 this
    is the correct separator, not a repeated param) plus a GS pay-grade
    band, and HiringPath=public rather than WhoMayApply=public - these are
    two different real USAJOBS API params; HiringPath=public is the one
    that actually matches the "Open to the public" checkbox on USAJOBS.gov
    (confirmed by matching a real observed on-site count: 211 for
    JobCategoryCode=1550;1515;0335;2210;0854, PayGradeLow=12,
    PayGradeHigh=15, HiringPath=public, live-tested 2026-08-12).

    Does not replace search_jobs() - kept as a separate function since
    other callers may still want the broad keyword path. Left as a
    separate function rather than folded into search_jobs()'s existing
    who_may_apply/job_category_code params for the same reason: those two
    functions build meaningfully different query shapes (one keyword +
    optional single series, this one a fixed set of series + a pay-grade
    band + a different "open to public" param entirely), not just a couple
    of extra optional args on the same shape.

    KNOWN GAP (confirmed live 2026-08-12, not yet resolved): this
    PayGradeLow/PayGradeHigh band is GS-scale only. Genuine IT-domain
    executive/SES-tier postings - confirmed real examples in the same
    series this searches: "Chief Information Security Officer (CISO),
    EM-2210-00" (grade EM) and "Director, Office of Information Services
    and Chief Information Officer (CIO)" (grade CP) - sit on non-GS grade
    scales (ES/EM/CP/SL/AD/etc., i.e. Senior Executive Service and related
    senior-level tracks) and are NOT captured by a GS 12-15 band, so this
    function alone would miss them even though they're squarely in-domain
    and exactly the seniority level Zahir (a 25-year VP/CIO) is targeting.
    A same-day broad-keyword-vs-series+grade comparison found 106 postings
    the old keyword approach surfaced that this new query missed - most
    are genuine false-domain noise (air traffic control, law enforcement
    training, etc. that happened to match "SVP"/"VP" as a substring), but
    at least the CISO/CIO examples above are real, in-domain, non-noise
    misses. This function does not attempt to also cover the SES/senior-
    level band - flagged here rather than silently shipped as a complete
    replacement; a caller wanting SES-level coverage still needs a
    separate query (e.g. a keyword search, or PayGradeLow/High left unset)
    until that's built."""
    params: dict = {
        "ResultsPerPage": results_per_page,
        "JobCategoryCode": ";".join(job_category_codes),
        "PayGradeLow": pay_grade_low,
        "PayGradeHigh": pay_grade_high,
        "HiringPath": "public",
    }
    if location:
        params["LocationName"] = location

    response = requests.get(API_URL, headers=_headers(), params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for item in data.get("SearchResult", {}).get("SearchResultItems", []):
        d = item.get("MatchedObjectDescriptor", {})
        remuneration = d.get("PositionRemuneration") or [{}]
        pay = remuneration[0]
        apply_uris = d.get("ApplyURI") or [None]
        jobs.append({
            "source": "USAJOBS",
            "job_id": d.get("PositionID"),
            "title": d.get("PositionTitle"),
            "organization": d.get("OrganizationName"),
            "department": d.get("DepartmentName"),
            "location": d.get("PositionLocationDisplay"),
            "pay_min": pay.get("MinimumRange"),
            "pay_max": pay.get("MaximumRange"),
            "posting_url": d.get("PositionURI"),
            "apply_url": apply_uris[0],
            "qualification_summary": d.get("QualificationSummary"),
        })
    return jobs


def check_position_open(position_id: str) -> bool:
    """Freshness check (added 2026-08-05, PRD-adjacent "closed by employer"
    automation): USAJOBS' own search API accepts PositionID as a filter, same
    as Keyword/LocationName above - a closed/expired announcement simply
    stops appearing in results, no separate status endpoint needed. Omits
    WhoMayApply so a position that closed to the public but is still
    internally open isn't misreported (Zahir isn't eligible for those anyway,
    but "still exists" is the only question this function answers)."""
    params = {"PositionID": position_id, "ResultsPerPage": 1}
    response = requests.get(API_URL, headers=_headers(), params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return int(data.get("SearchResult", {}).get("SearchResultCount", 0)) > 0


if __name__ == "__main__":
    for job in search_jobs(job_category_code="2210", results_per_page=5):
        print(f"{job['title']} - {job['organization']} - {job['location']}")
