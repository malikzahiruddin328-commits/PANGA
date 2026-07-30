"""Industry-specific job boards from config/industry_job_boards.yaml - unlike
usajobs.py/company_sites.py, these have no documented API, so this fetches
and parses public search-result HTML pages directly (requests + BeautifulSoup).
No login required, low ToS risk, but fragile - breaks if a site's HTML
structure changes. Standalone script like usajobs.py, no MCP connector needed.

Reconnaissance done 2026-07-29 across 19 candidate sites (see
config/industry_job_boards.yaml and docs/daily-job-search-task.md) found 5
confirmed scrapeable; the rest are bot-blocked (403/429) or JS-rendered
(need a headless browser, not attempted here). All 5 are now built: Planet
Pharma, BioSpace, Beacon Hill Life Sciences, Atrium, GForce Life Sciences
(added 2026-07-30).
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


def fetch_biospace_jobs(limit: int = 25) -> list[dict]:
    """Real job board (not a staffing firm) - each listing's "recruiter" meta
    field is the actual hiring company, unlike the staffing-firm sources
    below. Clean ?countrycode=US&Page=N pagination confirmed 2026-07-29, so
    this pages through as needed instead of a single fetch like Planet Pharma."""
    jobs = []
    page = 1
    while len(jobs) < limit:
        response = requests.get(
            "https://jobs.biospace.com/searchjobs/",
            params={"countrycode": "US", "Page": page},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items = soup.select("li.lister__item")
        if not items:
            break

        for item in items:
            title_el = item.select_one("h3.lister__header a span")
            link_el = item.select_one("h3.lister__header a")
            if not title_el or not link_el:
                continue

            location = None
            organization = None
            for meta in item.select("ul.lister__meta li"):
                classes = meta.get("class") or []
                if "lister__meta-item--location" in classes:
                    location = meta.get_text(strip=True)
                elif "lister__meta-item--recruiter" in classes:
                    organization = meta.get_text(strip=True)

            posting_url = "https://jobs.biospace.com" + link_el.get("href", "").strip()
            jobs.append({
                "source": "BioSpace",
                "job_id": posting_url,
                "title": title_el.get_text(strip=True),
                "organization": organization,
                "location": location,
                "pay_min": None,
                "pay_max": None,
                "posting_url": posting_url,
                "apply_url": posting_url,
            })
            if len(jobs) >= limit:
                break
        page += 1
    return jobs


def fetch_beacon_hill_jobs(limit: int = 25) -> list[dict]:
    """Beacon Hill Life Sciences - staffing firm, so (like Planet Pharma)
    listings don't name the underlying client company. ?_categories=
    life-sciences already narrows the default listing enough that a single
    fetch (no pagination) is sufficient, same pattern as Planet Pharma."""
    response = requests.get(
        "https://bhsg.com/jobs/job-search/",
        params={"_categories": "life-sciences"},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for card in soup.select("div.job-card")[:limit]:
        title_link = card.select_one("h3.job-card__title a")
        if not title_link:
            continue

        top_items = card.select("div.job-card__meta--top span.job-card__metaItem")
        location = top_items[0].get_text(strip=True) if top_items else None

        posting_url = title_link.get("href", "").strip()
        jobs.append({
            "source": "Beacon Hill Life Sciences",
            "job_id": posting_url,
            "title": title_link.get_text(strip=True),
            "organization": "Beacon Hill Life Sciences (staffing)",
            "location": location,
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def fetch_atrium_jobs(limit: int = 25) -> list[dict]:
    """Atrium - staffing firm. The generic /jobs/ homepage only shows a
    handful of unrelated featured roles; the dedicated
    /jobs/information-technology/ category page (linked from that homepage)
    is where the actual IT-tagged listings live, confirmed 2026-07-29."""
    response = requests.get("https://www.atriumstaff.com/jobs/information-technology/", headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for card in soup.select("article.job-card")[:limit]:
        title_el = card.select_one(".job-card__body h5")
        link_el = card.select_one(".job-card__footer a")
        if not title_el or not link_el:
            continue

        meta_spans = card.select(".job-card__body h6 span")
        location = meta_spans[0].get_text(strip=True) if meta_spans else None
        if location and location.upper().startswith("LOCATION:"):
            location = location.split(":", 1)[1].strip()

        posting_url = link_el.get("href", "").strip()
        jobs.append({
            "source": "Atrium",
            "job_id": posting_url,
            "title": title_el.get_text(strip=True),
            "organization": "Atrium (staffing)",
            "location": location,
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def fetch_gforce_jobs(limit: int = 25) -> list[dict]:
    """GForce Life Sciences - staffing firm. Static HTML with page-number
    pagination (/candidate-careers/page/N/), confirmed 2026-07-29. No
    working IT-only category URL was found (unlike Atrium), so this pages
    through the general listing and lets compatibility scoring filter for
    relevance, same pattern as Planet Pharma/USAJOBS."""
    jobs = []
    page = 1
    while len(jobs) < limit:
        url = "https://www.gforcelifesciences.com/candidate-careers/" if page == 1 \
            else f"https://www.gforcelifesciences.com/candidate-careers/page/{page}/"
        response = requests.get(url, headers=HEADERS, timeout=20)
        if response.status_code == 404:
            break
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        cards = soup.select("div.job-card")
        if not cards:
            break

        for card in cards:
            title_link = card.select_one("h3.job-card-title a")
            if not title_link:
                continue

            meta_items = card.select(".job-card-meta .job-card-meta-item")
            location = meta_items[0].get_text(strip=True) if meta_items else None

            posting_url = title_link.get("href", "").strip()
            jobs.append({
                "source": "GForce Life Sciences",
                "job_id": posting_url,
                "title": title_link.get_text(strip=True),
                "organization": "GForce Life Sciences (staffing)",
                "location": location,
                "pay_min": None,
                "pay_max": None,
                "posting_url": posting_url,
                "apply_url": posting_url,
            })
            if len(jobs) >= limit:
                break
        page += 1
    return jobs


if __name__ == "__main__":
    for job in fetch_planet_pharma_jobs(limit=10):
        print(f"{job['title']} - {job['location']}")
