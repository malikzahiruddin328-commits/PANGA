import pytest

import search.usajobs as usajobs


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

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
