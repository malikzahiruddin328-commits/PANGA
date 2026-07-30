"""Mechanical parsing for LinkedIn's Connections CSV export (PRD §16b
contact-sourcing addition, 2026-07-30) - no reasoning here, same split as
ingest.py's PDF extraction. Zahir generates this himself via LinkedIn's
own Settings > Data Privacy > "Get a copy of your data" > Connections,
same manual-export-only constraint as the profile PDF and job postings -
no scraping, no login automation.

NOT YET TESTED AGAINST A REAL EXPORT - Zahir hasn't provided one. Built
against LinkedIn's documented/well-known Connections.csv shape: a few
"Notes:" explanatory lines, a blank line, then the real header row
(First Name,Last Name,URL,Email Address,Company,Position,Connected On).
The parser finds the header row by content rather than assuming a fixed
line number, so it should tolerate the preamble varying slightly, but
this is a real, disclosed gap versus signals 1-3 (which were validated
against live real data) - worth a quick check against Zahir's actual file
the first time this runs for real.
"""
import csv
import io
import re

RECRUITER_KEYWORDS = (
    "recruiter", "recruiting", "talent acquisition", "executive search",
    "headhunter", "staffing", "talent partner",
)


def parse_connections_csv(file) -> list[dict]:
    """file: a path (str/Path), file-like object, or raw bytes/str. Returns
    a list of {first_name, last_name, company, position, connected_on}."""
    if hasattr(file, "read"):
        raw = file.read()
    elif isinstance(file, (bytes, str)):
        raw = file
    else:
        with open(file, "rb") as f:
            raw = f.read()
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw

    lines = text.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if "First Name" in line and "Company" in line), None)
    if header_idx is None:
        return []

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    connections = []
    for row in reader:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        if not first and not last:
            continue
        connections.append({
            "first_name": first,
            "last_name": last,
            "company": (row.get("Company") or "").strip(),
            "position": (row.get("Position") or "").strip(),
            "connected_on": (row.get("Connected On") or "").strip(),
        })
    return connections


def looks_like_recruiter(position: str) -> bool:
    if not position:
        return False
    lower = position.lower()
    return any(kw in lower for kw in RECRUITER_KEYWORDS)


def _normalize_company(name: str) -> str:
    """Lowercase, drop commas/periods, collapse whitespace - enough to
    match "Napo Pharmaceuticals, Inc." (openFDA-style) against "Napo
    Pharmaceuticals Inc" (how a LinkedIn connection's company field might
    actually read) without a full corporate-suffix normalization library.
    Found via a real test failure on exactly this pair, not hypothetical."""
    name = re.sub(r"[.,]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def cross_reference_target_accounts(connections: list[dict], target_account_names: list[str]) -> list[dict]:
    """Returns connections whose company matches a target account name
    (case-insensitive substring after punctuation normalization, either
    direction - "Acme" should match a connection's "Acme Corp" and vice
    versa), each tagged with which target account it matched."""
    matches = []
    for conn in connections:
        company = _normalize_company(conn.get("company") or "")
        if not company:
            continue
        for target_name in target_account_names:
            target_norm = _normalize_company(target_name)
            if target_norm in company or company in target_norm:
                matches.append({**conn, "matched_target_account": target_name})
                break
    return matches
