"""Normalizes SEC EDGAR S-1 filings into target_account signals (PRD §16a,
signal type 4: funding/IPO activity). Direct HTTP API (EDGAR's full-text
search), no MCP connector needed - same standalone-capable shape as
regulatory_filings.py. This was the one signal explicitly flagged in the
design as "needs source research before buildable" - EDGAR's full-text
search turned out to be a clean, free, public fit once tested live.

Real finding (2026-07-30): unlike signals 1-2, this source doesn't need a
separate mega-pharma exclusion - S-1 is specifically the form for a
company's OWN initial public offering (or an amendment to one in
progress); an already-public mega-pharma company has no reason to file
one for itself. Restricting to SIC codes 2834 (Pharmaceutical
Preparations)/2836 (Biological Products)/8731 (Commercial Physical &
Biological Research) plus a "phase 3" mention in the filing text (not SIC
code alone) keeps results to companies actually discussing a late-stage
clinical program in their registration statement, not just anything
nominally coded in a pharma SIC bucket.

Coverage note: EDGAR's search API returns up to 100 hits per request (a
real 2026-07-30 query for S-1/S-1-A filings mentioning "phase 3" in the
first 7 months of 2026 matched 193 total) - one page is fetched here, same
"real, disclosed, not-total" coverage caveat as regulatory_filings.py's
recency window.
"""
import re

import requests

API_URL = "https://efts.sec.gov/LATEST/search-index"
PHARMA_SIC_CODES = {"2834", "2836", "8731"}
USER_AGENT = "Panga Job Search Tool (personal use) - zahiruddin328@gmail.com"

_NAME_SUFFIX_RE = re.compile(r"\s*(?:\([A-Z, ]+\)\s*)?\(CIK \d+\)\s*$")


def _clean_company_name(display_name: str) -> str:
    """EDGAR's display_names look like "Attovia Therapeutics, Inc.  (ATTO)
    (CIK 0002058707)" - strips the ticker/CIK suffix. The ticker part is
    optional - pre-IPO companies filing their first S-1 often have no
    ticker yet, just "Company Name  (CIK 0001234567)"."""
    return _NAME_SUFFIX_RE.sub("", display_name).strip()


def fetch_recent_s1_filings(start_date: str, end_date: str) -> list[dict]:
    """One page (up to 100, EDGAR's max per request) of S-1/S-1-A filings
    mentioning "phase 3" in the given date range, restricted to pharma SIC
    codes client-side (the search API's `q` doesn't support SIC filtering
    directly)."""
    headers = {"User-Agent": USER_AGENT}
    params = {"q": '"phase 3"', "forms": "S-1", "startdt": start_date, "enddt": end_date}
    response = requests.get(API_URL, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    hits = response.json().get("hits", {}).get("hits", [])
    return [
        h["_source"] for h in hits
        if any(sic in PHARMA_SIC_CODES for sic in (h["_source"].get("sics") or []))
    ]


def normalize_funding_signals(filings: list[dict]) -> list[dict]:
    """One signal per company (by CIK, since the same company often has
    both an S-1 and one or more S-1/A amendments) - keeps the most recent
    filing's date/form in the detail text."""
    by_cik: dict[str, dict] = {}
    for f in filings:
        cik = (f.get("ciks") or [None])[0]
        if not cik:
            continue
        if cik not in by_cik or f.get("file_date", "") > by_cik[cik].get("file_date", ""):
            by_cik[cik] = f

    out = []
    for cik, f in by_cik.items():
        display_names = f.get("display_names") or [""]
        company = _clean_company_name(display_names[0])
        if not company:
            continue
        out.append({
            "company_name": company,
            "signal_type": "funding_event",
            "source": "sec_edgar",
            "detail": f"{f.get('form')} filed {f.get('file_date')} - mentions Phase 3 clinical activity",
            "ref": cik,
        })
    return out
