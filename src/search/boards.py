"""Build step 4b: standard job boards (ZipRecruiter, Dice, Indeed) via connected
MCP connectors.

Unlike usajobs.py, these sources have no plain HTTP API this script can call on
its own - they're only reachable as MCP tools inside a live Claude session
(Connect them at claude.ai, then Claude calls search_jobs on each). This module
only normalizes their raw results into the same job record shape
usajobs.search_jobs() produces, so downstream code (ranking, storage,
tailoring) doesn't need to know which source a job came from.

Indeed's search tool started working 2026-07-29 (didn't expose one as of
2026-07-28). The community "Jobs and Careers" connector still doesn't expose
a distinct search tool as of 2026-07-29 - revisit later.

DICE UPDATE (2026-08-06): the "no plain HTTP API" framing above no longer
applies to Dice specifically - investigated per Zahir's ask (was ZipRecruiter/
Indeed also worth a fresh look? No - re-confirmed both are still MCP-only,
see docs/native-packaging-scope.md, not re-litigated here). Dice's own
www.dice.com/jobs search-results page turned out to be server-rendered HTML
with real, stable job data (data-testid attributes, not a JS-only shell like
the industry_boards.py js_rendered candidates) - fetch_dice_jobs() below
scrapes it directly, no MCP/connector dependency at all. normalize_dice_job()
above stays as-is for the still-active MCP path (Zahir re-enabled the old
panga-daily-job-search scheduled task as a stopgap covering ZipRecruiter/
Dice/Indeed together) - not retired here, that's a call for whoever owns
deciding when the stopgap itself comes off, not something to do unilaterally
mid-investigation.
"""

import re

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}


def normalize_ziprecruiter_job(raw: dict) -> dict:
    salary = raw.get("salary") or {}
    return {
        "source": "ZipRecruiter",
        "job_id": raw.get("job_redirect_url"),
        "title": raw.get("title"),
        "organization": raw.get("company"),
        "department": None,
        "location": raw.get("location"),
        "pay_min": salary.get("min_annual"),
        "pay_max": salary.get("max_annual"),
        "posting_url": raw.get("job_redirect_url"),
        "apply_url": raw.get("job_redirect_url"),
    }


def normalize_dice_job(raw: dict) -> dict:
    return {
        "source": "Dice",
        "job_id": raw.get("guid"),
        "title": raw.get("title"),
        "organization": raw.get("companyName"),
        "department": None,
        "location": (raw.get("jobLocation") or {}).get("displayName"),
        "pay_min": None,
        "pay_max": None,
        "salary_text": raw.get("salary"),
        "posting_url": raw.get("detailsPageUrl"),
        "apply_url": raw.get("detailsPageUrl"),
    }


def normalize_indeed_jobs(raw_text: str) -> list[dict]:
    """Unlike the other two, Indeed's search_jobs tool returns one formatted
    markdown string for the whole result set, not structured JSON - this
    parses it. The "Job Id" field (e.g. "JOBSEARCH_1") is just a per-response
    sequence number, not stable across searches, so the short code embedded
    in the View Job URL is used as job_id instead - the only stable
    identifier available for dedup."""
    blocks = re.split(r"\*\*Job Title:\*\*", raw_text)[1:]

    def field(block: str, label: str) -> str | None:
        m = re.search(rf"\*\*{label}:\*\*\s*(.+)", block)
        value = m.group(1).strip() if m else None
        return None if value in (None, "N/A") else value

    jobs = []
    for block in blocks:
        block = "**Job Title:**" + block
        url = field(block, "View Job URL")
        compensation = field(block, "Compensation") or ""
        # \.\d+ optional group keeps cents attached to the number they belong
        # to (e.g. "87,509.62" is one number, not "87509" + a stray "62").
        nums = [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", compensation)]
        pay_min = nums[0] if nums else None
        pay_max = nums[1] if len(nums) > 1 else pay_min

        jobs.append({
            "source": "Indeed",
            "job_id": url,
            "title": field(block, "Job Title"),
            "organization": field(block, "Company"),
            "department": None,
            "location": field(block, "Location"),
            "pay_min": pay_min,
            "pay_max": pay_max,
            "posting_url": url,
            "apply_url": url,
        })
    return jobs


def fetch_dice_jobs(keyword: str, limit: int = 25) -> list[dict]:
    """Direct scrape of www.dice.com/jobs?q=<keyword> - confirmed 2026-08-06
    real server-side keyword filtering (not client-side/JS-only), e.g. a
    "Chief Information Officer" search returns actual CIO postings, not a
    generic unfiltered list. Selectors are keyed off data-testid attributes
    (job-search-job-detail-link, job-card-company-name) rather than utility
    CSS classes, which are the ones most likely to survive a Dice frontend
    redesign - same "prefer stable attributes over layout classes" instinct
    as the rest of this codebase's scrapers, just made explicit here since
    Dice's markup is unusually class-heavy (Tailwind-style utility classes).

    No dedicated location field extraction beyond what's server-filtered by
    keyword - Dice's own location/radius search params weren't part of this
    investigation's scope; add them if Zahir wants location-narrowed Dice
    results specifically, same as Rigzone's `sl` param."""
    response = requests.get(
        "https://www.dice.com/jobs", params={"q": keyword, "countryCode": "US"}, headers=HEADERS, timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for card in soup.select('[data-testid="job-card"]')[:limit]:
        title_link = card.select_one('a[data-testid="job-search-job-detail-link"]')
        if not title_link:
            continue
        company_el = card.select_one('p[data-testid="job-card-company-name"]')
        location = None
        if company_el:
            # Companies without a logo have the name <p> as a direct sibling
            # of the location <p>; companies with a logo wrap the name in an
            # <a> instead, one level up from that same sibling relationship
            # (confirmed live 2026-08-06 - a "Conexus" listing hit this and
            # crashed the first version of this parser, which assumed every
            # card always has the <a> wrapper). Walk up to whichever ancestor
            # actually sits next to the location <p>.
            company_container = company_el.parent if company_el.parent.name == "a" else company_el
            loc_p = company_container.find_next_sibling("p")
            if loc_p and loc_p.contents:
                first = loc_p.contents[0]
                location = str(first).strip() or None
        salary_el = card.select_one('[aria-labelledby="salary-label"] p')
        pay_min = pay_max = None
        if salary_el:
            # \.\d+ optional group keeps cents attached to the number they
            # belong to (e.g. "153,068.00" is one number, not "153068" + a
            # stray "00") - same fix normalize_indeed_jobs() already needed
            # for the identical shape of bug.
            nums = re.findall(r"\d[\d,]*(?:\.\d+)?", salary_el.get_text())
            if nums:
                pay_min = nums[0].split(".")[0].replace(",", "")
                pay_max = nums[1].split(".")[0].replace(",", "") if len(nums) > 1 else pay_min

        guid = card.get("data-job-guid")
        posting_url = f"https://www.dice.com/job-detail/{guid}" if guid else None
        jobs.append({
            "source": "Dice",
            "job_id": guid,
            "title": title_link.get_text(strip=True),
            "organization": company_el.get_text(strip=True) if company_el else None,
            "location": location,
            "pay_min": pay_min,
            "pay_max": pay_max,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs
