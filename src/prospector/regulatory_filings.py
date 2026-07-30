"""Normalizes openFDA drug-application data into target_account signals
(PRD §16a, signal type 2: regulatory filing activity). Direct HTTP API,
same as src/search/company_sites.py's openFDA usage (§4c) - no MCP
connector needed, unlike clinical_trials.py, so this can run as a
standalone script/scheduled task later, not just live in a Claude session.

Lives in src/prospector/ rather than extending company_sites.py as PRD
§16a originally sketched - that file's purpose is company/job sourcing for
the reactive Results pipeline, a different concern from Prospector's
proactive target-account signals, and the prospector package didn't exist
yet when that PRD line was written (2026-07-29).

Real finding (2026-07-30): a broad ORIG+AP (original application,
approved) search matches BOTH branded (NDA/BLA) and generic (ANDA)
approvals - ANDA generic-drug approvals aren't a "approaching commercial
launch" signal (generic manufacturers are already commodity-scale
businesses), so the query restricts to application_number prefixes
NDA*/BLA* only. Also: openFDA's date RANGE filter on the nested
submissions array doesn't reliably scope to the SAME submission that
matched submission_type/submission_status (a known Elasticsearch
nested-field limitation) - a range query returned 1990s/1950s approvals
despite the range being 2023-2026. Recency filtering is done entirely
client-side instead, on a large fetched page (limit=1000, the max),
which only sees that one page's worth of the ~5,800 total matches, not a
true global "most recent N" - a real coverage gap, not a hidden one.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from prospector.company_filters import looks_like_target_company

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_URL = "https://api.fda.gov/drug/drugsfda.json"


def fetch_recent_nda_bla_approvals(limit: int = 1000) -> list[dict]:
    """One page (up to openFDA's max limit of 1000) of original NDA/BLA
    applications with an approved submission - unsorted; caller filters/
    sorts by recency client-side (see module docstring for why)."""
    params = {
        "search": (
            'submissions.submission_status:"AP" AND '
            'submissions.submission_type:"ORIG" AND '
            "(application_number:NDA* OR application_number:BLA*)"
        ),
        "limit": limit,
    }
    api_key = os.environ.get("FDA_API_KEY")
    if api_key:
        params["api_key"] = api_key
    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("results", [])


def normalize_regulatory_signals(applications: list[dict], years_back: int = 3) -> list[dict]:
    """Filters to sponsors whose most recent ORIG/AP submission falls
    within years_back, excludes non-companies/mega-pharma/known-acquired
    via company_filters, and returns signal-ready dicts (company_name +
    the kwargs target_accounts.add_signal expects)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365 * years_back)).strftime("%Y%m%d")

    out = []
    for app in applications:
        sponsor = app.get("sponsor_name")
        if not looks_like_target_company(sponsor):
            continue
        orig_dates = [
            s["submission_status_date"] for s in app.get("submissions", [])
            if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP"
            and s.get("submission_status_date")
        ]
        if not orig_dates:
            continue
        latest = max(orig_dates)
        if latest < cutoff:
            continue

        products = app.get("products") or [{}]
        brand = products[0].get("brand_name") or "product not listed"
        detail = (
            f"{app.get('application_number')}: original approval for {brand} "
            f"- approved {latest[:4]}-{latest[4:6]}-{latest[6:]}"
        )
        out.append({
            "company_name": sponsor,
            "signal_type": "regulatory_filing",
            "source": "openfda",
            "detail": detail,
            "ref": app.get("application_number"),
        })
    return out
