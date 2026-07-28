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


def search_jobs(keyword: str | None = None, location: str | None = None, results_per_page: int = 25) -> list[dict]:
    params = {"ResultsPerPage": results_per_page}
    if keyword:
        params["Keyword"] = keyword
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
        })
    return jobs


if __name__ == "__main__":
    for job in search_jobs(keyword="Chief Information Officer", results_per_page=5):
        print(f"{job['title']} - {job['organization']} - {job['location']}")
