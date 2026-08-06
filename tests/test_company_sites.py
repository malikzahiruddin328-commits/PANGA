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
