"""Sorts results list by role priority weight (PRD §9). For v0 this is a sort
order only - it does not affect how hard or how often the system searches
for each role.
"""

import re


def weight_for(job_title: str, target_roles: list[dict]) -> int:
    if not job_title:
        return 0
    title_lower = job_title.lower()
    best = 0
    for role in target_roles:
        if role["name"].lower() in title_lower:
            best = max(best, role["priority_weight"])
    return best


_ORG_SUFFIX_RE = re.compile(r"[,.]?\s*\b(inc|incorporated|corp|corporation|llc|ltd|limited|co|company|plc)\.?\s*$", re.IGNORECASE)


def _normalize_org(name: str) -> str:
    if not name:
        return ""
    return _ORG_SUFFIX_RE.sub("", name.strip().lower()).strip()


def _normalize_title(title: str) -> str:
    return (title or "").strip().lower()


def dedupe_across_sources(jobs: list[dict]) -> list[dict]:
    """Folds job-board postings (USAJOBS/ZipRecruiter/Dice/Indeed/industry
    boards) into a matching direct company-site posting (Eisai/AbbVie/IQVIA
    etc. via company_sites.py - identifiable structurally since those
    records always have source == organization) when both represent the same
    real opening. The company-site posting is authoritative and kept; a
    matched board posting is removed from the returned list and appended to
    the surviving job's "_cross_source_duplicates" list instead, so the UI
    can still surface every original posting link (same pattern as the
    existing same-channel merge in ui/app.py).

    Matched narrowly - exact organization match (after stripping common
    corporate suffixes like "Inc"/"LLC") and exact title match, both
    case-insensitive - since a false merge (two different real jobs shown as
    one) is worse than a missed merge (same job shown twice); a near-miss
    just falls back to showing both postings separately, same as before this
    function existed.
    """
    company_site_primaries = {}
    for job in jobs:
        if job.get("source") and job.get("source") == job.get("organization"):
            key = (_normalize_org(job.get("organization")), _normalize_title(job.get("title")))
            company_site_primaries.setdefault(key, job)

    if not company_site_primaries:
        return jobs

    result = []
    for job in jobs:
        if job.get("source") == job.get("organization"):
            result.append(job)
            continue
        key = (_normalize_org(job.get("organization")), _normalize_title(job.get("title")))
        primary = company_site_primaries.get(key)
        if primary is not None:
            primary.setdefault("_cross_source_duplicates", []).append(job)
        else:
            result.append(job)
    return result


def find_freshness_downgrade_targets(jobs: list[dict], removed_company_name: str) -> list[tuple[str, str]]:
    """Given the full job list and a company just removed from
    config/job_sources.yaml (see search/job_sources.py), returns the
    (source, job_id) pairs whose freshness-check precision should be
    explicitly downgraded - Zahir's explicit product call (2026-08-10):
    the removed company's own postings, AND any board postings
    dedupe_across_sources() above would fold into them as the same real
    opening - not a blanket downgrade of everything ever associated with
    the company, and not left untouched either. Scoped precisely to
    postings whose freshness signal actually depended on this company
    still being tracked (its own company-site postings, whose fast/
    reliable API check disappears the moment the company leaves
    job_sources.yaml - see freshness_check.py's build_api_source_lookup())
    - a cross-source duplicate found via a still-tracked board keeps
    getting checked through that board same as always, but is included
    here too since freshness_check.py evaluates every stored job record
    independently, not the deduped view the Results tab shows, so an
    unflagged duplicate would otherwise carry no visible signal that its
    matched primary's freshness got less reliable.

    Takes a copy of `jobs` internally before calling dedupe_across_sources()
    (which mutates job dicts in place, attaching _cross_source_duplicates)
    - the caller's own list objects aren't touched, only read from, and the
    transient _cross_source_duplicates attribute is never itself persisted
    by this function (only the (source, job_id) pairs it points to are
    returned)."""
    deduped = dedupe_across_sources(list(jobs))
    targets = []
    for job in deduped:
        if job.get("source") != removed_company_name:
            continue
        if job.get("job_id"):
            targets.append((job["source"], job["job_id"]))
        for dup in job.get("_cross_source_duplicates", []):
            if dup.get("source") and dup.get("job_id"):
                targets.append((dup["source"], dup["job_id"]))
    return targets
