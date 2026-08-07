import search.company_sites as company_sites


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_html_to_text_strips_tags():
    assert company_sites._html_to_text("<p>Hello <b>world</b></p>") == "Hello\nworld"


def test_html_to_text_handles_none_and_empty():
    assert company_sites._html_to_text(None) is None
    assert company_sites._html_to_text("") is None


def test_fetch_workday_job_description_extracts_real_text(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, timeout=None: _FakeResponse({"jobPostingInfo": {"jobDescription": "<p>Lead the team.</p>"}}),
    )
    result = company_sites._fetch_workday_job_description("eisai", "eisai", 5, "/job/x")
    assert result == "Lead the team."


def test_fetch_workday_job_description_returns_none_on_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(company_sites.requests, "get", _raise)
    assert company_sites._fetch_workday_job_description("eisai", "eisai", 5, "/job/x") is None


def test_fetch_smartrecruiters_job_description_combines_sections(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, timeout=None: _FakeResponse({
            "jobAd": {"sections": {
                "jobDescription": {"text": "<p>Lead the team.</p>"},
                "qualifications": {"text": "<p>10+ years experience.</p>"},
            }},
        }),
    )
    result = company_sites._fetch_smartrecruiters_job_description("abbvie", "123")
    assert "Lead the team." in result
    assert "10+ years experience." in result


def test_fetch_smartrecruiters_job_description_returns_none_on_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(company_sites.requests, "get", _raise)
    assert company_sites._fetch_smartrecruiters_job_description("abbvie", "123") is None


def test_search_workday_jobs_includes_description(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "post",
        lambda url, json=None, timeout=None: _FakeResponse({
            "jobPostings": [{"externalPath": "/job/x", "title": "Director", "locationsText": "Remote"}],
        }),
    )
    monkeypatch.setattr(
        company_sites, "_fetch_workday_job_description",
        lambda tenant, site, wd_number, external_path: "Real JD text.",
    )
    jobs = company_sites.search_workday_jobs("Eisai", "eisai", "eisai", 5)
    assert jobs[0]["description"] == "Real JD text."


def test_search_smartrecruiters_jobs_includes_description(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, params=None, timeout=None: _FakeResponse({
            "content": [{"id": "123", "name": "Director", "location": {"city": "Chicago"}}],
        }),
    )
    monkeypatch.setattr(
        company_sites, "_fetch_smartrecruiters_job_description",
        lambda company_id, posting_id: "Real JD text.",
    )
    jobs = company_sites.search_smartrecruiters_jobs("AbbVie", "abbvie")
    assert jobs[0]["description"] == "Real JD text."


def test_search_greenhouse_jobs_parses_real_response_shape(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, timeout=None: _FakeResponse({
            "jobs": [{
                "id": 8023928, "title": "Account Executive, Bridge",
                "location": {"name": "London"},
                "absolute_url": "https://stripe.com/jobs/search?gh_jid=8023928",
            }],
        }),
    )
    jobs = company_sites.search_greenhouse_jobs("Stripe", "stripe")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "Stripe"
    assert job["job_id"] == "8023928"
    assert job["title"] == "Account Executive, Bridge"
    assert job["organization"] == "Stripe"
    assert job["location"] == "London"
    assert job["posting_url"] == "https://stripe.com/jobs/search?gh_jid=8023928"
    assert job["apply_url"] == job["posting_url"]


def test_search_greenhouse_jobs_respects_limit(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, timeout=None: _FakeResponse({"jobs": [{"id": i, "title": "Role"} for i in range(5)]}),
    )
    jobs = company_sites.search_greenhouse_jobs("Stripe", "stripe", limit=2)
    assert len(jobs) == 2


def test_search_greenhouse_jobs_handles_missing_location(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, timeout=None: _FakeResponse({"jobs": [{"id": 1, "title": "Role", "absolute_url": "https://x"}]}),
    )
    jobs = company_sites.search_greenhouse_jobs("Stripe", "stripe")
    assert jobs[0]["location"] is None


def test_search_lever_jobs_parses_real_response_shape(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, params=None, timeout=None: _FakeResponse([{
            "id": "abc-123", "text": "Administrative Business Partner",
            "categories": {"location": "London, United Kingdom"},
            "hostedUrl": "https://jobs.lever.co/palantir/abc-123",
            "applyUrl": "https://jobs.lever.co/palantir/abc-123/apply",
        }]),
    )
    jobs = company_sites.search_lever_jobs("Palantir", "palantir")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "Palantir"
    assert job["job_id"] == "abc-123"
    assert job["title"] == "Administrative Business Partner"
    assert job["organization"] == "Palantir"
    assert job["location"] == "London, United Kingdom"
    assert job["posting_url"] == "https://jobs.lever.co/palantir/abc-123"
    assert job["apply_url"] == "https://jobs.lever.co/palantir/abc-123/apply"


def test_search_lever_jobs_falls_back_to_hosted_url_when_no_apply_url(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, params=None, timeout=None: _FakeResponse([{
            "id": "abc-123", "text": "Role", "hostedUrl": "https://jobs.lever.co/x/abc-123",
        }]),
    )
    jobs = company_sites.search_lever_jobs("X", "x")
    assert jobs[0]["apply_url"] == "https://jobs.lever.co/x/abc-123"


def test_search_lever_jobs_respects_limit(monkeypatch):
    monkeypatch.setattr(
        company_sites.requests, "get",
        lambda url, params=None, timeout=None: _FakeResponse([{"id": str(i), "text": "Role"} for i in range(5)]),
    )
    jobs = company_sites.search_lever_jobs("X", "x", limit=2)
    assert len(jobs) == 2


def test_check_greenhouse_posting_open_true_for_real_posting(monkeypatch):
    monkeypatch.setattr(company_sites.requests, "get", lambda url, timeout=None: _FakeResponse({"id": 1}, status_code=200))
    assert company_sites.check_greenhouse_posting_open("stripe", "1") is True


def test_check_greenhouse_posting_open_false_for_removed_posting(monkeypatch):
    monkeypatch.setattr(company_sites.requests, "get", lambda url, timeout=None: _FakeResponse({}, status_code=404))
    assert company_sites.check_greenhouse_posting_open("stripe", "999") is False


def test_check_lever_posting_open_true_for_real_posting(monkeypatch):
    monkeypatch.setattr(company_sites.requests, "get", lambda url, params=None, timeout=None: _FakeResponse({"id": "1"}, status_code=200))
    assert company_sites.check_lever_posting_open("palantir", "1") is True


def test_check_lever_posting_open_false_for_removed_posting(monkeypatch):
    monkeypatch.setattr(company_sites.requests, "get", lambda url, params=None, timeout=None: _FakeResponse({}, status_code=404))
    assert company_sites.check_lever_posting_open("palantir", "not-a-real-id") is False
