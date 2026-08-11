"""User-managed ATS company list (2026-08-05), replacing the two hardcoded
Python lists that used to live in scripts/run_search.py
(_WORKDAY_COMPANIES/_SMARTRECRUITERS_COMPANIES) - part of the "industry &
location agnostic search" backlog item (PRD §13). Same idea as
config/industry_job_boards.yaml (an ever-growing lookup, editable without a
code change), but this one's user-editable from the Settings tab, not just
Zahir-editable in code - see ui/app.py's "Job-board sources" section.

Plain YAML, not security/crypto_store.py - company names/IDs aren't
sensitive, same call as industry_job_boards.yaml.

Locked with security/file_lock.py because this store now has two real
writers: scripts/run_search.py only reads it, but ui/app.py's Settings tab
writes it from a live Streamlit session while a scheduled run could be mid-
read - same shared-JSON/YAML-store race CLAUDE.md flags for jobs.json et
al. (unlike settings.yaml's existing load/save, which predates the
scheduled-script writers and hasn't been retrofitted with locking - out of
scope for this change).
"""

from pathlib import Path

import yaml

from security.file_lock import locked

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOB_SOURCES_PATH = PROJECT_ROOT / "config" / "job_sources.yaml"

PLATFORMS = ["workday", "smartrecruiters", "greenhouse", "lever"]

# Fields every caller indexes directly (company["company_name"], etc. -
# see scripts/run_search.py, freshness_check.py's build_api_source_lookup(),
# ui/app.py's "Manage companies" table population) with no .get() fallback
# anywhere. company_name is required on every platform since it's the dict
# key callers group/stats by.
_REQUIRED_FIELDS = {
    "workday": {"company_name", "tenant", "site", "wd_number", "limit"},
    "smartrecruiters": {"company_name", "company_id", "limit"},
    "greenhouse": {"company_name", "board_token", "limit"},
    "lever": {"company_name", "company_slug", "limit"},
}


def _empty() -> dict:
    return {platform: [] for platform in PLATFORMS}


def _is_valid_entry(platform: str, entry) -> bool:
    """True only if `entry` is a dict with every field its platform's
    consumers index directly, non-empty. Real bug found 2026-08-10
    (Mirror's audit): every caller of load_job_sources() does
    company["company_name"] etc. with no .get() fallback - one malformed
    row (a hand-edited YAML typo, or a Settings-tab save that slipped past
    validation before this fix) raised a bare KeyError/TypeError with no
    surrounding try/except anywhere in the call chain, crashing the whole
    scheduled search run past whichever step hit it - not just skipping
    that one company. Filtering here is the backstop that protects every
    caller at once, since they all funnel through this one function."""
    if not isinstance(entry, dict):
        return False
    for field in _REQUIRED_FIELDS[platform]:
        value = entry.get(field)
        if value is None or value == "":
            return False
    return True


def load_job_sources() -> dict:
    """Returns {"workday": [...], "smartrecruiters": [...]}, one dict per
    company per platform (same field shape as the old hardcoded lists -
    see company_sites.search_workday_jobs()/search_smartrecruiters_jobs()
    for what each field means). Missing file/missing platform key both
    resolve to an empty list, not an error - a fresh install has no
    company-site sources configured yet.

    Two defensive passes applied to whatever's on disk, silently (this is
    the backstop layer - the Settings tab's save handler is the primary
    defense and surfaces real errors there; by the time data reaches here,
    silently protecting every read-only caller is more valuable than
    raising on a row a previous save already let through):
    1. Drop any entry missing a required field (see _is_valid_entry) -
       rather than crash every caller that indexes it directly.
    2. Drop later entries sharing a company_name already seen - GLOBALLY
       across all 4 platforms, not just within one. Real bug found
       2026-08-10: scripts/run_search.py's stats dict is keyed by name
       across workday+smartrecruiters combined, and separately
       freshness_check.py's build_api_source_lookup() builds ONE lookup
       dict spanning all 4 platforms - either one silently collapses two
       same-named entries into a single slot regardless of which
       platform(s) they're on, corrupting source_activity.py's per-company
       tracking and/or freshness-checking for both."""
    with locked("job_sources"):
        if not JOB_SOURCES_PATH.exists():
            return _empty()
        with open(JOB_SOURCES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    result = _empty()
    seen_names = set()
    for platform in PLATFORMS:
        raw_entries = data.get(platform) or []
        if not isinstance(raw_entries, list):
            continue
        valid_entries = []
        for entry in raw_entries:
            if not _is_valid_entry(platform, entry):
                continue
            name = entry["company_name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            valid_entries.append(entry)
        result[platform] = valid_entries
    return result


def save_job_sources(sources: dict) -> None:
    """Overwrites the whole store. Callers should pass a full {"workday":
    [...], "smartrecruiters": [...]} dict (e.g. load_job_sources(), mutate,
    save) rather than a partial one - a partial dict would silently drop
    the platform(s) it omits."""
    with locked("job_sources"):
        JOB_SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JOB_SOURCES_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(sources, f, sort_keys=False, allow_unicode=True)
