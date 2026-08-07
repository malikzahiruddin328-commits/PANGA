"""One-off backfill (2026-08-06): fills in real JD text for jobs saved
before company_sites.py's search_workday_jobs()/search_smartrecruiters_jobs()
started capturing a per-job description at search time (see the commit that
added this script for the full story - ATS keyword extraction was coming
back empty for every non-USAJOBS/non-LinkedIn-manual job because most
sources never populated any real posting text at all).

Scope: Workday (Eisai, IQVIA) and SmartRecruiters (AbbVie) only - same
companies scripts/run_search.py already knows about, same detail endpoints
company_sites.py's freshness-check functions already call successfully.
Dice jobs are NOT covered here: Dice is only reachable as a live MCP
connector tool inside a Claude Code conversation (see boards.py/SKILL.md),
so backfilling Dice's already-saved jobs needs a live conversation, not a
standalone script - do that separately, matching normalize_dice_job()'s
now-fixed "summary" capture against a fresh live search.
ZipRecruiter/Indeed/industry-board jobs have no available fix at all
(confirmed live 2026-08-06 - see test_boards.py) - nothing to backfill.

Run once: venv\\Scripts\\python.exe scripts\\backfill_jd_text.py
Safe to re-run - only touches jobs that still lack a description.
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from search import company_sites, job_store  # noqa: E402

_WORKDAY_COMPANIES = {
    "Eisai": dict(tenant="eisai", site="eisai", wd_number=5),
    "IQVIA": dict(tenant="iqvia", site="IQVIA", wd_number=1),
}
_SMARTRECRUITERS_COMPANIES = {
    "AbbVie": dict(company_id="abbvie"),
}


def _log(message: str) -> None:
    print(message, flush=True)


def backfill() -> int:
    jobs = job_store.load_jobs()
    updated = 0

    for job in jobs:
        source = job.get("source")
        if job.get("description"):
            continue

        if source in _WORKDAY_COMPANIES:
            cfg = _WORKDAY_COMPANIES[source]
            external_path = job.get("job_id")
            if not external_path:
                continue
            description = company_sites._fetch_workday_job_description(
                cfg["tenant"], cfg["site"], cfg["wd_number"], external_path,
            )
        elif source in _SMARTRECRUITERS_COMPANIES:
            cfg = _SMARTRECRUITERS_COMPANIES[source]
            posting_id = job.get("job_id")
            if not posting_id:
                continue
            description = company_sites._fetch_smartrecruiters_job_description(cfg["company_id"], posting_id)
        else:
            continue

        if not description:
            _log(f"  [skip] no description found for {source} / {job.get('title')!r} ({job.get('job_id')})")
            continue

        job_store.update_job_description(source, job.get("job_id"), description)
        updated += 1
        _log(f"  [ok] backfilled {source} / {job.get('title')!r} ({len(description)} chars)")

    return updated


if __name__ == "__main__":
    count = backfill()
    _log(f"Backfilled JD text for {count} job(s).")
