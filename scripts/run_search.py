"""Entry point for scheduled + on-demand search runs (native-packaging
branch, 2026-07-31). Standalone replacement for the panga-daily-job-search
Claude scheduled task's SKILL.md - runs mechanical source searches plus
direct-API fit scoring with no live Claude Code session required, so it can
be invoked from Windows Task Scheduler in a standalone build.

Ported step-for-step from
C:\\Users\\User\\.claude\\scheduled-tasks\\panga-daily-job-search\\SKILL.md,
with one deliberate scope cut: STEP 2 (ZipRecruiter/Dice/Indeed via MCP
connector tools) is dropped - see docs/native-packaging-scope.md's Phase 1
spike. ZipRecruiter has no API usable outside that connector (only a
"Publisher Partner" program built for job-aggregator sites, not a personal
search tool) and Indeed's connector tool has no non-MCP equivalent either -
both genuinely unavailable in a standalone build. Dice is the exception
(investigated fresh 2026-08-06, see search/boards.py's fetch_dice_jobs()):
its search-results page turned out to be plain server-rendered HTML, no MCP
needed - STEP 2c below covers it directly. USAJOBS + company-site ATS APIs +
industry-board scraping + Adzuna (steps 1, 2b, 3, 4) are also unaffected,
since none of those ever depended on MCP either.
"""

import sys
from pathlib import Path

# Real job titles/organization names contain arbitrary Unicode - a Windows
# console's default codepage can't encode most of it, so an unguarded
# print() would crash the whole run on one (same issue found live in
# gmail_cta_scan.py - see its identical comment).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import yaml  # noqa: E402

from notifications import send_notification  # noqa: E402
from profile.storage import load_profile  # noqa: E402
from search import aggregators, boards, company_sites, freshness_check, industry_boards, job_sources, job_store, usajobs  # noqa: E402
from tailoring.applications import get_unreviewed_skip_reasons  # noqa: E402
from tailoring.drafting import DraftingFailed, DraftingNotConfigured, score_job  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def _load_settings() -> dict:
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _log(message: str) -> None:
    print(message, flush=True)


def search_usajobs(target_roles: list[dict], job_series: list[str]) -> int:
    added = 0
    for role in target_roles:
        try:
            jobs = usajobs.search_jobs(keyword=role["name"], results_per_page=50)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001 - one role's failure shouldn't stop the rest
            _log(f"  [usajobs] keyword search failed for {role['name']!r}: {exc}")
    for code in job_series:
        try:
            jobs = usajobs.search_jobs(job_category_code=code, results_per_page=100)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001
            _log(f"  [usajobs] job-series search failed for {code!r}: {exc}")
    return added


def search_aggregators(target_roles: list[dict], countries: list[str]) -> int:
    """Adzuna, per role per country. Skips the whole step (not an error)
    if credentials aren't set up, same as USAJOBS/Gmail/drafting today.
    Stops making further Adzuna calls the moment the daily call budget is
    used up rather than letting later (role, country) pairs fail one by
    one with the same error - see aggregators.py's own docstring for why
    this needs a real budget check, not just a rate-limit sleep."""
    if not aggregators.is_configured():
        _log("  [aggregators] Adzuna not configured (ADZUNA_APP_ID/ADZUNA_APP_KEY not in .env) - skipping")
        return 0
    if not countries:
        _log("  [aggregators] no Adzuna search countries configured in Settings - skipping")
        return 0

    added = 0
    for role in target_roles:
        for country in countries:
            try:
                jobs = aggregators.fetch_adzuna_jobs(role["name"], country, limit=25)
                added += job_store.save_jobs(jobs)
            except aggregators.AdzunaBudgetExceeded as exc:
                _log(f"  [aggregators] {exc} - stopping Adzuna search for the rest of this run")
                return added
            except Exception as exc:  # noqa: BLE001 - one (role, country) pair's failure shouldn't stop the rest
                _log(f"  [aggregators] Adzuna search failed for {role['name']!r} / {country!r}: {exc}")
    return added


def search_dice(target_roles: list[dict]) -> int:
    """Direct scrape, no MCP - see boards.fetch_dice_jobs()'s docstring for
    why this is safe to run unattended (server-rendered, plain `requests`
    reaches it fine, unlike ZipRecruiter/Indeed's WAF)."""
    added = 0
    for role in target_roles:
        try:
            jobs = boards.fetch_dice_jobs(role["name"], limit=25)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001 - one role's failure shouldn't stop the rest
            _log(f"  [boards] Dice search failed for {role['name']!r}: {exc}")
    return added


def search_company_sites(target_roles: list[dict]) -> int:
    """Companies come from config/job_sources.yaml (user-managed from the
    Settings tab, not hardcoded here) - see search/job_sources.py."""
    sources = job_sources.load_job_sources()
    added = 0
    for role in target_roles:
        for company in sources["workday"]:
            try:
                jobs = company_sites.search_workday_jobs(
                    company["company_name"], company["tenant"], company["site"], company["wd_number"],
                    keyword=role["name"], limit=company["limit"],
                    applied_facets=company.get("applied_facets"),
                )
                added += job_store.save_jobs(jobs)
            except Exception as exc:  # noqa: BLE001 - one company's failure shouldn't stop the rest
                _log(f"  [company_sites] Workday search failed for {company['company_name']} / {role['name']!r}: {exc}")
        for company in sources["smartrecruiters"]:
            try:
                jobs = company_sites.search_smartrecruiters_jobs(
                    company["company_name"], company["company_id"], keyword=role["name"], limit=company["limit"],
                )
                added += job_store.save_jobs(jobs)
            except Exception as exc:  # noqa: BLE001
                _log(f"  [company_sites] SmartRecruiters search failed for {company['company_name']} / {role['name']!r}: {exc}")
    return added


def search_ats_boards() -> int:
    """Greenhouse/Lever companies from config/job_sources.yaml. Unlike
    search_company_sites() above, this is NOT called per target role -
    neither platform's public API supports server-side keyword search, so
    looping it by role would just refetch the identical full board N
    times for zero extra data. One fetch per company, same shape as
    search_industry_boards() below; compatibility scoring is what
    actually filters for relevance, same as those sources."""
    sources = job_sources.load_job_sources()
    added = 0
    for company in sources["greenhouse"]:
        try:
            jobs = company_sites.search_greenhouse_jobs(
                company["company_name"], company["board_token"], limit=company["limit"],
            )
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001 - one company's failure shouldn't stop the rest
            _log(f"  [company_sites] Greenhouse search failed for {company['company_name']}: {exc}")
    for company in sources["lever"]:
        try:
            jobs = company_sites.search_lever_jobs(
                company["company_name"], company["company_slug"], limit=company["limit"],
            )
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001
            _log(f"  [company_sites] Lever search failed for {company['company_name']}: {exc}")
    return added


_INDUSTRY_BOARD_FETCHERS = [
    ("Planet Pharma", industry_boards.fetch_planet_pharma_jobs),
    ("BioSpace", industry_boards.fetch_biospace_jobs),
    ("Beacon Hill", industry_boards.fetch_beacon_hill_jobs),
    ("Atrium", industry_boards.fetch_atrium_jobs),
    ("GForce", industry_boards.fetch_gforce_jobs),
    ("Rigzone", industry_boards.fetch_rigzone_jobs),
    # IChemE Job Board deliberately NOT wired in here - fetch_icheme_jobs()
    # below is written and its parsing logic verified against real markup,
    # but the live site 403s Python's requests library specifically (works
    # fine via curl/PowerShell's WinHTTP stack) - see the function's
    # docstring and config/industry_job_boards.yaml. Not safe to run daily
    # until that's resolved.
]


def search_industry_boards() -> int:
    added = 0
    for name, fetch in _INDUSTRY_BOARD_FETCHERS:
        try:
            jobs = fetch(limit=25)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001 - one site's failure shouldn't stop the rest
            _log(f"  [industry_boards] {name} fetch failed: {exc}")
    return added


def score_unscored_jobs(profile: dict) -> list[dict]:
    """Scores every job missing a fit_score via the direct API (mirrors the
    exact rubric tailoring.drafting.score_job already uses for manually-added
    jobs, per docs/native-packaging-scope.md Phase 1). Returns the list of
    newly-scored job records that scored 60+, for the notification step."""
    jobs = job_store.load_jobs()
    unscored = [j for j in jobs if "fit_score" not in j]
    if not unscored:
        return []

    _log(f"Scoring {len(unscored)} new job(s)...")
    strong_matches = []
    for job in unscored:
        try:
            result = score_job(job, profile)
        except (DraftingNotConfigured, DraftingFailed) as exc:
            _log(f"  [score] failed for {job.get('title')!r} at {job.get('organization')!r}: {exc}")
            continue
        job_store.update_job_score(job.get("source"), job.get("job_id"), result["fit_score"], result["fit_rationale"])
        if result["fit_score"] >= 60:
            strong_matches.append({**job, **result})
    return strong_matches


def notify(strong_matches: list[dict], unreviewed_skip_count: int) -> None:
    parts = []
    if strong_matches:
        listed = ", ".join(f"{j['title']} at {j['organization']} ({j['fit_score']})" for j in strong_matches[:3])
        remainder = len(strong_matches) - 3
        if remainder > 0:
            listed += f", +{remainder} more"
        parts.append(f"{len(strong_matches)} strong new match{'es' if len(strong_matches) != 1 else ''}: {listed}")
    if unreviewed_skip_count:
        parts.append(
            f"{unreviewed_skip_count} rejection reason{'s' if unreviewed_skip_count != 1 else ''} ready for your review"
        )
    if not parts:
        return
    message = ". Also: ".join(parts) if len(parts) > 1 else parts[0]
    send_notification("Panga - Daily job search", message[:200])


def run() -> None:
    settings = _load_settings()
    target_roles = settings.get("target_roles", [])
    job_series = settings.get("usajobs_job_series", [])

    _log("STEP 1 - USAJOBS")
    added = search_usajobs(target_roles, job_series)
    _log(f"  added {added} new job(s)")

    _log("STEP 2 - ZipRecruiter/Indeed: skipped (not available outside the Claude Code MCP "
         "connector - see docs/native-packaging-scope.md Phase 1 spike)")

    _log("STEP 2b - Adzuna aggregator")
    added = search_aggregators(target_roles, settings.get("aggregator_countries", []))
    _log(f"  added {added} new job(s)")

    _log("STEP 2c - Dice (direct scrape, no MCP needed - see boards.fetch_dice_jobs())")
    added = search_dice(target_roles)
    _log(f"  added {added} new job(s)")

    _log("STEP 3 - Company career sites")
    added = search_company_sites(target_roles)
    _log(f"  added {added} new job(s)")

    _log("STEP 3b - Greenhouse/Lever company boards")
    added = search_ats_boards()
    _log(f"  added {added} new job(s)")

    _log("STEP 4 - Industry job boards")
    added = search_industry_boards()
    _log(f"  added {added} new job(s)")

    _log("STEP 5 - Scoring")
    profile = load_profile()
    strong_matches = score_unscored_jobs(profile)

    _log("STEP 6 - Unreviewed 'not interested' reasons")
    unreviewed = get_unreviewed_skip_reasons()
    _log(f"  {len(unreviewed)} unreviewed")

    _log("STEP 7 - Notify")
    notify(strong_matches, len(unreviewed))

    _log("STEP 8 - Freshness check")
    checked, marked = freshness_check.check_and_mark_closed_postings()
    _log(f"  checked {checked} job(s), marked {marked} closed")

    _log("Done.")


if __name__ == "__main__":
    run()
