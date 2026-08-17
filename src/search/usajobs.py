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


def _log(message: str) -> None:
    # Same print(..., flush=True) convention as job_sources.py's/
    # run_search.py's own _log() - keeps this module's stdout visible in
    # whatever captures it (scheduled script, Streamlit console, etc.)
    # without adding a logging dependency this file didn't already have.
    print(f"[usajobs] {message}", flush=True)


def _requested_category_codes(job_category_code: str) -> set[str]:
    """job_category_code may be a single code ("2210") or several
    semicolon-joined (confirmed live 2026-08-12 in
    search_jobs_by_series_and_grade()'s docstring - that's the real
    USAJOBS separator, not a repeated param). Split defensively either
    way so a single code still works."""
    return {code.strip() for code in job_category_code.split(";") if code.strip()}


def _actual_category_codes(descriptor: dict) -> set[str]:
    """USAJOBS' real response (live-verified 2026-08-17 against
    JobCategoryCode=2210) puts a job's true category/series in
    MatchedObjectDescriptor.JobCategory - a list of {"Name": ..., "Code":
    ...} objects, e.g. [{"Name": "Information Technology Management",
    "Code": "2210"}]. USAJOBS' own server-side JobCategoryCode filter is
    looser than the name implies: real production evidence (2026-08-17)
    showed "Cook" (Bureau of Indian Education), "Staff Accountant" (Army
    National Guard), and "Social Worker" (Air National Guard) coming back
    from a JobCategoryCode=2210 request despite JobCategory not containing
    2210 at all. This reads the field the API actually returns so callers
    can reject those instead of trusting the request filter alone."""
    return {
        entry.get("Code")
        for entry in descriptor.get("JobCategory") or []
        if entry.get("Code")
    }


def _category_matches(descriptor: dict, requested_codes: set[str]) -> bool:
    return bool(_actual_category_codes(descriptor) & requested_codes)


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

    # job_category_code was sent as a *request* filter above, but USAJOBS'
    # own server-side filtering is looser than that implies (real
    # production evidence 2026-08-17: off-domain jobs like "Cook" and
    # "Staff Accountant" came back from a JobCategoryCode=2210 request).
    # Validate each returned job's actual JobCategory against what was
    # requested and drop anything that doesn't really match - deterministic,
    # no AI involved. Keyword-only searches (no job_category_code) have
    # nothing to validate against and pass through unchanged, same as today.
    requested_codes = _requested_category_codes(job_category_code) if job_category_code else None

    jobs = []
    skipped = 0
    for item in data.get("SearchResult", {}).get("SearchResultItems", []):
        d = item.get("MatchedObjectDescriptor", {})
        if requested_codes is not None and not _category_matches(d, requested_codes):
            skipped += 1
            continue
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
    if skipped:
        _log(
            f"search_jobs(job_category_code={job_category_code!r}): skipped "
            f"{skipped} of {skipped + len(jobs)} returned job(s) whose actual "
            f"JobCategory didn't match the requested code(s)"
        )
    return jobs


# Non-GS "executive-tier" pay plan codes to fetch when
# include_executive_grades=True (see search_jobs_by_series_and_grade()).
# Live-verified 2026-08-12 against the real 5-series set below: USAJOBS'
# JobGrade param is a real, working filter (confirmed via a bogus value -
# JobGrade=ZZZBOGUS - returning 0 results, not silently ignored, unlike
# PayPlan which returned the same count regardless of value and was
# rejected as fake). Each code here returned at least one genuinely
# senior/executive-tier, in-domain real posting today: ES (Senior
# Executive Service - "Assistant Chief Information Officer", "Chief
# Product and Technology Officer"), EM (Executive Manager - the real
# CISO EM-2210-00 example from the gap this closes), CP (the real CIO
# CP-graded example from the same gap), AD (Administratively Determined -
# several genuine senior Senate/legislative-branch technical roles), ZP
# (NIST/NOAA senior broadbanded technical roles), SL (Senior Level -
# "Senior Technical Advisor"), FP (Foreign Service - "Foreign Service
# Diplomatic Technology Officer"). Deliberately excludes "GG": live-tested
# and confirmed real, but it's a DoD/military-component broadbanded pay
# plan that covers ALL levels (entry-level "Hardware Engineer... - Entry
# Level" showed up right alongside senior roles), and the API response has
# no numeric grade-level field to separate them the way PayGradeLow/High
# does for GS - so there's no reliable way to keep only the senior GG
# postings without guessing from salary alone, which this module
# deliberately doesn't do (see the client-side-PayGrade-filter rejection
# below). Also excludes ZA/EV/ST/EX/SES: live-tested 2026-08-12 as real,
# accepted values (not silently ignored) but returned zero results in
# today's live data for this series set, so there's no confirmed real
# example to validate them against - can be added later if/when a real
# posting under one of these surfaces.
_EXECUTIVE_GRADE_CODES = ["ES", "EM", "CP", "AD", "ZP", "SL", "FP"]


def search_jobs_by_series_and_grade(
    job_category_codes: list[str],
    pay_grade_low: str,
    pay_grade_high: str,
    location: str | None = None,
    results_per_page: int = 500,
    include_executive_grades: bool = False,
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

    GAP CLOSED 2026-08-12 via include_executive_grades=True: the
    PayGradeLow/PayGradeHigh band above is GS-scale only, so genuine
    IT-domain executive/SES-tier postings sitting on a non-GS pay plan
    (ES/EM/CP/SL/AD/ZP/FP - Senior Executive Service and related senior-
    level tracks) were missed by the GS-band query alone - confirmed real
    examples: "Chief Information Security Officer (CISO), EM-2210-00"
    (grade EM) and "Director, Office of Information Services and Chief
    Information Officer (CIO)" (grade CP), both squarely in-series and
    exactly Zahir's (a 25-year VP/CIO) target seniority.

    Two real approaches were live-tested for this: (1) USAJOBS' own
    PayPlan param - rejected, confirmed to silently accept and ignore any
    value (a bogus PayPlan value returned the identical result count to a
    real one, so it isn't a functioning filter at all - not shipped, per
    "don't fake a filter that doesn't do anything"); (2) fetching the same
    5 series with no PayGrade filter at all and filtering client-side to
    drop GS-11-and-below - also rejected: the API response has no numeric
    GS grade-level field (JobGrade only returns {"Code": "GS"}, with no
    step/level), so a client-side cut would have to infer seniority from
    the salary range alone, which is unreliable (locality pay and
    step-within-grade both shift the range independent of grade level).
    The real, working mechanism turned out to be a third param: JobGrade,
    confirmed live 2026-08-12 to be a genuine filter (a bogus code returns
    0 results, not the unfiltered count) and confirmed semicolon-joins the
    same way JobCategoryCode does. include_executive_grades=True issues a
    second request using _EXECUTIVE_GRADE_CODES (see that constant's own
    docstring for exactly which codes and why) instead of the
    PayGradeLow/High band, and merges the two result sets, de-duped by
    PositionID - so the combined call keeps every GS 12-15 posting from
    the original query plus every live-confirmed-real non-GS executive-
    tier posting in the same 5 series, without silently claiming to also
    catch grade types (see the DoD/military "GG" broadband plan discussion
    on _EXECUTIVE_GRADE_CODES) this module can't reliably separate senior
    from entry-level.

    Real live result 2026-08-12 for the 5-series set at PayGradeLow=12/
    High=15: 211 (base) -> 216 combined, +5 net-new. The de-dupe matters:
    the JobGrade=AD/ZP/SL/FP query alone returns 19 postings, but most
    (14) turn out to already be inside the GS 12-15 band result - those
    pay plans apparently carry an equivalent GS-level classification that
    PayGradeLow/High already matches on. Only ES/EM/CP-graded postings
    (which sit fully outside the GS numeric scale, with no equivalent
    level) are genuinely new - and those 5 are exactly the real gap this
    was built to close, including both confirmed examples above (CISO
    EM-2210-00, CIO CP-graded)."""
    requested_codes = set(job_category_codes)

    def _fetch(extra_params: dict) -> list[dict]:
        params: dict = {
            "ResultsPerPage": results_per_page,
            "JobCategoryCode": ";".join(job_category_codes),
            "HiringPath": "public",
            **extra_params,
        }
        if location:
            params["LocationName"] = location

        response = requests.get(API_URL, headers=_headers(), params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        results = []
        skipped = 0
        for item in data.get("SearchResult", {}).get("SearchResultItems", []):
            d = item.get("MatchedObjectDescriptor", {})
            # Same real-world mismatch as search_jobs() - validate the
            # returned job's actual JobCategory against the requested
            # series instead of trusting USAJOBS' server-side filter.
            if not _category_matches(d, requested_codes):
                skipped += 1
                continue
            remuneration = d.get("PositionRemuneration") or [{}]
            pay = remuneration[0]
            apply_uris = d.get("ApplyURI") or [None]
            results.append({
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
        if skipped:
            _log(
                f"search_jobs_by_series_and_grade(job_category_codes={job_category_codes!r}): "
                f"skipped {skipped} of {skipped + len(results)} returned job(s) whose actual "
                f"JobCategory didn't match the requested series"
            )
        return results

    jobs = _fetch({"PayGradeLow": pay_grade_low, "PayGradeHigh": pay_grade_high})

    if include_executive_grades:
        seen_ids = {j["job_id"] for j in jobs}
        executive_jobs = _fetch({"JobGrade": ";".join(_EXECUTIVE_GRADE_CODES)})
        for job in executive_jobs:
            if job["job_id"] not in seen_ids:
                jobs.append(job)
                seen_ids.add(job["job_id"])

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
