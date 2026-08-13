import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_search  # noqa: E402
from search import boards, company_sites, job_sources, job_store, source_activity, usajobs  # noqa: E402


@pytest.fixture
def isolated_run_search(isolated_data, tmp_path, monkeypatch):
    monkeypatch.setattr(job_sources, "JOB_SOURCES_PATH", tmp_path / "job_sources.yaml")
    monkeypatch.setattr(source_activity, "ACTIVITY_PATH", tmp_path / "source_activity.json")
    return tmp_path


def test_rigzone_is_not_in_the_daily_industry_board_fetchers():
    # Dropped 2026-08-07 - current live volume (~4-6 postings site-wide)
    # fails the real-recent-activity merit bar. Function kept, just not
    # wired into the daily run - see the commented-out line's own comment.
    names = [name for name, _fetch in run_search._INDUSTRY_BOARD_FETCHERS]
    assert "Rigzone" not in names


def test_search_usajobs_skips_bare_director_keyword(isolated_run_search, monkeypatch):
    # Real bug found 2026-08-11 (research task, live-verified against real
    # production data before building): a bare "Director" keyword matches
    # any federal director role regardless of field - confirmed a real,
    # measurable contributor to the 31.8%-of-all-scored-jobs senior-
    # titled-wrong-domain waste pattern. usajobs.search_jobs()'s own
    # job_category_code=2210 (IT Management series) is the real
    # structural domain filter for USAJOBS, already run separately - the
    # bare keyword search is redundant-and-noisy on top of it for this
    # one term specifically.
    calls = []
    monkeypatch.setattr(usajobs, "search_jobs", lambda **kwargs: calls.append(kwargs) or [])
    target_roles = [{"name": "CIO"}, {"name": "Director"}, {"name": "VP Information Technology"}]
    run_search.search_usajobs(target_roles, job_series=[])
    keyword_calls = [c["keyword"] for c in calls if "keyword" in c]
    assert "Director" not in keyword_calls
    assert "CIO" in keyword_calls
    assert "VP Information Technology" in keyword_calls  # a new domain-qualified role, not skipped


def test_search_usajobs_still_runs_job_category_code_search(isolated_run_search, monkeypatch):
    monkeypatch.setattr(usajobs, "search_jobs", lambda **kwargs: [])
    run_search.search_usajobs([{"name": "Director"}], job_series=["2210"])
    data = source_activity._load()
    # ran (0 error) even though the only target_role was skipped - the
    # job_category_code search still counts as a real attempt.
    assert data["USAJOBS"][0]["had_error"] is False


def _dice_job(job_id, title="CIO", organization="Acme Corp", posting_url=None):
    return {
        "source": "Dice", "job_id": job_id, "title": title, "organization": organization,
        "location": "Remote", "posting_url": posting_url or f"https://www.dice.com/job-detail/{job_id}",
    }


def test_search_dice_backfills_description_for_new_postings_only(isolated_run_search, monkeypatch):
    # Real gap found 2026-08-11 (Mirror's audit F1): fetch_dice_jobs()
    # never captured description at all. Fixed by fetching it separately
    # here, but ONLY for postings genuinely new this run - not the same
    # "re-fetch every already-stored posting on every run" shape flagged
    # as a real cost concern for Workday/SmartRecruiters (Mirror's audit F4).
    job_store.save_jobs([_dice_job("already-here")])  # pre-existing
    monkeypatch.setattr(boards, "fetch_dice_jobs", lambda keyword, limit=25: [
        _dice_job("already-here"), _dice_job("brand-new", title="VP IT"),
    ])
    fetch_calls = []
    monkeypatch.setattr(boards, "fetch_dice_job_description", lambda posting_url: fetch_calls.append(posting_url) or "Real JD text.")

    run_search.search_dice([{"name": "CIO"}])

    assert fetch_calls == ["https://www.dice.com/job-detail/brand-new"]  # not the pre-existing one
    jobs_by_id = {j["job_id"]: j for j in job_store.load_jobs()}
    assert jobs_by_id["brand-new"]["description"] == "Real JD text."
    assert "description" not in jobs_by_id["already-here"]


def test_search_dice_only_fetches_description_once_across_multiple_roles(isolated_run_search, monkeypatch):
    # The same real posting can turn up under more than one target_role
    # keyword in a single run - its JD should only be fetched once, not
    # once per role match.
    monkeypatch.setattr(boards, "fetch_dice_jobs", lambda keyword, limit=25: [_dice_job("posting-1")])
    fetch_calls = []
    monkeypatch.setattr(boards, "fetch_dice_job_description", lambda posting_url: fetch_calls.append(posting_url) or "JD text.")

    run_search.search_dice([{"name": "CIO"}, {"name": "IT Director"}])

    assert len(fetch_calls) == 1


def test_search_dice_jd_fetch_failure_does_not_stop_the_run(isolated_run_search, monkeypatch):
    monkeypatch.setattr(boards, "fetch_dice_jobs", lambda keyword, limit=25: [_dice_job("posting-1"), _dice_job("posting-2")])

    def _flaky(posting_url):
        if "posting-1" in posting_url:
            raise RuntimeError("network error")
        return "Real JD text."
    monkeypatch.setattr(boards, "fetch_dice_job_description", _flaky)

    added = run_search.search_dice([{"name": "CIO"}])

    assert added == 2  # both jobs still saved, despite one JD fetch failing
    jobs_by_id = {j["job_id"]: j for j in job_store.load_jobs()}
    assert "description" not in jobs_by_id["posting-1"]
    assert jobs_by_id["posting-2"]["description"] == "Real JD text."


def test_search_industry_boards_records_activity_per_source(isolated_run_search, monkeypatch):
    monkeypatch.setattr(run_search, "_INDUSTRY_BOARD_FETCHERS", [
        ("Planet Pharma", lambda limit: [{"source": "Planet Pharma", "job_id": "1", "title": "Director"}]),
        ("BioSpace", lambda limit: []),
    ])
    run_search.search_industry_boards()
    assert source_activity.all_tracked_sources() == ["BioSpace", "Planet Pharma"]


def test_search_industry_boards_records_error_not_a_zero(isolated_run_search, monkeypatch):
    def _raise(limit):
        raise RuntimeError("site down")
    monkeypatch.setattr(run_search, "_INDUSTRY_BOARD_FETCHERS", [("Rigzone", _raise)])
    run_search.search_industry_boards()
    data = source_activity._load()
    assert data["Rigzone"][0]["had_error"] is True


def test_search_company_sites_records_each_company_once_per_run_not_once_per_role(isolated_run_search, monkeypatch):
    job_sources.save_job_sources({
        "workday": [{"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15}],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    monkeypatch.setattr(
        company_sites, "search_workday_jobs",
        lambda company_name, tenant, site, wd_number, keyword, limit, applied_facets=None: [
            {"source": "Eisai", "job_id": f"/job/{keyword}", "title": "Director"},
        ],
    )
    target_roles = [{"name": "CIO"}, {"name": "VP"}, {"name": "Director"}]

    run_search.search_company_sites(target_roles)

    data = source_activity._load()
    assert list(data.keys()) == ["Eisai"]
    # one history entry for the whole run, not one per role searched
    assert len(data["Eisai"]) == 1
    assert data["Eisai"][0]["added"] == 3  # 3 distinct job_ids, one per role keyword


def test_search_company_sites_records_error_only_if_every_role_failed(isolated_run_search, monkeypatch):
    job_sources.save_job_sources({
        "workday": [{"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15}],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    calls = {"n": 0}

    def _flaky(company_name, tenant, site, wd_number, keyword, limit, applied_facets=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure")
        return [{"source": "Eisai", "job_id": f"/job/{keyword}", "title": "Director"}]

    monkeypatch.setattr(company_sites, "search_workday_jobs", _flaky)
    run_search.search_company_sites([{"name": "CIO"}, {"name": "VP"}])

    data = source_activity._load()
    # one role failed, one succeeded - not "every attempt failed", so this
    # isn't inconclusive-evidence, it's a real (if partial) zero/non-zero signal
    assert data["Eisai"][0]["had_error"] is False


def test_search_company_sites_does_not_crash_on_a_malformed_entry(isolated_run_search, monkeypatch):
    # Real bug found 2026-08-10 (Mirror's audit): a company entry missing a
    # required field used to raise a bare KeyError with no surrounding
    # try/except anywhere in this call chain, crashing the whole daily
    # search run past this point, not just skipping the one bad company.
    # job_sources.load_job_sources() now filters malformed entries out
    # before this function (or anything else) ever sees them - this proves
    # the fix end to end, not just at the load_job_sources() unit level.
    job_sources.save_job_sources({
        "workday": [
            {"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15},
            {"company_name": "Broken Co", "tenant": "broken"},  # missing site/wd_number/limit
        ],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    monkeypatch.setattr(
        company_sites, "search_workday_jobs",
        lambda company_name, tenant, site, wd_number, keyword, limit, applied_facets=None: [
            {"source": company_name, "job_id": f"/job/{keyword}", "title": "Director"},
        ],
    )
    added = run_search.search_company_sites([{"name": "CIO"}])  # must not raise
    assert added == 1  # only Eisai searched - Broken Co silently dropped, not crashed on

    data = source_activity._load()
    assert list(data.keys()) == ["Eisai"]  # Broken Co never reached the stats dict either


def test_search_company_sites_does_not_crash_on_syntactically_invalid_yaml(isolated_run_search, monkeypatch):
    # Real bug found 2026-08-11 (Mirror/Documentor, live-reproducible even
    # with the 2026-08-10 malformed-entry fix in place): a syntax-broken
    # config/job_sources.yaml (stray tab, unterminated bracket) crashed
    # yaml.safe_load() itself inside load_job_sources() - before any
    # per-entry filtering ever got a chance to run - taking down the whole
    # daily search run past this point, not just company-site search.
    job_sources.JOB_SOURCES_PATH.write_text(
        "workday:\n  - company_name: Acme\n\ttenant: acme\n  bad: [unterminated\n", encoding="utf-8",
    )
    added = run_search.search_company_sites([{"name": "CIO"}])  # must not raise
    assert added == 0  # nothing configured (file treated as empty), not a crash


def test_search_ats_boards_records_greenhouse_and_lever_companies_independently(isolated_run_search, monkeypatch):
    job_sources.save_job_sources({
        "workday": [], "smartrecruiters": [],
        "greenhouse": [{"company_name": "Stripe", "board_token": "stripe", "limit": 10}],
        "lever": [{"company_name": "Palantir", "company_slug": "palantir", "limit": 10}],
    })
    # "Senior Director of Engineering" (not a bare "Engineer") so this fixture
    # doesn't incidentally trip search.exclusion_filter's seniority_mismatch
    # rule - this test is about source_activity tracking for Greenhouse/Lever
    # boards, not exclusion-filter behavior, and a bare "Engineer" title would
    # now get caught by check_exclusion() inside job_store.save_jobs() before
    # ever incrementing the "added" count this test asserts on (2026-08-12).
    monkeypatch.setattr(company_sites, "search_greenhouse_jobs", lambda company_name, board_token, limit: [{"source": "Stripe", "job_id": "1", "title": "Senior Director of Engineering"}])
    monkeypatch.setattr(company_sites, "search_lever_jobs", lambda company_name, company_slug, limit: [])

    run_search.search_ats_boards()

    data = source_activity._load()
    assert data["Stripe"][0]["added"] == 1
    assert data["Palantir"][0]["added"] == 0
