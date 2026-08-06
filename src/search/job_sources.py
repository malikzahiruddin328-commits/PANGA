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


def _empty() -> dict:
    return {platform: [] for platform in PLATFORMS}


def load_job_sources() -> dict:
    """Returns {"workday": [...], "smartrecruiters": [...]}, one dict per
    company per platform (same field shape as the old hardcoded lists -
    see company_sites.search_workday_jobs()/search_smartrecruiters_jobs()
    for what each field means). Missing file/missing platform key both
    resolve to an empty list, not an error - a fresh install has no
    company-site sources configured yet."""
    with locked("job_sources"):
        if not JOB_SOURCES_PATH.exists():
            return _empty()
        with open(JOB_SOURCES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    result = _empty()
    for platform in PLATFORMS:
        result[platform] = data.get(platform) or []
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
