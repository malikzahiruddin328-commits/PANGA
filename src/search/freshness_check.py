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

TWO-OBSERVATION CONFIRMATION + PERIODIC RECHECK (added 2026-08-11, Mirror's
audit findings #6/#7): a single False from check_posting_open() is exactly
the kind of signal a transient blip (server hiccup, a maintenance window, a
site returning 404 instead of 429 while rate-limiting) can produce
indistinguishably from a real closure, and there's no in-request retry - so
a lone False only STAGES a pending-closed flag (data/jobs/
freshness_check_state.json, its own small lock-guarded store, kept separate
from applications.json rather than bolting more fields onto
upsert_application - see that function's docstring, it has no "leave status
untouched" mode, every call sets status). It takes a second, separate
day's run also reporting False before status actually becomes "closed by
employer". A job already marked closed is no longer skipped forever either
- it's re-verified every RECHECK_CLOSED_AFTER_DAYS (one extra request per
already-closed job per that window - negligible added volume even against
a large closed backlog, see CLAUDE.md's cost-blast-radius principle). If a
recheck finds it genuinely open again (e.g. a Workday requisition or
SmartRecruiters posting paused for a budget/headcount freeze and later
republished under the SAME id - a real ATS state, not a hypothetical),
status is restored to whatever it was before this automation ever touched
it (captured at the moment the pending flag was first staged), or "under
review" if there was none - there's no supported blank-status write path
here either, and "under review" (PRD: "the app has no way to know a job
was actually submitted") is the closest honest equivalent to "untouched".
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from search import company_sites, job_sources, job_store, usajobs
from security.crypto_store import read_json, write_json
from security.file_lock import locked
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

RECHECK_CLOSED_AFTER_DAYS = 14

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATE_PATH = PROJECT_ROOT / "data" / "jobs" / "freshness_check_state.json"
_STATE_LOCK_NAME = "freshness_check_state"

# Statuses that already reflect a real decision Zahir made (never
# overwritten by this automation). CLOSED_STATUS is deliberately NOT in
# here - see the two-observation/recheck design above; a closed job is
# handled by its own recheck-due gate in check_and_mark_closed_postings(),
# not blanket-skipped like a genuine decision would be.
_SKIP_STATUSES = {
    "not interested",
    "not-interested",
    "applied",
    "interview scheduled",
    "offer",
    "rejected",
}

# Per-site "posting closed" phrase, matched case-insensitively against the
# fetched page text. Starts empty for sites we haven't confirmed real
# wording on yet (the industry boards) - those rely on the 404-on-removal
# heuristic alone until a real closure is observed and the wording can be
# added here, same as LinkedIn/ZipRecruiter/Dice were confirmed.
#
# ZipRecruiter and Indeed postings both live-tested as always returning None
# here (confirmed 2026-08-05) - both domains 403 Python's `requests` library
# specifically on the exact stored posting_url (a WAF fingerprinting the
# HTTP/TLS stack, not headers; curl/browser reach the same URL fine), the
# same block already documented for IChemE in industry_boards.py. Their
# confirmed closed-phrases above are correct but currently unreachable by
# this fetcher - fail-safe means these two sources just never get marked
# closed automatically until that's solved, not a bug in the phrase match.
#
# Dice (added 2026-08-06, alongside boards.fetch_dice_jobs()) doesn't 404 a
# removed posting - the detail page still 200s but its <title> becomes "job
# not found | dice.com" (confirmed live against a real and a fake guid), and
# unlike ZipRecruiter/Indeed, plain `requests` reaches it fine (no WAF
# block) - so this phrase is actually reachable by this fetcher, not just
# documented like the two above.
_CLOSED_PHRASES = {
    "LinkedIn": ["no longer accepting applications"],
    "ZipRecruiter": ["this job post has expired"],
    "Dice": ["job not found"],
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


def _check_greenhouse(job: dict, company: dict) -> bool | None:
    try:
        return company_sites.check_greenhouse_posting_open(company["board_token"], job["job_id"])
    except Exception:  # noqa: BLE001
        return None


def _check_lever(job: dict, company: dict) -> bool | None:
    try:
        return company_sites.check_lever_posting_open(company["company_slug"], job["job_id"])
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
    for company in sources["greenhouse"]:
        lookup[company["company_name"]] = (_check_greenhouse, company)
    for company in sources["lever"]:
        lookup[company["company_name"]] = (_check_lever, company)
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


def _load_state() -> list[dict]:
    return read_json(_STATE_PATH, default=[])


def _find_state_entry(state: list[dict], source: str, job_id: str) -> dict | None:
    for entry in state:
        if entry.get("source") == source and entry.get("job_id") == job_id:
            return entry
    return None


def _stage_pending_closed(source: str, job_id: str, prior_status: str | None) -> None:
    """First observed-closed for this posting - record it without touching
    the real status yet (see module docstring)."""
    with locked(_STATE_LOCK_NAME):
        state = _load_state()
        if _find_state_entry(state, source, job_id) is None:
            state.append({
                "source": source,
                "job_id": job_id,
                "pending_since": datetime.now(timezone.utc).isoformat(),
                "prior_status": prior_status,
                "closed_confirmed_at": None,
            })
            write_json(_STATE_PATH, state)
        # else: a pending entry already exists (shouldn't normally happen -
        # this only runs once/day and a pending entry always resolves the
        # same run it's created - but leave the original prior_status/
        # pending_since alone rather than overwrite them if it does).


def _commit_closed(source: str, job_id: str) -> None:
    """Marks the state entry closed-as-of-now - used both the first time a
    posting is confirmed closed (second consecutive observation) and every
    later recheck that still finds it closed (refreshes the recheck clock
    without re-touching applications.json, since it's already the right
    status)."""
    with locked(_STATE_LOCK_NAME):
        state = _load_state()
        entry = _find_state_entry(state, source, job_id)
        if entry is None:
            entry = {"source": source, "job_id": job_id, "prior_status": None}
            state.append(entry)
        entry["pending_since"] = None
        entry["closed_confirmed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(_STATE_PATH, state)


def _clear_state(source: str, job_id: str) -> None:
    with locked(_STATE_LOCK_NAME):
        state = _load_state()
        filtered = [e for e in state if not (e.get("source") == source and e.get("job_id") == job_id)]
        if len(filtered) != len(state):
            write_json(_STATE_PATH, filtered)


def _due_for_recheck(closed_confirmed_at: str | None) -> bool:
    """No/unparseable timestamp (e.g. a job closed by the pre-2026-08-11
    version of this module, before recheck tracking existed) is treated as
    due - fail toward rechecking a job we have no record of, not toward
    leaving it permanently skipped, which is the exact bug being fixed."""
    if not closed_confirmed_at:
        return True
    try:
        closed_at = datetime.fromisoformat(closed_confirmed_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - closed_at >= timedelta(days=RECHECK_CLOSED_AFTER_DAYS)


def check_and_mark_closed_postings(min_fit_score: int = MIN_FIT_SCORE) -> tuple[int, int, int, int]:
    """Returns (checked, marked_closed, newly_pending, reopened). Iterates a
    fixed snapshot of the >=min_fit_score jobs taken once at the start - not
    O(n^2), and immune to the store growing mid-run since save_jobs() only
    appends."""
    candidates = [j for j in job_store.load_jobs() if (j.get("fit_score") or 0) >= min_fit_score]
    api_sources = build_api_source_lookup()
    state = _load_state()

    checked = 0
    marked = 0
    newly_pending = 0
    reopened = 0
    for job in candidates:
        source, job_id = job.get("source"), job.get("job_id")
        if not source or not job_id:
            continue
        current = applications.get_application(source, job_id)
        current_status = (current or {}).get("status")
        is_currently_closed = current_status == CLOSED_STATUS

        if current_status in _SKIP_STATUSES:
            continue  # a real decision Zahir made - never second-guess it

        state_entry = _find_state_entry(state, source, job_id)
        if is_currently_closed and not _due_for_recheck((state_entry or {}).get("closed_confirmed_at")):
            continue  # already closed, not due for its periodic re-verify yet

        checked += 1
        is_api_source = source == "USAJOBS" or source in api_sources
        try:
            is_open = check_posting_open(job, api_sources)
        except Exception:  # noqa: BLE001 - one posting's failure shouldn't stop the run
            is_open = None
        time.sleep(API_CHECK_DELAY_SECONDS if is_api_source else SCRAPE_CHECK_DELAY_SECONDS)

        if is_open is False:
            if is_currently_closed:
                _commit_closed(source, job_id)  # still closed on recheck - just refresh the clock
            elif state_entry is not None and state_entry.get("pending_since"):
                _commit_closed(source, job_id)  # second consecutive observation - commit for real
                applications.upsert_application(source, job_id, status=CLOSED_STATUS)
                marked += 1
            else:
                _stage_pending_closed(source, job_id, prior_status=current_status)
                newly_pending += 1
        elif is_open is True:
            if is_currently_closed:
                restore_status = (state_entry or {}).get("prior_status") or "under review"
                applications.upsert_application(source, job_id, status=restore_status)
                _clear_state(source, job_id)
                reopened += 1
            elif state_entry is not None:
                _clear_state(source, job_id)  # false alarm - status was never actually changed
        # is_open is None: ambiguous - leave everything exactly as-is (existing fail-safe)

    return checked, marked, newly_pending, reopened
