"""Build step 4c: lifesciences/pharma company sourcing via openFDA (public API,
no scraping, no API key required for light use - same shape as usajobs.py).

Docs: https://open.fda.gov/apis/drug/drugsfda/
Pulls distinct sponsor (company) names from FDA drug application records.
This produces a company LIST, not job listings - the next step (not yet
built) would be searching each company's own career site for openings.
Optional FDA_API_KEY in .env raises the rate limit; unset works fine for
periodic runs.

PubMed (author/institution affiliations) was considered as a second company
source per the PRD, but openFDA's sponsor_name field is a much cleaner
signal - revisit PubMed only if openFDA coverage proves too narrow.
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


if __name__ == "__main__":
    companies = fetch_companies(limit=1000)
    print(f"Found {len(companies)} distinct companies in this page.")
    for c in companies[:10]:
        print(f"{c['company']} - e.g. {c['sample_product']}")
