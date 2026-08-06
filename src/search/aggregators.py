"""Slice 4 of the "industry & location agnostic search" backlog item
(2026-08-06): aggregator API client, the one genuinely new source in this
build (everything else so far was ATS-platform detection). Adzuna covers
many countries (see ADZUNA_COUNTRIES below) with one client, unlike the
per-company ATS integrations - this is what lets a user in any of those
countries get real search coverage without Zahir/anyone hand-picking
companies for them first.

Docs: https://developer.adzuna.com/overview. Free registration gives an
app_id/app_key pair - same "reads credentials from .env, raises a
NotConfigured exception if missing" pattern as usajobs.py, so a user who
hasn't set this up just doesn't get this source, same as USAJOBS/Gmail/
drafting today - not a hard failure.

Reed (UK-only, same shape) was considered as a second aggregator per the
original plan but deliberately left out of this pass - same reasoning
Greenhouse+Lever got built together but iCIMS didn't: Adzuna alone proves
the pattern and already covers the UK, so a second aggregator with its own
credential-setup burden isn't worth it until there's a real reason to add
one (e.g. Adzuna coverage gaps Zahir actually hits).

CALL BUDGET (not just a rate-limit sleep): Adzuna's free tier caps daily
calls (default assumed 250/day, ADZUNA_DAILY_CALL_LIMIT overrides this for
a higher-tier account) - unlike a scrape delay, sleeping between requests
doesn't stop a user with e.g. 8 target roles x 3 countries from blowing
through that in one run. _CallBudget persists today's count to disk
(file-locked, same read-modify-write-race concern as job_sources.yaml -
CLAUDE.md's shared-state check) and refuses further calls once the day's
count is reached, resetting itself the next calendar day.
"""

import os
from datetime import date
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_URL_TEMPLATE = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
BUDGET_PATH = PROJECT_ROOT / "data" / "adzuna_call_budget.yaml"
DEFAULT_DAILY_CALL_LIMIT = 250

# Adzuna's own supported country codes (developer.adzuna.com/overview) -
# used to validate what a user types into the Settings field rather than
# silently no-op-ing on a typo.
ADZUNA_COUNTRIES = {
    "at", "au", "br", "ca", "de", "es", "fr", "gb", "in", "it",
    "mx", "nl", "nz", "pl", "sg", "us", "za",
}


class AdzunaNotConfigured(Exception):
    pass


class AdzunaBudgetExceeded(Exception):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))


def _credentials() -> tuple[str, str]:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise AdzunaNotConfigured(
            "ADZUNA_APP_ID and ADZUNA_APP_KEY must be set in .env - free "
            "registration at https://developer.adzuna.com/."
        )
    return app_id, app_key


def _daily_limit() -> int:
    raw = os.environ.get("ADZUNA_DAILY_CALL_LIMIT")
    return int(raw) if raw else DEFAULT_DAILY_CALL_LIMIT


def _today() -> str:
    return date.today().isoformat()


def _load_budget_state() -> dict:
    if not BUDGET_PATH.exists():
        return {"date": _today(), "calls": 0}
    with open(BUDGET_PATH, encoding="utf-8") as f:
        state = yaml.safe_load(f) or {}
    if state.get("date") != _today():
        return {"date": _today(), "calls": 0}
    return state


def _save_budget_state(state: dict) -> None:
    BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BUDGET_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(state, f)


def _reserve_call() -> None:
    """Raises AdzunaBudgetExceeded instead of making the request if today's
    call count is already at the limit. Locked read-modify-write so two
    processes calling this around the same moment can't both read "1 call
    left" and both proceed - see file_lock.py's own docstring for why that
    matters now that scheduled scripts run unattended alongside the app."""
    with locked("adzuna_budget"):
        state = _load_budget_state()
        if state["calls"] >= _daily_limit():
            raise AdzunaBudgetExceeded(
                f"Adzuna daily call budget ({_daily_limit()}) already used today - "
                "will reset tomorrow. Raise ADZUNA_DAILY_CALL_LIMIT in .env if your "
                "Adzuna account has a higher tier."
            )
        state["calls"] += 1
        _save_budget_state(state)


def remaining_calls_today() -> int:
    return max(0, _daily_limit() - _load_budget_state()["calls"])


def fetch_adzuna_jobs(keyword: str, country: str, limit: int = 25) -> list[dict]:
    """country is a 2-letter Adzuna country code (see ADZUNA_COUNTRIES).
    Raises AdzunaNotConfigured if no credentials, AdzunaBudgetExceeded if
    today's call budget is used up - both are caller-skippable, same as
    usajobs.py's USAJobsNotConfigured."""
    country = country.strip().lower()
    if country not in ADZUNA_COUNTRIES:
        raise ValueError(f"{country!r} is not a supported Adzuna country code: {sorted(ADZUNA_COUNTRIES)}")
    app_id, app_key = _credentials()
    _reserve_call()

    url = API_URL_TEMPLATE.format(country=country)
    params = {
        "app_id": app_id, "app_key": app_key, "results_per_page": limit,
        "what": keyword, "content-type": "application/json",
    }
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for posting in data.get("results", []):
        company = (posting.get("company") or {}).get("display_name")
        location = (posting.get("location") or {}).get("display_name")
        posting_url = posting.get("redirect_url")
        jobs.append({
            "source": "Adzuna",
            "job_id": str(posting.get("id")),
            "title": posting.get("title"),
            "organization": company,
            "location": location,
            "pay_min": posting.get("salary_min"),
            "pay_max": posting.get("salary_max"),
            "posting_url": posting_url,
            "apply_url": posting_url,
        })
    return jobs
