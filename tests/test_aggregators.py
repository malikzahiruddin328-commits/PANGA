import pytest

import search.aggregators as aggregators


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
def isolated_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(aggregators, "BUDGET_PATH", tmp_path / "adzuna_call_budget.yaml")
    return tmp_path


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "test-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-key")


def test_is_configured_false_without_credentials(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    assert aggregators.is_configured() is False


def test_is_configured_true_with_both_credentials(configured):
    assert aggregators.is_configured() is True


def test_fetch_raises_not_configured_without_credentials(monkeypatch, isolated_budget):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    with pytest.raises(aggregators.AdzunaNotConfigured):
        aggregators.fetch_adzuna_jobs("engineer", "gb")


def test_fetch_raises_value_error_for_invalid_country(configured, isolated_budget):
    with pytest.raises(ValueError):
        aggregators.fetch_adzuna_jobs("engineer", "zz")


def test_not_configured_check_happens_before_budget_is_spent(monkeypatch, isolated_budget):
    # A user without credentials shouldn't burn budget on every failed call -
    # the NotConfigured check must happen before _reserve_call().
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    with pytest.raises(aggregators.AdzunaNotConfigured):
        aggregators.fetch_adzuna_jobs("engineer", "gb")
    assert aggregators.remaining_calls_today() == aggregators.DEFAULT_DAILY_CALL_LIMIT


def test_fetch_parses_real_response_shape(configured, isolated_budget, monkeypatch):
    monkeypatch.setattr(aggregators.requests, "get", lambda url, params=None, timeout=None: _FakeResponse({
        "results": [{
            "id": 12345,
            "title": "Chief Information Officer",
            "company": {"display_name": "Acme Corp"},
            "location": {"display_name": "London"},
            "salary_min": 150000,
            "salary_max": 200000,
            "redirect_url": "https://www.adzuna.co.uk/details/12345",
        }],
    }))
    jobs = aggregators.fetch_adzuna_jobs("Chief Information Officer", "gb")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "Adzuna"
    assert job["job_id"] == "12345"
    assert job["title"] == "Chief Information Officer"
    assert job["organization"] == "Acme Corp"
    assert job["location"] == "London"
    assert job["pay_min"] == 150000
    assert job["pay_max"] == 200000
    assert job["posting_url"] == "https://www.adzuna.co.uk/details/12345"
    assert job["apply_url"] == "https://www.adzuna.co.uk/details/12345"


def test_fetch_handles_missing_company_and_location(configured, isolated_budget, monkeypatch):
    monkeypatch.setattr(aggregators.requests, "get", lambda url, params=None, timeout=None: _FakeResponse({
        "results": [{"id": 1, "title": "Engineer", "redirect_url": "https://x"}],
    }))
    jobs = aggregators.fetch_adzuna_jobs("engineer", "us")
    assert jobs[0]["organization"] is None
    assert jobs[0]["location"] is None


def test_fetch_country_is_case_and_whitespace_insensitive(configured, isolated_budget, monkeypatch):
    monkeypatch.setattr(aggregators.requests, "get", lambda url, params=None, timeout=None: _FakeResponse({"results": []}))
    aggregators.fetch_adzuna_jobs("engineer", " GB ")  # should not raise


def test_reserve_call_increments_and_blocks_at_limit(isolated_budget, monkeypatch):
    monkeypatch.setenv("ADZUNA_DAILY_CALL_LIMIT", "3")
    assert aggregators.remaining_calls_today() == 3
    for expected_remaining in (2, 1, 0):
        aggregators._reserve_call()
        assert aggregators.remaining_calls_today() == expected_remaining
    with pytest.raises(aggregators.AdzunaBudgetExceeded):
        aggregators._reserve_call()


def test_budget_resets_on_a_new_day(isolated_budget, monkeypatch):
    monkeypatch.setenv("ADZUNA_DAILY_CALL_LIMIT", "1")
    aggregators._reserve_call()
    assert aggregators.remaining_calls_today() == 0

    state = aggregators._load_budget_state()
    state["date"] = "2000-01-01"
    aggregators._save_budget_state(state)

    assert aggregators.remaining_calls_today() == 1


def test_fetch_raises_budget_exceeded_once_limit_reached(configured, isolated_budget, monkeypatch):
    monkeypatch.setenv("ADZUNA_DAILY_CALL_LIMIT", "1")
    monkeypatch.setattr(aggregators.requests, "get", lambda url, params=None, timeout=None: _FakeResponse({"results": []}))
    aggregators.fetch_adzuna_jobs("engineer", "us")  # uses the one call
    with pytest.raises(aggregators.AdzunaBudgetExceeded):
        aggregators.fetch_adzuna_jobs("engineer", "us")
