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
both genuinely unavailable in a standalone build. Dice, Built In, and
SimplyHired are the exceptions (Dice investigated 2026-08-06; Built In/
SimplyHired added 2026-08-07 as part of the broad-scope-expansion work -
see search/boards.py's module docstring for the recon behind all three):
each turned out to be plain server-rendered HTML, no MCP needed - STEPs
2c/2d/2e below cover them directly. USAJOBS + company-site ATS APIs +
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
from search import aggregators, boards, company_sites, freshness_check, industry_boards, job_sources, job_store, source_activity, usajobs  # noqa: E402
from tailoring.applications import get_unreviewed_skip_reasons  # noqa: E402
from llm_client import spend_cap_tripped_today  # noqa: E402
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
    attempts = errors = 0
    for role in target_roles:
        attempts += 1
        try:
            jobs = usajobs.search_jobs(keyword=role["name"], results_per_page=50)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001 - one role's failure shouldn't stop the rest
            errors += 1
            _log(f"  [usajobs] keyword search failed for {role['name']!r}: {exc}")
    for code in job_series:
        attempts += 1
        try:
            jobs = usajobs.search_jobs(job_category_code=code, results_per_page=100)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            _log(f"  [usajobs] job-series search failed for {code!r}: {exc}")
    if attempts:
        source_activity.record_run_result("USAJOBS", added, had_error=errors == attempts)
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
    attempts = errors = 0
    for role in target_roles:
        for country in countries:
            attempts += 1
            try:
                jobs = aggregators.fetch_adzuna_jobs(role["name"], country, limit=25)
                added += job_store.save_jobs(jobs)
            except aggregators.AdzunaBudgetExceeded as exc:
                _log(f"  [aggregators] {exc} - stopping Adzuna search for the rest of this run")
                # Budget exhaustion isn't evidence of staleness (or of
                # anything about Adzuna's own posting activity) - it means
                # this run stopped early, not that no new jobs exist.
                source_activity.record_run_result("Adzuna", added, had_error=True)
                return added
            except Exception as exc:  # noqa: BLE001 - one (role, country) pair's failure shouldn't stop the rest
                errors += 1
                _log(f"  [aggregators] Adzuna search failed for {role['name']!r} / {country!r}: {exc}")
    if attempts:
        source_activity.record_run_result("Adzuna", added, had_error=errors == attempts)
    return added


def search_dice(target_roles: list[dict]) -> int:
    """Direct scrape, no MCP - see boards.fetch_dice_jobs()'s docstring for
    why this is safe to run unattended (server-rendered, plain `requests`
    reaches it fine, unlike ZipRecruiter/Indeed's WAF)."""
    added = 0
    errors = 0
    for role in target_roles:
        try:
            jobs = boards.fetch_dice_jobs(role["name"], limit=25)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001 - one role's failure shouldn't stop the rest
            errors += 1
            _log(f"  [boards] Dice search failed for {role['name']!r}: {exc}")
    if target_roles:
        source_activity.record_run_result("Dice", added, had_error=errors == len(target_roles))
    return added


def search_built_in(target_roles: list[dict]) -> int:
    """Direct scrape, no MCP - see boards.fetch_built_in_jobs()'s docstring.
    Searched per target-role keyword, same reasoning as search_dice(): an
    unfiltered fetch of a broad multi-employer board could pull in
    thousands of unrelated roles (Zahir's own example: a nurse, a supply-
    chain specialist), unlike Greenhouse/Lever's fetch-everything pattern,
    which only stays safe because it's bounded to one company's postings."""
    added = 0
    errors = 0
    for role in target_roles:
        try:
            jobs = boards.fetch_built_in_jobs(role["name"], limit=25)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001 - one role's failure shouldn't stop the rest
            errors += 1
            _log(f"  [boards] Built In search failed for {role['name']!r}: {exc}")
    if target_roles:
        source_activity.record_run_result("Built In", added, had_error=errors == len(target_roles))
    return added


def search_simplyhired(target_roles: list[dict]) -> int:
    """Direct scrape, no MCP - see boards.fetch_simplyhired_jobs()'s
    docstring. Same per-target-role-keyword reasoning as search_built_in()."""
    added = 0
    errors = 0
    for role in target_roles:
        try:
            jobs = boards.fetch_simplyhired_jobs(role["name"], limit=25)
            added += job_store.save_jobs(jobs)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            _log(f"  [boards] SimplyHired search failed for {role['name']!r}: {exc}")
    if target_roles:
        source_activity.record_run_result("SimplyHired", added, had_error=errors == len(target_roles))
    return added


def search_company_sites(target_roles: list[dict]) -> int:
    """Companies come from config/job_sources.yaml (user-managed from the
    Settings tab, not hardcoded here) - see search/job_sources.py."""
    sources = job_sources.load_job_sources()
    added = 0
    # Per-company stats accumulated across the whole role loop (a company
    # is searched once per target role) so each company's activity gets
    # recorded exactly once per run, not once per role.
    stats = {c["company_name"]: [0, 0, 0] for c in sources["workday"] + sources["smartrecruiters"]}  # [added, attempts, errors]
    for role in target_roles:
        for company in sources["workday"]:
            name = company["company_name"]
            stats[name][1] += 1
            try:
                jobs = company_sites.search_workday_jobs(
                    name, company["tenant"], company["site"], company["wd_number"],
                    keyword=role["name"], limit=company["limit"],
                    applied_facets=company.get("applied_facets"),
                )
                got = job_store.save_jobs(jobs)
                added += got
                stats[name][0] += got
            except Exception as exc:  # noqa: BLE001 - one company's failure shouldn't stop the rest
                stats[name][2] += 1
                _log(f"  [company_sites] Workday search failed for {name} / {role['name']!r}: {exc}")
        for company in sources["smartrecruiters"]:
            name = company["company_name"]
            stats[name][1] += 1
            try:
                jobs = company_sites.search_smartrecruiters_jobs(
                    name, company["company_id"], keyword=role["name"], limit=company["limit"],
                )
                got = job_store.save_jobs(jobs)
                added += got
                stats[name][0] += got
            except Exception as exc:  # noqa: BLE001
                stats[name][2] += 1
                _log(f"  [company_sites] SmartRecruiters search failed for {name} / {role['name']!r}: {exc}")
    for name, (company_added, attempts, errors) in stats.items():
        if attempts:
            source_activity.record_run_result(name, company_added, had_error=errors == attempts)
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
        name = company["company_name"]
        try:
            jobs = company_sites.search_greenhouse_jobs(name, company["board_token"], limit=company["limit"])
            got = job_store.save_jobs(jobs)
            added += got
            source_activity.record_run_result(name, got, had_error=False)
        except Exception as exc:  # noqa: BLE001 - one company's failure shouldn't stop the rest
            source_activity.record_run_result(name, 0, had_error=True)
            _log(f"  [company_sites] Greenhouse search failed for {name}: {exc}")
    for company in sources["lever"]:
        name = company["company_name"]
        try:
            jobs = company_sites.search_lever_jobs(name, company["company_slug"], limit=company["limit"])
            got = job_store.save_jobs(jobs)
            added += got
            source_activity.record_run_result(name, got, had_error=False)
        except Exception as exc:  # noqa: BLE001
            source_activity.record_run_result(name, 0, had_error=True)
            _log(f"  [company_sites] Lever search failed for {name}: {exc}")
    return added


_INDUSTRY_BOARD_FETCHERS = [
    ("Planet Pharma", industry_boards.fetch_planet_pharma_jobs),
    ("BioSpace", industry_boards.fetch_biospace_jobs),
    ("Beacon Hill", industry_boards.fetch_beacon_hill_jobs),
    ("Atrium", industry_boards.fetch_atrium_jobs),
    ("GForce", industry_boards.fetch_gforce_jobs),
    # Rigzone dropped from the daily run (2026-08-07, Zahir's explicit call
    # via General) - live-checked during the board-scope-expansion work and
    # its own site-wide listing (no filter at all) is only ~4-6 postings
    # right now, vs. "thousands unfiltered" at its original 2026-08-04
    # recon. Fails merit criterion #1 (real recent activity) outright -
    # fixing fetch_rigzone_jobs()'s location default (currently defaults to
    # "United Kingdom", never overridden here, a separate real bug also
    # found during this pass) wouldn't fix a source that's nearly dead
    # right now regardless of which country it's pointed at. Function kept
    # as-is, not deleted, so it's easy to re-enable if Rigzone's volume
    # recovers - source_activity.py (added this same pass) will surface
    # that automatically once enough of the OTHER sources below run
    # consecutively without Rigzone contributing to a false "stale"
    # judgment on itself while it's disabled (is_source_stale() only
    # counts REAL runs, and a disabled source simply gets no new runs
    # recorded at all rather than being force-fed zeros).
    # ("Rigzone", industry_boards.fetch_rigzone_jobs),
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
            got = job_store.save_jobs(jobs)
            added += got
            source_activity.record_run_result(name, got, had_error=False)
        except Exception as exc:  # noqa: BLE001 - one site's failure shouldn't stop the rest
            source_activity.record_run_result(name, 0, had_error=True)
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


def notify(strong_matches: list[dict], unreviewed_skip_count: int, spend_cap_hit: bool = False) -> None:
    """spend_cap_hit (2026-08-11 fast-follow): whether llm_client's daily
    spend cap blocked any call today, in any process - checked once in
    run() via llm_client.spend_cap_tripped_today() and passed in here
    rather than queried again, so this stays a pure formatting function.
    Before this, a tripped cap was only visible in the Ops tab or
    panga_debug.log - Zahir's own daily-search notification said nothing
    about it, so a day where the cap silently cut scoring short looked
    identical to a normal quiet day with no matches. Listed FIRST (not
    appended) so it survives the message[:200] truncation below and is
    the first thing read, matching the "genuinely visible, unmissable"
    bar the cap itself was already held to (its own CRITICAL log line).
    This also means a cap-hit day with zero strong matches and zero
    unreviewed reasons now still sends a notification (previously "not
    parts: return" would have silently sent nothing at all on such a day
    - arguably worse than the specific gap this was built to close)."""
    parts = []
    if spend_cap_hit:
        parts.append("Daily AI spend cap was hit today - some job scoring may have been skipped. Check the Ops tab.")
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

    _log("STEP 2d - Built In (direct scrape, no MCP needed)")
    added = search_built_in(target_roles)
    _log(f"  added {added} new job(s)")

    _log("STEP 2e - SimplyHired (direct scrape, no MCP needed)")
    added = search_simplyhired(target_roles)
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
    spend_cap_hit = spend_cap_tripped_today()
    if spend_cap_hit:
        _log("  daily spend cap was hit today - flagging in the notification")
    notify(strong_matches, len(unreviewed), spend_cap_hit)

    _log("STEP 8 - Freshness check")
    checked, marked, newly_pending, reopened = freshness_check.check_and_mark_closed_postings()
    _log(f"  checked {checked} job(s), marked {marked} closed, {newly_pending} newly pending "
         f"(needs a second day's confirmation), {reopened} reopened")

    _log("Done.")


if __name__ == "__main__":
    run()
