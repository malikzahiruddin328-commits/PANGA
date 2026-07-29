"""Industry-specific job boards from config/industry_job_boards.yaml - unlike
usajobs.py/company_sites.py, these have no documented API, so this fetches
and parses public search-result HTML pages directly (requests + BeautifulSoup).
No login required, low ToS risk, but fragile - breaks if a site's HTML
structure changes. Standalone script like usajobs.py, no MCP connector needed.

Reconnaissance done 2026-07-29 across 19 candidate sites (see
config/industry_job_boards.yaml and docs/daily-job-search-task.md) found 5
confirmed scrapeable; the rest are bot-blocked (403/429) or JS-rendered
(need a headless browser, not attempted here). Built here: Planet Pharma.
The other 4 confirmed-scrapeable sites (BioSpace, Beacon Hill Life Sciences,
Atrium, GForce Life Sciences) are documented as candidates with their
confirmed-working URLs, not yet built - same pattern, add as needed.
"""

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_planet_pharma_jobs(limit: int = 25) -> list[dict]:
    """No server-side keyword/category filtering attempted - FacetWP's
    category filter only applies via a JS-triggered AJAX refresh, not a
    plain URL param, so this fetches the default listing page and lets
    compatibility scoring do the relevance filtering downstream, same
    pattern as USAJOBS/ZipRecruiter/Dice."""
    response = requests.get("https://careers.planet-pharma.com/job-search/", headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for post in soup.select(".pp-content-post")[:limit]:
        title_link = post.select_one(".job-listing-card-title a")
        if not title_link:
            continue

        details = {}
        for row in post.select(".job-listing-card-detail-row"):
            label = row.select_one(".job-listing-card-detail-label")
            value = row.select_one(".job-listing-card-detail-value")
            if label and value:
                details[label.get_text(strip=True).rstrip(":")] = value.get_text(strip=True)

        posting_url = title_link.get("href")
        jobs.append({
            "source": "Planet Pharma",
            "job_id": posting_url,
            "title": title_link.get_text(strip=True),
            "organization": "Planet Pharma (staffing)",
            "location": details.get("Location"),
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


if __name__ == "__main__":
    for job in fetch_planet_pharma_jobs(limit=10):
        print(f"{job['title']} - {job['location']}")
