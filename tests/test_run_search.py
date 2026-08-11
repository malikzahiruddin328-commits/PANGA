import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_search  # noqa: E402
from search import company_sites, job_sources, source_activity  # noqa: E402


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


def test_search_ats_boards_records_greenhouse_and_lever_companies_independently(isolated_run_search, monkeypatch):
    job_sources.save_job_sources({
        "workday": [], "smartrecruiters": [],
        "greenhouse": [{"company_name": "Stripe", "board_token": "stripe", "limit": 10}],
        "lever": [{"company_name": "Palantir", "company_slug": "palantir", "limit": 10}],
    })
    monkeypatch.setattr(company_sites, "search_greenhouse_jobs", lambda company_name, board_token, limit: [{"source": "Stripe", "job_id": "1", "title": "Engineer"}])
    monkeypatch.setattr(company_sites, "search_lever_jobs", lambda company_name, company_slug, limit: [])

    run_search.search_ats_boards()

    data = source_activity._load()
    assert data["Stripe"][0]["added"] == 1
    assert data["Palantir"][0]["added"] == 0
