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


# --- Malformed/duplicate entry handling (2026-08-10, Mirror's audit) ---
# Real bug: every caller of load_job_sources() (run_search.py,
# freshness_check.py, ui/app.py's table population) indexes
# company["company_name"] etc. directly, no .get() fallback anywhere -
# one bad entry crashed the whole daily search run, not just that company.

def test_load_drops_entry_missing_a_required_field(isolated_job_sources):
    job_sources.save_job_sources({
        "workday": [
            {"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15},
            {"company_name": "Broken Co", "tenant": "broken"},  # missing site/wd_number/limit
        ],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    result = job_sources.load_job_sources()
    assert [c["company_name"] for c in result["workday"]] == ["Eisai"]


def test_load_drops_entry_with_empty_string_required_field(isolated_job_sources):
    job_sources.save_job_sources({
        "workday": [], "smartrecruiters": [
            {"company_name": "", "company_id": "abbvie", "limit": 15},
            {"company_name": "AbbVie", "company_id": "abbvie", "limit": 15},
        ],
        "greenhouse": [], "lever": [],
    })
    result = job_sources.load_job_sources()
    assert [c["company_name"] for c in result["smartrecruiters"]] == ["AbbVie"]


def test_load_drops_entry_that_is_not_a_dict(isolated_job_sources):
    # A hand-edited YAML could have a bare string/list where a mapping is
    # expected - must not crash load_job_sources() itself either.
    isolated_job_sources.joinpath("job_sources.yaml").write_text(
        "workday:\n  - just a string\n  - company_name: Eisai\n    tenant: eisai\n    site: eisai\n    wd_number: 5\n    limit: 15\n"
        "smartrecruiters: []\ngreenhouse: []\nlever: []\n",
        encoding="utf-8",
    )
    result = job_sources.load_job_sources()
    assert [c["company_name"] for c in result["workday"]] == ["Eisai"]


def test_load_drops_duplicate_company_name_within_one_platform(isolated_job_sources):
    job_sources.save_job_sources({
        "workday": [
            {"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15},
            {"company_name": "Eisai", "tenant": "eisai-uk", "site": "eisai-uk", "wd_number": 2, "limit": 15},
        ],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    result = job_sources.load_job_sources()
    assert len(result["workday"]) == 1
    assert result["workday"][0]["tenant"] == "eisai"  # first occurrence kept


def test_load_logs_a_dropped_duplicate_company_name_instead_of_silently_dropping_it(isolated_job_sources, capsys):
    # Real gap found 2026-08-10 (backlog-log.md): two entries sharing a
    # company_name used to silently collapse activity-tracking stats onto
    # whichever was processed last, with the other's real config/tracked
    # activity lost and no signal anywhere that it happened. The dedup
    # itself (keep-first-occurrence) already existed - what was missing is
    # this: the drop needs to be visible, not silent, same "never silently
    # drop, always log" discipline as every other filtering path in this
    # module.
    job_sources.save_job_sources({
        "workday": [
            {"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15},
            {"company_name": "Eisai", "tenant": "eisai-uk", "site": "eisai-uk", "wd_number": 2, "limit": 15},
        ],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    job_sources.load_job_sources()
    out = capsys.readouterr().out
    assert "Eisai" in out and "already used" in out


def test_load_drops_duplicate_company_name_across_different_platforms(isolated_job_sources):
    # Real bug found 2026-08-10: freshness_check.py's build_api_source_lookup()
    # keys ONE dict by company_name across all 4 platforms combined - a name
    # reused on a different platform silently overwrites the earlier one
    # there, even though they're in separate lists here.
    job_sources.save_job_sources({
        "workday": [{"company_name": "Acme", "tenant": "acme", "site": "acme", "wd_number": 1, "limit": 15}],
        "smartrecruiters": [],
        "greenhouse": [{"company_name": "Acme", "board_token": "acme", "limit": 15}],
        "lever": [],
    })
    result = job_sources.load_job_sources()
    assert len(result["workday"]) == 1
    assert len(result["greenhouse"]) == 0  # dropped - name already claimed by workday, seen first


def test_load_handles_a_platform_value_that_is_not_a_list(isolated_job_sources):
    isolated_job_sources.joinpath("job_sources.yaml").write_text(
        "workday: not-a-list\nsmartrecruiters: []\ngreenhouse: []\nlever: []\n", encoding="utf-8",
    )
    result = job_sources.load_job_sources()
    assert result["workday"] == []


# --- Structural/type-level crashes (2026-08-11, Mirror/Documentor) ---
# Real bug: the 2026-08-10 fix above only checked field *presence*, not
# whether the whole file was even parseable, or whether a present field
# had a type downstream fetch code could actually use - both still
# crashed load_job_sources() (or a real search call further downstream)
# with no handling anywhere in the call chain, live-reproduced 2026-08-11.

def test_load_survives_syntactically_invalid_yaml(isolated_job_sources, capsys):
    # A stray tab in an indented block is invalid YAML - yaml.safe_load()
    # used to raise ScannerError straight out of this function.
    isolated_job_sources.joinpath("job_sources.yaml").write_text(
        "workday:\n  - company_name: Acme\n\ttenant: acme\n  bad: [unterminated\n", encoding="utf-8",
    )
    result = job_sources.load_job_sources()
    assert result == {"workday": [], "smartrecruiters": [], "greenhouse": [], "lever": []}
    assert "not valid YAML" in capsys.readouterr().out


def test_load_survives_non_mapping_top_level(isolated_job_sources, capsys):
    # Valid YAML, but a bare list at the top level instead of a mapping -
    # data.get(platform) used to raise AttributeError since a list has no
    # .get().
    isolated_job_sources.joinpath("job_sources.yaml").write_text("- workday\n- smartrecruiters\n", encoding="utf-8")
    result = job_sources.load_job_sources()
    assert result == {"workday": [], "smartrecruiters": [], "greenhouse": [], "lever": []}
    assert "not a mapping" in capsys.readouterr().out


def test_load_drops_entry_with_non_numeric_wd_number(isolated_job_sources, capsys):
    # Present and non-empty (passes the old presence-only check) but not a
    # real number - company_sites.search_workday_jobs() would still crash
    # the moment this specific company was actually searched.
    isolated_job_sources.joinpath("job_sources.yaml").write_text(
        "workday:\n"
        "  - company_name: Broken Co\n    tenant: broken\n    site: broken\n    wd_number: not-a-number\n    limit: 15\n"
        "  - company_name: Eisai\n    tenant: eisai\n    site: eisai\n    wd_number: 5\n    limit: 15\n"
        "smartrecruiters: []\ngreenhouse: []\nlever: []\n",
        encoding="utf-8",
    )
    result = job_sources.load_job_sources()
    assert [c["company_name"] for c in result["workday"]] == ["Eisai"]
    assert "Broken Co" in capsys.readouterr().out


def test_load_drops_entry_with_non_numeric_limit(isolated_job_sources):
    # Same crash mode as wd_number, but for a field every platform shares
    # (search_greenhouse_jobs()/search_lever_jobs() slice postings[:limit]
    # - a non-int limit raises TypeError on the slice).
    isolated_job_sources.joinpath("job_sources.yaml").write_text(
        "workday: []\nsmartrecruiters: []\n"
        "greenhouse:\n  - company_name: Stripe\n    board_token: stripe\n    limit: '10'\n"
        "lever: []\n",
        encoding="utf-8",
    )
    result = job_sources.load_job_sources()
    assert result["greenhouse"] == []


def test_load_accepts_a_real_int_limit_and_wd_number(isolated_job_sources):
    # Sanity check the type check above isn't overly strict about the
    # normal, valid case.
    job_sources.save_job_sources({
        "workday": [{"company_name": "Eisai", "tenant": "eisai", "site": "eisai", "wd_number": 5, "limit": 15}],
        "smartrecruiters": [], "greenhouse": [], "lever": [],
    })
    result = job_sources.load_job_sources()
    assert len(result["workday"]) == 1
