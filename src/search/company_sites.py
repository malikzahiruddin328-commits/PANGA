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
from bs4 import BeautifulSoup
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_URL = "https://api.fda.gov/drug/drugsfda.json"


def _html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True) or None


def _fetch_workday_job_description(tenant: str, site: str, wd_number: int, external_path: str) -> str | None:
    """Real posting text for one Workday requisition - the list-search
    response search_workday_jobs() calls only has title/location/pay, no
    description at all (live-confirmed 2026-08-06 while auditing why ATS
    keyword extraction was coming back empty for every non-USAJOBS job -
    see tailoring/ats_score.py). Same CXS per-requisition detail endpoint
    check_workday_posting_open() below already calls successfully for
    freshness-checking - reusing an endpoint already proven reachable, not
    a new live-fetch risk. Returns None on any failure (network error,
    unexpected shape) rather than raising - one job's description fetch
    failing must not break the whole search."""
    url = f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{external_path}"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return _html_to_text(response.json().get("jobPostingInfo", {}).get("jobDescription"))
    except Exception:
        return None


def _fetch_smartrecruiters_job_description(company_id: str, posting_id: str) -> str | None:
    """Real posting text for one SmartRecruiters posting - same reasoning
    as _fetch_workday_job_description() above, one platform over. The
    detail endpoint's jobAd.sections separates jobDescription from
    qualifications (live-confirmed 2026-08-06) - qualifications is
    concatenated in since it's exactly the required/preferred-skills text
    tailoring/ats_score.py's keyword extraction looks for. Returns None on
    any failure, same fail-soft reasoning as the Workday version."""
    url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{posting_id}"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        sections = response.json().get("jobAd", {}).get("sections", {})
        parts = [
            _html_to_text((sections.get(key) or {}).get("text"))
            for key in ("jobDescription", "qualifications")
        ]
        text = "\n\n".join(p for p in parts if p)
        return text or None
    except Exception:
        return None


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
        external_path = posting.get("externalPath")
        posting_url = base_url + (external_path or "")
        jobs.append({
            "source": company_name,
            "job_id": external_path,
            "title": posting.get("title"),
            "organization": company_name,
            "location": posting.get("locationsText"),
            "pay_min": None,
            "pay_max": None,
            "description": _fetch_workday_job_description(tenant, site, wd_number, external_path),
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

        posting_id = posting.get("id")
        posting_url = f"https://jobs.smartrecruiters.com/{company_name}/{posting_id}"
        jobs.append({
            "source": company_name,
            "job_id": posting_id,
            "title": posting.get("name"),
            "organization": company_name,
            "location": location_text,
            "pay_min": pay_min,
            "pay_max": pay_max,
            "description": _fetch_smartrecruiters_job_description(company_id, posting_id),
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


if __name__ == "__main__":
    companies = fetch_companies(limit=1000)
    print(f"Found {len(companies)} distinct companies in this page.")
    for c in companies[:10]:
        print(f"{c['company']} - e.g. {c['sample_product']}")
