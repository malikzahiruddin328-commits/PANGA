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

BROAD-SCOPE UPDATE (2026-08-07): Zahir's ask to widen board coverage across
the whole US job market, not just pharma - general-purpose, multi-employer
national boards belong here alongside Dice, not in industry_boards.py
(which is for single-vertical/single-employer niche sources). Recon'd ~10
real candidates live before building anything (Built In and SimplyHired
cleared all 3 merit criteria - real recent activity, reputable/established,
US-focused; Monster/CareerBuilder/WallStreetOasis/eFinancialCareers/
ClearedJobs/The Ladders all WAF-blocked same as ZipRecruiter/Indeed;
HealthITJobs.com turned out to be a parked/expired domain; Idealist is
JS-rendered). fetch_built_in_jobs()/fetch_simplyhired_jobs() below both
search PER keyword (same as search_dice()) rather than fetching
everything - deliberate: an unfiltered fetch of a broad multi-employer
board could pull in thousands of totally unrelated roles (a nurse, a
supply-chain specialist - Zahir's own example of what NOT to surface),
unlike Greenhouse/Lever's fetch-everything pattern, which stays safe only
because it's bounded to one company's total open roles. Searching by
target-role keyword keeps the volume/relevance bar the same as every
other keyword-searched source in this codebase, rather than leaning on
fit_score alone to filter out noise it was never designed to absorb at
this scale.
"""

import hashlib
import json
import re

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}


def _normalize_for_hash(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


# Location-specific normalization on top of _normalize_for_hash() - real bug
# found 2026-08-11 (Mirror's audit, docs/review-sweep-reports/2026-08-07-
# mirror-audit.md F2): _stable_job_id() was supposed to dedupe a posting
# found via BOTH Dice code paths (fetch_dice_jobs()'s direct scrape vs.
# normalize_dice_job()'s MCP path), but the two format location differently
# for the exact same real posting - "Hybrid in Ann Arbor, Michigan" (scrape)
# vs. "Ann Arbor, Michigan, USA" (MCP), "No location provided" (scrape) vs.
# None (MCP) - so the hash never matched. Confirmed live against production
# data: 0 of 141 (title, organization) pairs present on both paths shared a
# job_id. Stripping the work-mode prefix ("Hybrid/Remote/On-site in/or ")
# and country suffix (", USA") before hashing - not fixing the two scrapers
# to agree on a display format (fragile, either could change independently;
# this fixes the comparison instead of the source data, more durable).
_LOCATION_WORKMODE_PREFIX_RE = re.compile(r"^(hybrid|remote|on-site)\s+(in|or)\s+")
_LOCATION_COUNTRY_SUFFIX_RE = re.compile(r",?\s*usa$")


def _normalize_location_for_hash(location: str | None) -> str:
    text = _normalize_for_hash(location)
    text = _LOCATION_WORKMODE_PREFIX_RE.sub("", text)
    text = _LOCATION_COUNTRY_SUFFIX_RE.sub("", text).strip()
    if text == "no location provided":
        return ""
    return text


def _stable_job_id(source: str, title: str | None, organization: str | None, location: str | None) -> str:
    """job_store.save_jobs() dedupes on (source, job_id), which only works
    if job_id is actually stable across repeated searches for the same real
    posting - confirmed live in production data 2026-08-06 that it wasn't,
    for every source in this module: Indeed reissues its redirect URL,
    ZipRecruiter's match_token is a per-request signed token, and even
    Dice's MCP-path "guid" (which looks like a stable database ID) turned
    out to rotate too - the same real "VP, Data Product Manager" at Ledgent
    Technology, San Diego showed up under two different guids in the real
    store. (Dice's own DIRECT-scrape guid, from fetch_dice_jobs() below,
    empirically IS stable across repeated fetches - verified live, same
    guid both times for every posting checked - but both paths write
    source="Dice", so unifying the id scheme here also dedupes across the
    two Dice code paths, not just within either one.)

    Content-based instead: normalized (lowercased, whitespace-collapsed)
    title+organization+location, hashed - same "derive stability from
    stable inputs" idea as tailoring/dossier.py's _job_hash(), just applied
    to fields that are actually stable here rather than to an already-
    stable job_id. Real tradeoff, accepted: two genuinely different
    postings with identical title/org/location would now collide - far
    rarer than the every-run duplication this replaces. Location goes
    through the extra _normalize_location_for_hash() pass above (title/
    organization don't need it - no equivalent formatting-variance bug
    found there) - see that function's docstring for why."""
    key = "|".join([_normalize_for_hash(title), _normalize_for_hash(organization), _normalize_location_for_hash(location)])
    return hashlib.sha1(f"{source}:{key}".encode()).hexdigest()[:16]


def normalize_ziprecruiter_job(raw: dict) -> dict:
    salary = raw.get("salary") or {}
    title, organization, location = raw.get("title"), raw.get("company"), raw.get("location")
    return {
        "source": "ZipRecruiter",
        "job_id": _stable_job_id("ZipRecruiter", title, organization, location),
        "title": title,
        "organization": organization,
        "department": None,
        "location": location,
        "pay_min": salary.get("min_annual"),
        "pay_max": salary.get("max_annual"),
        "posting_url": raw.get("job_redirect_url"),
        "apply_url": raw.get("job_redirect_url"),
    }


def normalize_dice_job(raw: dict) -> dict:
    # "summary" is a real (if truncated, ~500 char) excerpt of the actual
    # posting text, already present in Dice's own search response - live-
    # confirmed 2026-08-06 while auditing why ATS keyword extraction was
    # coming back empty for non-USAJOBS jobs (see tailoring/ats_score.py).
    # Captured as "description" so drafting.py's _extract_ats_keywords()
    # picks it up the same way it already does for USAJOBS/LinkedIn jobs.
    title, organization = raw.get("title"), raw.get("companyName")
    location = (raw.get("jobLocation") or {}).get("displayName")
    return {
        "source": "Dice",
        "job_id": _stable_job_id("Dice", title, organization, location),
        "title": title,
        "organization": organization,
        "department": None,
        "location": location,
        "pay_min": None,
        "pay_max": None,
        "salary_text": raw.get("salary"),
        "description": raw.get("summary"),
        "posting_url": raw.get("detailsPageUrl"),
        "apply_url": raw.get("detailsPageUrl"),
    }


def normalize_indeed_jobs(raw_text: str) -> list[dict]:
    """Unlike the other two, Indeed's search_jobs tool returns one formatted
    markdown string for the whole result set, not structured JSON - this
    parses it. The "Job Id" field (e.g. "JOBSEARCH_1") is just a per-response
    sequence number, not stable across searches. The View Job URL was used
    as job_id for a while, on the assumption it was the only stable
    identifier available - turned out not to be true either (confirmed live
    2026-08-06, Indeed reissues the redirect URL on repeat searches for the
    same posting); see _stable_job_id()'s docstring for the current fix."""
    blocks = re.split(r"\*\*Job Title:\*\*", raw_text)[1:]

    def field(block: str, label: str) -> str | None:
        m = re.search(rf"\*\*{label}:\*\*\s*(.+)", block)
        value = m.group(1).strip() if m else None
        return None if value in (None, "N/A") else value

    jobs = []
    for block in blocks:
        block = "**Job Title:**" + block
        url = field(block, "View Job URL")
        title, organization, location = field(block, "Job Title"), field(block, "Company"), field(block, "Location")
        compensation = field(block, "Compensation") or ""
        # \.\d+ optional group keeps cents attached to the number they belong
        # to (e.g. "87,509.62" is one number, not "87509" + a stray "62").
        nums = [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", compensation)]
        pay_min = nums[0] if nums else None
        pay_max = nums[1] if len(nums) > 1 else pay_min

        jobs.append({
            "source": "Indeed",
            "job_id": _stable_job_id("Indeed", title, organization, location),
            "title": title,
            "organization": organization,
            "department": None,
            "location": location,
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
        title = title_link.get_text(strip=True)
        organization = company_el.get_text(strip=True) if company_el else None
        jobs.append({
            "source": "Dice",
            # data-job-guid itself is empirically stable across repeat
            # fetches of this direct-scrape path (verified live) - but
            # normalize_dice_job()'s MCP path shares this same source="Dice"
            # label and its guid is NOT stable (see _stable_job_id()'s
            # docstring), so using the same content-based id here too is
            # what actually dedupes a posting found via both paths, not
            # just within this one.
            "job_id": _stable_job_id("Dice", title, organization, location),
            "title": title,
            "organization": organization,
            "location": location,
            "pay_min": pay_min,
            "pay_max": pay_max,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def fetch_dice_job_description(posting_url: str) -> str | None:
    """Real JD text for one direct-scrape Dice posting - real gap found
    2026-08-11 (Mirror's audit F1): fetch_dice_jobs() above never set
    `description` at all, unlike its sibling normalize_dice_job() (the MCP
    path, which gets a ~500-char excerpt for free in its own search
    response) - the direct scrape is the one that actually runs on the
    daily unattended job search (run_search.py STEP 2c), so resumes were
    being drafted/scored against zero real JD content for every Dice
    posting found this way.

    Takes the job's own `posting_url` (https://www.dice.com/job-detail/
    <guid>), NOT `job_id` - job_id is now _stable_job_id()'s content hash,
    not the raw Dice guid the real URL needs (a real bug caught live while
    building this: calling this with job_id 404s, since that string was
    never a valid Dice URL segment to begin with).

    The job-detail page's search-result cards have no inline description
    (confirmed live) - but the page itself embeds a standard schema.org
    JobPosting JSON-LD block (`<script data-testid="jobDetailStructuredData"
    type="application/ld+json">`) with a full `description` field (HTML),
    not just an excerpt - a more stable extraction target than guessing at
    Dice's own CSS classes, and the same real content normalize_dice_job()'s
    truncated `summary` field is itself an excerpt of.

    Raises on failure rather than swallowing it - unlike company_sites.py's
    equivalent Workday/SmartRecruiters fetchers (flagged separately, Mirror
    F5, as a real gap: silent failures with no signal anywhere). The
    caller (scripts/run_search.py's search_dice()) logs the exception,
    same as every other per-item failure in that script."""
    response = requests.get(posting_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    script = soup.select_one('[data-testid="jobDetailStructuredData"]')
    if not script:
        return None
    data = json.loads(script.get_text())
    html_description = data.get("description")
    if not html_description:
        return None
    return BeautifulSoup(html_description, "html.parser").get_text("\n", strip=True) or None


def fetch_built_in_jobs(keyword: str, limit: int = 25) -> list[dict]:
    """Direct scrape of builtin.com/jobs?search=<keyword> - confirmed real
    server-side keyword filtering, live-verified against a "chief
    information officer" search (25 real, recent postings - JPMorganChase,
    Navan, etc., "Reposted Yesterday"/"N Days Ago"). Inherently US-only (a
    US-focused careers platform - every location seen in recon was a US
    city/state or "N Locations"), no extra country param needed.

    Location and salary fields aren't behind a stable data-id like title/
    company are - Built In's markup only tags a fixed handful of fields
    with data-id (company-title, job-card-title), everything else is a
    bare list of `.font-barlow.text-gray-04` spans whose first entry is
    always work-mode (Hybrid/Remote/In-Office) and second is always
    location, but salary/seniority-level entries are only present when
    Built In actually has that data - a card missing salary just has one
    fewer span, not an empty placeholder (confirmed live across 25 real
    cards). Salary is identified by pattern-matching for a "NNNK" shape
    rather than trusting a fixed index, since its position shifts.

    job_id is content-based (_stable_job_id()), NOT the raw href/data-alias
    - a real gap caught by Mirror's audit 2026-08-08 (this function shipped
    in the same commit as fetch_simplyhired_jobs(), which got this fix and
    its disclosure; this one didn't, no note explaining why - it wasn't a
    deliberate exception, just missed). Confirmed live in production data:
    one real duplicate already existed ("Audit Project Manager - CIO" at US
    Bank under two different /job/.../<id> hrefs, 9932864 vs 10508582) -
    Built In's own "Reposted" labeling suggests a repost can get a genuinely
    new numeric ID, same failure mode as Indeed/ZipRecruiter/Dice's MCP
    path. posting_url/apply_url still use the real href, same "id is
    stable, link is whatever's live today" split used everywhere else."""
    response = requests.get(
        "https://builtin.com/jobs", params={"search": keyword}, headers=HEADERS, timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for card in soup.select('[data-id="job-card"]')[:limit]:
        title_link = card.select_one('a[data-id="job-card-title"]')
        if not title_link:
            continue
        company_el = card.select_one('a[data-id="company-title"]')
        attr_section = card.select_one(".bounded-attribute-section")
        spans = attr_section.select("span.font-barlow.text-gray-04") if attr_section else []
        location = spans[1].get_text(strip=True) if len(spans) > 1 else None

        pay_min = pay_max = None
        for span in spans:
            text = span.get_text(strip=True)
            nums = re.findall(r"(\d+)K", text)
            if nums:
                pay_min = str(int(nums[0]) * 1000)
                pay_max = str(int(nums[1]) * 1000) if len(nums) > 1 else pay_min
                break

        href = title_link.get("data-alias") or title_link.get("href")
        posting_url = f"https://builtin.com{href}" if href and href.startswith("/") else href
        title = title_link.get_text(strip=True)
        organization = company_el.get_text(strip=True) if company_el else None
        jobs.append({
            "source": "Built In",
            "job_id": _stable_job_id("Built In", title, organization, location),
            "title": title,
            "organization": organization,
            "location": location,
            "pay_min": pay_min,
            "pay_max": pay_max,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs


def fetch_simplyhired_jobs(keyword: str, limit: int = 25) -> list[dict]:
    """Direct scrape of simplyhired.com/search?q=<keyword>&l=United+States -
    confirmed real server-side keyword + location filtering, live-verified
    against a "chief information officer" search restricted to "United
    States" (real, relevant results - IT Director, CIO, CISO postings with
    real salary/location/date-posted data, "2d"/"8d" recency stamps).

    job_id is content-based (_stable_job_id()), NOT the /job/<token> href -
    live-tested fetching the same search twice (same lesson as Indeed/
    ZipRecruiter/Dice's MCP path): 7 of 8 matching postings kept the same
    href token, but one didn't, so it's not safe to trust as a dedup key.
    posting_url/apply_url still use the real (if occasionally reissued)
    href, same "id is stable, link is whatever's live today" split used
    everywhere else in this module."""
    response = requests.get(
        "https://www.simplyhired.com/search", params={"q": keyword, "l": "United States"}, headers=HEADERS, timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = []
    for card in soup.select('[data-testid="searchSerpJob"]')[:limit]:
        title_el = card.select_one('[data-testid="searchSerpJobTitle"]')
        title_link = title_el.select_one("a") if title_el else None
        if not title_link:
            continue
        company_el = card.select_one('[data-testid="companyName"]')
        location_el = card.select_one('[data-testid="searchSerpJobLocation"]')
        salary_el = card.select_one('[data-testid="salaryChip-0"]')

        pay_min = pay_max = None
        if salary_el:
            nums = re.findall(r"[\d,]+", salary_el.get_text())
            if nums:
                pay_min = nums[0].replace(",", "")
                pay_max = nums[1].replace(",", "") if len(nums) > 1 else pay_min

        href = title_link.get("href")
        posting_url = f"https://www.simplyhired.com{href}" if href and href.startswith("/") else href
        title = title_link.get_text(strip=True)
        organization = company_el.get_text(strip=True) if company_el else None
        location = location_el.get_text(strip=True) if location_el else None
        jobs.append({
            "source": "SimplyHired",
            "job_id": _stable_job_id("SimplyHired", title, organization, location),
            "title": title,
            "organization": organization,
            "location": location,
            "pay_min": pay_min,
            "pay_max": pay_max,
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs
