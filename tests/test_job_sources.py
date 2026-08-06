import pytest

import search.job_sources as job_sources


@pytest.fixture
def isolated_job_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(job_sources, "JOB_SOURCES_PATH", tmp_path / "job_sources.yaml")
    return tmp_path


def test_load_returns_empty_platforms_when_file_does_not_exist(isolated_job_sources):
    assert job_sources.load_job_sources() == {"workday": [], "smartrecruiters": [], "greenhouse": [], "lever": []}


def test_save_then_load_round_trips(isolated_job_sources):
    job_sources.save_job_sources({
        "workday": [{"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15}],
        "smartrecruiters": [{"company_name": "AbbVie", "company_id": "abbvie", "limit": 15}],
        "greenhouse": [{"company_name": "Stripe", "board_token": "stripe", "limit": 10}],
        "lever": [{"company_name": "Palantir", "company_slug": "palantir", "limit": 10}],
    })
    result = job_sources.load_job_sources()
    assert result["workday"][0]["company_name"] == "Eisai"
    assert result["greenhouse"][0]["board_token"] == "stripe"
    assert result["lever"][0]["company_slug"] == "palantir"


def test_round_trip_preserves_advanced_fields_like_applied_facets(isolated_job_sources):
    job_sources.save_job_sources({
        "workday": [{
            "company_name": "IQVIA", "tenant": "iqvia", "site": "IQVIA", "wd_number": 1, "limit": 15,
            "applied_facets": {"Location_Country": ["bc33aa3152ec42d4995f4791a106ed09"]},
        }],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    result = job_sources.load_job_sources()
    assert result["workday"][0]["applied_facets"] == {"Location_Country": ["bc33aa3152ec42d4995f4791a106ed09"]}


def test_load_fills_in_missing_platform_keys(isolated_job_sources):
    # A hand-edited or older-format file might omit a platform key entirely -
    # this must resolve to an empty list, not a KeyError downstream.
    isolated_job_sources.joinpath("job_sources.yaml").write_text("workday: []\n", encoding="utf-8")
    result = job_sources.load_job_sources()
    assert result == {"workday": [], "smartrecruiters": [], "greenhouse": [], "lever": []}


def test_load_treats_null_platform_value_as_empty_list(isolated_job_sources):
    isolated_job_sources.joinpath("job_sources.yaml").write_text("workday:\nsmartrecruiters: []\ngreenhouse: []\nlever: []\n", encoding="utf-8")
    result = job_sources.load_job_sources()
    assert result["workday"] == []
