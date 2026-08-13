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

A second recon pass 2026-08-03 covered 11 UK oil & gas/energy/nuclear boards
(config/industry_job_boards.yaml's uk_energy_job_boards section) - 9 of 11
turned out blocked/JS-rendered/dead, same self-service-onboarding dead end
as most of the pharma non-matches above; the self-service "raise a ticket"
flow discussed for these is deferred until Bhangi has a real ticket API -
for now, sites that don't clear this module's bar go straight to the
existing manual-entry fallback (same one used for LinkedIn), no ticket.

Rigzone cleared it and is built below (added 2026-08-04) - it's a global
board with no UK-only URL, so fetch_rigzone_jobs() passes the site's own
`sl` (search location) param to narrow results instead of scraping
everything and relying on downstream scoring to filter, unlike the
single-fetch pattern the other functions use.

IChemE Job Board's HTML was confirmed parseable (see fetch_icheme_jobs()
below), but is deliberately NOT wired into scripts/run_search.py - the live
site 403s Python's `requests` library specifically (a WAF fingerprinting
the HTTP/TLS stack, not the User-Agent header - confirmed 2026-08-04, curl
and PowerShell reach the same URL fine). A reminder that "confirmed
scrapeable" recon done via curl/WebFetch doesn't guarantee this module's
actual `requests`-based fetch will work - always live-test the real
function before marking a site built, not just the raw HTML shape.
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


def fetch_biospace_jobs(limit: int = 25, keywords: str = "information technology") -> list[dict]:
    """Real job board (not a staffing firm) - each listing's "recruiter" meta
    field is the actual hiring company, unlike the staffing-firm sources
    below. Clean ?countrycode=US&Page=N pagination confirmed 2026-07-29, so
    this pages through as needed instead of a single fetch like Planet Pharma.

    keywords (added 2026-08-12): the unfiltered default listing is almost
    pure noise for this project's IT/executive focus - real batch checked
    2026-08-12 found ~46% of a real search's total volume came from this
    one source, with only ~2 of 25 sampled records plausibly IT-relevant.
    Confirmed live the same day that jobs.biospace.com/searchjobs/ accepts
    a real server-side `Keywords` query param (same lister__item HTML, but
    the actual result set changes - unfiltered top results were generic
    "Medical Representative"/"Associate Director, Government Pricing"
    roles, "information technology" narrowed to results like "Sr
    Information Technology Operations Analyst"/"Information Technology
    Program Manager"), not a param the site silently ignores. Default value
    keeps existing callers working unchanged; pass keywords="" to restore
    the old unfiltered behavior if ever needed."""
    jobs = []
    page = 1
    while len(jobs) < limit:
        params = {"countrycode": "US", "Page": page}
        if keywords:
            params["Keywords"] = keywords
        response = requests.get(
            "https://jobs.biospace.com/searchjobs/",
            params=params,
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


def fetch_rigzone_jobs(limit: int = 25, location: str = "United States") -> list[dict]:
    """Global oil & gas board, not UK-specific - confirmed 2026-08-04 that
    the site's own location field (?sl=<text>) filters server-side (143 UK
    jobs vs. "thousands" unfiltered), so this passes it instead of scraping
    everything and relying on downstream scoring, unlike the other
    functions in this module. Single fetch of the default (most-recent-
    first) sort - no pagination, same reasoning as Planet Pharma/Beacon
    Hill. Listings are HTML with the employer name and location as two text
    nodes inside one <address> tag separated by a <br/>, not separate
    elements - parsed positionally rather than by selector.

    Default fixed 2026-08-07 (was "United Kingdom") - the daily run
    (scripts/run_search.py) never overrode this, so this had been quietly
    searching UK postings only, not US, against the project's US-only
    merit bar. Fixed regardless of Rigzone currently being disabled in the
    daily run (see run_search.py's _INDUSTRY_BOARD_FETCHERS comment) - the
    function should be correct whenever it's re-enabled, not just when
    someone remembers to also fix this."""
    response = requests.get(
        "https://www.rigzone.com/oil/jobs/search/", params={"sl": location}, headers=HEADERS, timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for card in soup.select("article.update-block")[:limit]:
        title_link = card.select_one("h3 a")
        if not title_link:
            continue

        organization = None
        job_location = None
        addr = card.select_one("address")
        if addr:
            first = addr.contents[0] if addr.contents else None
            if isinstance(first, str):
                organization = first.strip() or None
            br = addr.find("br")
            if br and br.next_sibling:
                job_location = str(br.next_sibling).replace("\xa0", " ").strip() or None

        posting_url = "https://www.rigzone.com" + title_link.get("href", "").strip()
        jobs.append({
            "source": "Rigzone",
            "job_id": posting_url,
            "title": title_link.get_text(strip=True),
            "organization": organization,
            "location": job_location,
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def fetch_icheme_jobs(limit: int = 25) -> list[dict]:
    """IChemE's job board actually lives at jobs.thechemicalengineer.com
    (Madgex platform), not icheme.org - confirmed 2026-08-04. Static HTML,
    small volume (13 jobs on the default listing 2026-08-04), single fetch
    covers everything - no pagination link found. Some listings omit the
    employer name entirely (confidential/agency postings) - left as None
    rather than guessed."""
    response = requests.get("https://jobs.thechemicalengineer.com/jobs/", headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for card in soup.select("article.listing-item")[:limit]:
        title_link = card.select_one(".listing-item__title a.link")
        if not title_link:
            continue

        org_el = card.select_one(".listing-item__info--item-company")
        loc_el = card.select_one(".listing-item__info--item-location")

        posting_url = title_link.get("href", "").strip()
        jobs.append({
            "source": "IChemE Job Board",
            "job_id": posting_url,
            "title": title_link.get_text(strip=True),
            "organization": org_el.get_text(strip=True) if org_el else None,
            "location": loc_el.get_text(strip=True) if loc_el else None,
            "pay_min": None,
            "pay_max": None,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


if __name__ == "__main__":
    for job in fetch_planet_pharma_jobs(limit=10):
        print(f"{job['title']} - {job['location']}")
