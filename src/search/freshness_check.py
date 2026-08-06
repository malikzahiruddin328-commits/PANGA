"""Automated "closed by employer" detection (added 2026-08-05, PRD-adjacent -
see ui/app.py's Results-tab CLOSED filter and tailoring/applications.py's
status docstring for the status itself, which was previously only ever set
by hand). Runs once/day as scripts/run_search.py's STEP 8.

Scope, per Zahir's explicit ask: only jobs already scored >=70 (everything
below that is hidden by the Results tab's own filter anyway - checking it
would be wasted work and wasted requests against other people's sites), and
skips any job whose application status already reflects a real decision
(applied/interview/offer/rejected/not interested/already closed) - those are
Zahir's own knowledge, this shouldn't second-guess or overwrite them.

Detection prefers an official structured signal over scraping wherever one
exists - USAJOBS' own search API, SmartRecruiters' public posting endpoint
(AbbVie), Workday's job-detail endpoint (Eisai, IQVIA) - all APIs this
codebase already calls for search, just re-queried per-posting. Only the
boards with no API at all (see industry_boards.py's own docstring on which
sites are scrape-only) fall back to fetching the single posting page and
pattern-matching a per-site "closed" phrase; a 404 is treated as closed too
(most boards remove a filled/expired posting's page outright).

check_posting_open()'s three-value result matters: True/False are confirmed,
None means "couldn't tell" (network error, timeout, no phrase match on an
unrecognized site, or - per industry_boards.py's IChemE note - a WAF quietly
blocking `requests` specifically). Only a confirmed False marks a job
closed; None is treated as still-open (fail-safe against hiding a real
posting on a flaky check) and simply left alone until tomorrow's run.
"""

import time

import requests

from search import company_sites, job_sources, job_store, usajobs
from tailoring import applications

HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_TIMEOUT_SECONDS = 15

# Delay applied after every check, regardless of which site it hit - keeps
# each site to at most one request every N seconds even though jobs aren't
# grouped by source in the store, without needing per-domain bookkeeping.
API_CHECK_DELAY_SECONDS = 1.0
SCRAPE_CHECK_DELAY_SECONDS = 2.5

MIN_FIT_SCORE = 70
CLOSED_STATUS = "closed by employer"

# Statuses that already reflect a real decision (Zahir's own, or a prior
# freshness-check run) - never overwritten by this automation.
_SKIP_STATUSES = {
    CLOSED_STATUS,
    "not interested",
    "not-interested",
    "applied",
    "interview scheduled",
    "offer",
    "rejected",
}

# Per-site "posting closed" phrase, matched case-insensitively against the
# fetched page text. Starts empty for sites we haven't confirmed real
# wording on yet (Dice and the industry boards) - those rely on the
# 404-on-removal heuristic alone until a real closure is observed and the
# wording can be added here, same as LinkedIn/ZipRecruiter were confirmed.
#
# ZipRecruiter and Indeed postings both live-tested as always returning None
# here (confirmed 2026-08-05) - both domains 403 Python's `requests` library
# specifically on the exact stored posting_url (a WAF fingerprinting the
# HTTP/TLS stack, not headers; curl/browser reach the same URL fine), the
# same block already documented for IChemE in industry_boards.py. Their
# confirmed closed-phrases above are correct but currently unreachable by
# this fetcher - fail-safe means these two sources just never get marked
# closed automatically until that's solved, not a bug in the phrase match.
_CLOSED_PHRASES = {
    "LinkedIn": ["no longer accepting applications"],
    "ZipRecruiter": ["this job post has expired"],
}


def _check_usajobs(job: dict) -> bool | None:
    try:
        return usajobs.check_position_open(job["job_id"])
    except Exception:  # noqa: BLE001 - one job's failure shouldn't stop the run
        return None


def _check_smartrecruiters(job: dict, company: dict) -> bool | None:
    try:
        return company_sites.check_smartrecruiters_posting_open(company["company_id"], job["job_id"])
    except Exception:  # noqa: BLE001
        return None


def _check_workday(job: dict, company: dict) -> bool | None:
    try:
        return company_sites.check_workday_posting_open(
            company["tenant"], company["site"], company["wd_number"], job["job_id"],
        )
    except Exception:  # noqa: BLE001
        return None


def build_api_source_lookup() -> dict:
    """source (company_name) -> (check function, company dict), built fresh
    from config/job_sources.yaml each run rather than a hardcoded table -
    so a company added via the Settings tab's "Job-board sources" section
    gets freshness-checked too, without a matching code change here. Built
    once per check_and_mark_closed_postings() run, not per-job, since this
    would otherwise mean one job_sources.yaml read+lock per posting."""
    sources = job_sources.load_job_sources()
    lookup = {}
    for company in sources["workday"]:
        lookup[company["company_name"]] = (_check_workday, company)
    for company in sources["smartrecruiters"]:
        lookup[company["company_name"]] = (_check_smartrecruiters, company)
    return lookup


def _check_via_page_text(job: dict) -> bool | None:
    url = job.get("posting_url")
    if not url:
        return None
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException:
        return None
    if response.status_code == 404:
        return False
    if not response.ok:
        return None

    phrases = _CLOSED_PHRASES.get(job["source"], [])
    page_text = response.text.lower()
    if any(phrase in page_text for phrase in phrases):
        return False
    return True


def check_posting_open(job: dict, api_sources: dict) -> bool | None:
    """api_sources is USAJOBS plus whatever build_api_source_lookup()
    resolved from config/job_sources.yaml for this run - everything else
    (industry boards, LinkedIn, ZipRecruiter, ...) falls back to a real
    page fetch, which gets the longer delay (see the caller)."""
    source = job.get("source")
    if source == "USAJOBS":
        return _check_usajobs(job)
    if source in api_sources:
        check_fn, company = api_sources[source]
        return check_fn(job, company)
    return _check_via_page_text(job)


def check_and_mark_closed_postings(min_fit_score: int = MIN_FIT_SCORE) -> tuple[int, int]:
    """Returns (checked, marked_closed). Iterates a fixed snapshot of the
    >=min_fit_score jobs taken once at the start - not O(n^2), and immune to
    the store growing mid-run since save_jobs() only appends."""
    candidates = [j for j in job_store.load_jobs() if (j.get("fit_score") or 0) >= min_fit_score]
    api_sources = build_api_source_lookup()

    checked = 0
    marked = 0
    for job in candidates:
        source, job_id = job.get("source"), job.get("job_id")
        if not source or not job_id:
            continue
        current = applications.get_application(source, job_id)
        if current and current.get("status") in _SKIP_STATUSES:
            continue

        checked += 1
        is_api_source = source == "USAJOBS" or source in api_sources
        try:
            is_open = check_posting_open(job, api_sources)
        except Exception:  # noqa: BLE001 - one posting's failure shouldn't stop the run
            is_open = None
        time.sleep(API_CHECK_DELAY_SECONDS if is_api_source else SCRAPE_CHECK_DELAY_SECONDS)

        if is_open is False:
            applications.upsert_application(source, job_id, status=CLOSED_STATUS)
            marked += 1

    return checked, marked
