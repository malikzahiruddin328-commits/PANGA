"""Build step 4c: lifesciences/pharma company sourcing via openFDA (public API,
no scraping, no API key required for light use - same shape as usajobs.py).

Docs: https://open.fda.gov/apis/drug/drugsfda/
Pulls distinct sponsor (company) names from FDA drug application records.
This produces a company LIST, not job listings - see search_workday_jobs()
and search_smartrecruiters_jobs() below for actually searching a specific
company's openings.
Optional FDA_API_KEY in .env raises the rate limit; unset works fine for
periodic runs.

PubMed (author/institution affiliations) was considered as a second company
source per the PRD, but openFDA's sponsor_name field is a much cleaner
signal - revisit PubMed only if openFDA coverage proves too narrow.

COMPANY-SITE JOB SEARCH (added 2026-07-29): rather than scraping each
company's career page HTML (fragile, often blocked), this calls the same
JSON APIs the career page's own JavaScript calls - a company using Workday
or SmartRecruiters as their ATS can be searched cleanly with zero HTML
parsing. Confirmed working against real companies from Zahir's background:
Eisai and IQVIA (Workday), AbbVie (SmartRecruiters). Not every company uses
one of these two platforms - this covers a large share of large employers,
not all of them.
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_URL = "https://api.fda.gov/drug/drugsfda.json"


def _params(limit: int, skip: int, search: str | None) -> dict:
    params = {"limit": limit, "skip": skip}
    if search:
        params["search"] = search
    api_key = os.environ.get("FDA_API_KEY")
    if api_key:
        params["api_key"] = api_key
    return params


def fetch_companies(limit: int = 1000, skip: int = 0, search: str | None = None) -> list[dict]:
    """Returns distinct sponsor companies seen in one page of FDA drug
    application records. `search` uses openFDA's Lucene-style syntax, e.g.
    'sponsor_name:"PFIZER"'. Increase `skip` to page through the full set."""
    response = requests.get(API_URL, params=_params(limit, skip, search), timeout=30)
    response.raise_for_status()
    data = response.json()

    companies: dict[str, dict] = {}
    for item in data.get("results", []):
        name = item.get("sponsor_name")
        if not name or name in companies:
            continue
        products = item.get("products") or [{}]
        companies[name] = {
            "source": "openFDA",
            "company": name,
            "sample_product": products[0].get("brand_name"),
            "sample_application_number": item.get("application_number"),
        }
    return list(companies.values())


def search_workday_jobs(company_name: str, tenant: str, site: str, wd_number: int, keyword: str = "", limit: int = 20, applied_facets: dict | None = None) -> list[dict]:
    """tenant/site/wd_number come from the company's own myworkdayjobs.com
    URL, e.g. "eisai.wd5.myworkdayjobs.com/eisai" -> tenant="eisai", site="eisai",
    wd_number=5. Find these by web-searching "<company> careers myworkdayjobs.com"
    - there's no directory of which companies use Workday.

    applied_facets passes through to Workday's own CXS "appliedFacets" filter
    (server-side, same mechanism the career site's own filter UI uses) - e.g.
    {"Location_Country": ["<facet-id>"]} to restrict to one country. Facet
    IDs are tenant-specific and not documented; discover them by POSTing an
    empty search to the /jobs endpoint above and reading the "facets" list in
    the response (each facet's "values" gives {descriptor, id, count})."""
    url = f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": applied_facets or {}, "limit": limit, "offset": 0, "searchText": keyword}
    response = requests.post(url, json=body, timeout=20)
    response.raise_for_status()
    data = response.json()

    base_url = f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/{site}"
    jobs = []
    for posting in data.get("jobPostings", []):
        posting_url = base_url + (posting.get("externalPath") or "")
        jobs.append({
            "source": company_name,
            "job_id": posting.get("externalPath"),
            "title": posting.get("title"),
            "organization": company_name,
            "location": posting.get("locationsText"),
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def search_smartrecruiters_jobs(company_name: str, company_id: str, keyword: str = "", limit: int = 20) -> list[dict]:
    """company_id is the identifier in the company's careers.smartrecruiters.com/{company_id}
    URL, e.g. "abbvie". Public posting API, no auth required - meant for job
    board syndication, this is its intended use, not scraping."""
    url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
    params = {"limit": limit}
    if keyword:
        params["q"] = keyword
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for posting in data.get("content", []):
        location = posting.get("location") or {}
        location_text = ", ".join(filter(None, [location.get("city"), location.get("region"), location.get("country")])) or None

        pay_min = pay_max = None
        for field in posting.get("customField") or []:
            if field.get("fieldLabel") == "Salary Min":
                pay_min = field.get("valueLabel")
            elif field.get("fieldLabel") == "Salary Max":
                pay_max = field.get("valueLabel")

        posting_url = f"https://jobs.smartrecruiters.com/{company_name}/{posting.get('id')}"
        jobs.append({
            "source": company_name,
            "job_id": posting.get("id"),
            "title": posting.get("name"),
            "organization": company_name,
            "location": location_text,
            "pay_min": pay_min,
            "pay_max": pay_max,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def check_smartrecruiters_posting_open(company_id: str, posting_id: str) -> bool:
    """Freshness check (added 2026-08-05): SmartRecruiters' public API also
    exposes a single-posting detail endpoint, same base as
    search_smartrecruiters_jobs() above - a filled/withdrawn posting either
    404s or comes back with status != "PUBLISHED"."""
    url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{posting_id}"
    response = requests.get(url, timeout=20)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return response.json().get("status") == "PUBLISHED"


def check_workday_posting_open(tenant: str, site: str, wd_number: int, external_path: str) -> bool:
    """Freshness check (added 2026-08-05): same Workday CXS JSON API as
    search_workday_jobs() above, just the per-requisition detail path instead
    of the list-search path - a closed requisition 404s here rather than
    returning a job body."""
    url = f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{external_path}"
    response = requests.get(url, timeout=20)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return bool(response.json().get("jobPostingInfo"))


def search_greenhouse_jobs(company_name: str, board_token: str, limit: int = 50) -> list[dict]:
    """board_token is the identifier in the company's own
    boards.greenhouse.io/{board_token} careers URL, e.g. "stripe". Public
    job board API, no auth required - meant for syndication, same
    intended-use basis as search_smartrecruiters_jobs() above.

    No server-side keyword search on this endpoint (unlike Workday/
    SmartRecruiters) - it always returns the company's whole open-jobs
    list. Deliberately NOT called per-role in a loop the way Workday/
    SmartRecruiters are (see run_search.py's search_ats_boards()) -
    fetching the same full board once per target role would be wasted
    requests for zero extra data, same reasoning as the industry-board
    fetchers in industry_boards.py. Compatibility scoring, not this
    function, is what actually filters for relevance."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for posting in data.get("jobs", [])[:limit]:
        posting_url = posting.get("absolute_url")
        jobs.append({
            "source": company_name,
            "job_id": str(posting.get("id")),
            "title": posting.get("title"),
            "organization": company_name,
            "location": (posting.get("location") or {}).get("name"),
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def search_lever_jobs(company_name: str, company_slug: str, limit: int = 50) -> list[dict]:
    """company_slug is the identifier in the company's own
    jobs.lever.co/{company_slug} careers URL, e.g. "netflix". Public
    postings API, no auth required. Same "no server-side keyword search,
    fetch once per company not per role" reasoning as
    search_greenhouse_jobs() above - see run_search.py's
    search_ats_boards()."""
    url = f"https://api.lever.co/v0/postings/{company_slug}"
    response = requests.get(url, params={"mode": "json"}, timeout=20)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for posting in data[:limit]:
        posting_url = posting.get("hostedUrl")
        location = (posting.get("categories") or {}).get("location")
        jobs.append({
            "source": company_name,
            "job_id": str(posting.get("id")),
            "title": posting.get("text"),
            "organization": company_name,
            "location": location,
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting.get("applyUrl") or posting_url,
        })
    return jobs


def check_greenhouse_posting_open(board_token: str, job_id: str) -> bool:
    """Freshness check: Greenhouse's single-job endpoint - a filled/closed
    requisition 404s here rather than returning a job body (confirmed
    2026-08-05, same shape as check_workday_posting_open())."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}"
    response = requests.get(url, timeout=20)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def check_lever_posting_open(company_slug: str, posting_id: str) -> bool:
    """Freshness check: Lever's single-posting endpoint - a filled/closed
    posting 404s here (confirmed 2026-08-05, same shape as
    check_smartrecruiters_posting_open())."""
    url = f"https://api.lever.co/v0/postings/{company_slug}/{posting_id}"
    response = requests.get(url, params={"mode": "json"}, timeout=20)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


if __name__ == "__main__":
    companies = fetch_companies(limit=1000)
    print(f"Found {len(companies)} distinct companies in this page.")
    for c in companies[:10]:
        print(f"{c['company']} - e.g. {c['sample_product']}")
