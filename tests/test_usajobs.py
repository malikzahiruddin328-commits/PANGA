import pytest

import search.usajobs as usajobs


class _FakeResponse:
    def __init__(self, json_data, status_code=200, text=None):
        self._json_data = json_data
        self.status_code = status_code
        # check_position_open() checks .text for the 204-empty-body case
        # the real Historic JOA API returns for an unmatched id (verified
        # live 2026-08-17) - default to a non-empty stand-in unless a test
        # explicitly wants the empty-body case.
        self.text = "" if text is None and json_data is None else (text if text is not None else "x")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "test-key")
    monkeypatch.setenv("USAJOBS_USER_AGENT_EMAIL", "test@example.com")


def _search_result(items):
    return {"SearchResult": {"SearchResultItems": items, "SearchResultCountAll": len(items)}}


def _item(position_id="P1", title="IT Specialist", job_category=None):
    # Defaults to a real-shaped, matching JobCategory (verified live
    # 2026-08-17 against JobCategoryCode=2210:
    # MatchedObjectDescriptor.JobCategory = [{"Name": ..., "Code": ...}])
    # so existing tests that don't care about category validation keep
    # passing unchanged. Pass job_category=[] or a mismatched code to
    # exercise the validation itself.
    if job_category is None:
        job_category = [{"Name": "Information Technology Management", "Code": "2210"}]
    return {
        "MatchedObjectDescriptor": {
            "PositionID": position_id,
            "PositionTitle": title,
            "OrganizationName": "Dept of Example",
            "DepartmentName": "Dept of Example",
            "PositionLocationDisplay": "Washington, DC",
            "PositionRemuneration": [{"MinimumRange": "100000", "MaximumRange": "150000"}],
            "ApplyURI": ["https://example.gov/apply"],
            "PositionURI": "https://example.gov/job",
            "QualificationSummary": "Some summary",
            "JobCategory": job_category,
        }
    }


def test_search_jobs_by_series_and_grade_raises_without_credentials(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    monkeypatch.delenv("USAJOBS_USER_AGENT_EMAIL", raising=False)
    with pytest.raises(usajobs.USAJobsNotConfigured):
        usajobs.search_jobs_by_series_and_grade(["2210"], "12", "15")


def test_search_jobs_by_series_and_grade_joins_series_with_semicolon(monkeypatch, configured):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_search_result([]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    usajobs.search_jobs_by_series_and_grade(
        ["1550", "1515", "0335", "2210", "0854"], "12", "15",
    )

    assert captured["params"]["JobCategoryCode"] == "1550;1515;0335;2210;0854"


def test_search_jobs_by_series_and_grade_uses_hiring_path_not_who_may_apply(monkeypatch, configured):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_search_result([]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    usajobs.search_jobs_by_series_and_grade(["2210"], "12", "15")

    assert captured["params"]["HiringPath"] == "public"
    assert "WhoMayApply" not in captured["params"]


def test_search_jobs_by_series_and_grade_sends_pay_grade_band(monkeypatch, configured):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_search_result([]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    usajobs.search_jobs_by_series_and_grade(["2210"], "12", "15")

    assert captured["params"]["PayGradeLow"] == "12"
    assert captured["params"]["PayGradeHigh"] == "15"


def test_search_jobs_by_series_and_grade_parses_results(monkeypatch, configured):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_search_result([_item()]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs_by_series_and_grade(["2210"], "12", "15")

    assert len(jobs) == 1
    assert jobs[0]["source"] == "USAJOBS"
    assert jobs[0]["job_id"] == "P1"
    assert jobs[0]["title"] == "IT Specialist"
    assert jobs[0]["pay_min"] == "100000"
    assert jobs[0]["pay_max"] == "150000"


def test_search_jobs_by_series_and_grade_optional_location(monkeypatch, configured):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_search_result([]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    usajobs.search_jobs_by_series_and_grade(["2210"], "12", "15", location="Washington, DC")

    assert captured["params"]["LocationName"] == "Washington, DC"


def test_search_jobs_by_series_and_grade_default_does_not_fetch_executive_grades(monkeypatch, configured):
    call_count = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        call_count["n"] += 1
        return _FakeResponse(_search_result([_item(position_id="P1")]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs_by_series_and_grade(["2210"], "12", "15")

    assert call_count["n"] == 1
    assert len(jobs) == 1


def test_search_jobs_by_series_and_grade_executive_grades_issues_second_call(monkeypatch, configured):
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        if "JobGrade" in params:
            return _FakeResponse(_search_result([_item(position_id="EXEC1", title="Chief Information Security Officer")]))
        return _FakeResponse(_search_result([_item(position_id="P1")]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs_by_series_and_grade(
        ["2210"], "12", "15", include_executive_grades=True,
    )

    assert len(calls) == 2
    # first call: the existing GS-band query, unchanged
    assert calls[0]["PayGradeLow"] == "12"
    assert calls[0]["PayGradeHigh"] == "15"
    assert "JobGrade" not in calls[0]
    # second call: JobGrade-based, semicolon-joined executive codes, no PayGrade band
    assert calls[1]["JobGrade"] == ";".join(usajobs._EXECUTIVE_GRADE_CODES)
    assert "PayGradeLow" not in calls[1]

    assert {j["job_id"] for j in jobs} == {"P1", "EXEC1"}


def test_search_jobs_by_series_and_grade_executive_grades_dedupes_overlap(monkeypatch, configured):
    # A posting returned by BOTH the GS-band query and the JobGrade query
    # (real observed case: some AD/ZP/SL/FP-graded postings already carry
    # an equivalent GS 12-15 classification) must only appear once.
    def fake_get(url, headers=None, params=None, timeout=None):
        if "JobGrade" in params:
            return _FakeResponse(_search_result([_item(position_id="P1"), _item(position_id="EXEC1")]))
        return _FakeResponse(_search_result([_item(position_id="P1")]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs_by_series_and_grade(
        ["2210"], "12", "15", include_executive_grades=True,
    )

    assert sorted(j["job_id"] for j in jobs) == ["EXEC1", "P1"]


def test_search_jobs_unaffected_by_new_function_existing(monkeypatch, configured):
    # search_jobs() itself must remain unchanged/callable - the new function
    # is additive, not a replacement (task's explicit requirement).
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_search_result([_item()]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs(keyword="CIO")
    assert len(jobs) == 1


# --- Real bug (2026-08-17): USAJOBS' own JobCategoryCode server-side
# filter is looser than expected - real production evidence returned
# "Cook" (Bureau of Indian Education), "Staff Accountant" (Army National
# Guard), and "Social Worker" (Air National Guard) from a
# JobCategoryCode=2210 request. USAJOBS' real behavior doesn't reproduce
# the bug on every call (a live re-check 2026-08-17 came back clean), so
# these synthetic MatchedObjectDescriptors with a mismatched JobCategory
# are what actually exercises the fix deterministically, per the task's
# explicit instruction to cover that case.

def test_search_jobs_drops_job_whose_actual_category_does_not_match_requested_code(monkeypatch, configured):
    off_domain_job = _item(
        position_id="BAD1",
        title="Cook",
        job_category=[{"Name": "Food Preparation And Cooking", "Code": "7404"}],
    )
    matching_job = _item(position_id="GOOD1", title="IT Specialist")

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_search_result([off_domain_job, matching_job]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs(job_category_code="2210")

    assert [j["job_id"] for j in jobs] == ["GOOD1"]


def test_search_jobs_drops_job_with_no_job_category_at_all_when_code_requested(monkeypatch, configured):
    no_category_job = _item(position_id="BAD2", job_category=[])

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_search_result([no_category_job]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs(job_category_code="2210")

    assert jobs == []


def test_search_jobs_matches_any_of_several_semicolon_joined_requested_codes(monkeypatch, configured):
    job = _item(job_category=[{"Name": "Accounting", "Code": "0510"}])

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_search_result([job]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs(job_category_code="2210;0510;1550")

    assert len(jobs) == 1


def test_search_jobs_keyword_only_search_is_unaffected_by_category_validation(monkeypatch, configured):
    # No job_category_code passed -> nothing to validate against, must
    # pass through unchanged even with no JobCategory field at all.
    job = _item(job_category=[])

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_search_result([job]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs(keyword="Director")

    assert len(jobs) == 1


def test_search_jobs_by_series_and_grade_drops_off_series_job(monkeypatch, configured):
    off_domain_job = _item(
        position_id="BAD1",
        title="Staff Accountant",
        job_category=[{"Name": "Accounting", "Code": "0510"}],
    )
    matching_job = _item(position_id="GOOD1")

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_search_result([off_domain_job, matching_job]))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    jobs = usajobs.search_jobs_by_series_and_grade(["2210"], "12", "15")

    assert [j["job_id"] for j in jobs] == ["GOOD1"]


# check_position_open() - rewritten 2026-08-17. The old implementation
# passed PositionID as a *search API* filter and was never actually tested
# against real responses; live-verification that day found the parameter
# was silently ignored (a real, a garbage, an empty, and an all-zeros
# PositionID all returned the same 5 generic results), so this function
# could never detect a real closure. These tests use response shapes
# confirmed live against the real Historic JOA endpoint
# (data.usajobs.gov/api/historicjoa) that day: a real open posting
# (PositionID "DH-13024454-26-VJ") returned positionOpeningStatus
# "Accepting applications"; a real closed posting already in Panga's own
# job store (PositionID "req806") returned positionOpeningStatus "Job
# closed"; a garbage/empty/all-zeros id returned HTTP 204 with an empty
# body.

def _historic_joa_record(position_opening_status="Accepting applications", position_close_date="2099-01-01"):
    return {
        "data": [
            {
                "usajobsControlNumber": 879297700,
                "positionOpeningStatus": position_opening_status,
                "positionCloseDate": position_close_date,
            }
        ]
    }


def test_check_position_open_true_for_accepting_applications(monkeypatch, configured):
    # Shape confirmed live 2026-08-17 for PositionID "DH-13024454-26-VJ".
    def fake_get(url, headers=None, params=None, timeout=None):
        assert url == usajobs.HISTORIC_JOA_URL
        assert params == {"AnnouncementNumbers": "DH-13024454-26-VJ"}
        return _FakeResponse(_historic_joa_record("Accepting applications", "2026-08-18"))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    assert usajobs.check_position_open("DH-13024454-26-VJ") is True


def test_check_position_open_false_for_job_closed(monkeypatch, configured):
    # Shape confirmed live 2026-08-17 for PositionID "req806" - a real
    # closed posting already sitting in Panga's own job store.
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_historic_joa_record("Job closed", "2026-01-23"))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    assert usajobs.check_position_open("req806") is False


@pytest.mark.parametrize(
    "status",
    ["Reviewing applications", "Hiring complete", "Job canceled"],
)
def test_check_position_open_false_for_other_non_accepting_statuses(monkeypatch, configured, status):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_historic_joa_record(status, "2026-01-01"))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    assert usajobs.check_position_open("SOME-ID") is False


def test_check_position_open_raises_not_found_on_204(monkeypatch, configured):
    # Confirmed live 2026-08-17: a garbage id, an all-zeros id, and an
    # empty string each return HTTP 204 with an empty body - correctly
    # distinguishable from both open and closed, unlike the old (broken)
    # search-API-based implementation which returned the same fake "found"
    # result regardless of what was passed.
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(None, status_code=204, text="")

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    with pytest.raises(usajobs.USAJobsPositionNotFound):
        usajobs.check_position_open("GARBAGE-NOT-REAL")


def test_check_position_open_raises_not_found_on_empty_data_list(monkeypatch, configured):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse({"data": []}, status_code=200, text='{"data": []}')

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    with pytest.raises(usajobs.USAJobsPositionNotFound):
        usajobs.check_position_open("0000000000")


def test_check_position_open_uses_close_date_fallback_when_status_blank(monkeypatch, configured):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(_historic_joa_record("", "2099-01-01"))

    monkeypatch.setattr(usajobs.requests, "get", fake_get)
    assert usajobs.check_position_open("SOME-ID") is True


def test_check_position_open_regression_old_broken_behavior_would_have_returned_true_for_garbage():
    """Documents the actual bug being fixed: the OLD implementation queried
    API_URL (the search endpoint) with PositionID as a filter param, which
    USAJOBS silently ignores - confirmed live 2026-08-17 that a garbage
    PositionID returned the same non-empty SearchResultItems as a real one.
    The new implementation queries a different endpoint
    (HISTORIC_JOA_URL) entirely, which does not have this problem (see the
    204-on-not-found tests above)."""
    assert usajobs.HISTORIC_JOA_URL != usajobs.API_URL
