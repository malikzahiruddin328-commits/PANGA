import search.industry_boards as industry_boards


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _biospace_page(*items):
    body = "".join(items)
    return f"""
    <html><body><ul>{body}</ul></body></html>
    """


def _biospace_item(title, location, organization, href):
    return f"""
    <li class="lister__item">
      <h3 class="lister__header"><a href="{href}"><span>{title}</span></a></h3>
      <ul class="lister__meta">
        <li class="lister__meta-item--location">{location}</li>
        <li class="lister__meta-item--recruiter">{organization}</li>
      </ul>
    </li>
    """


_IT_ITEM = _biospace_item(
    "Sr Information Technology Operations Analyst", "Boston, MA", "Genentech", "/job/1234",
)
_NOISE_ITEM = _biospace_item(
    "Medical Representative (MR) - Dermatology", "Remote", "AbbVie", "/job/5678",
)


def test_fetch_biospace_jobs_sends_keywords_param_by_default(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_biospace_page(_IT_ITEM))

    monkeypatch.setattr(industry_boards.requests, "get", fake_get)
    industry_boards.fetch_biospace_jobs(limit=5)

    assert captured["params"]["Keywords"] == "information technology"


def test_fetch_biospace_jobs_custom_keywords_override_default(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_biospace_page(_IT_ITEM))

    monkeypatch.setattr(industry_boards.requests, "get", fake_get)
    industry_boards.fetch_biospace_jobs(limit=5, keywords="cybersecurity")

    assert captured["params"]["Keywords"] == "cybersecurity"


def test_fetch_biospace_jobs_empty_keywords_restores_unfiltered_behavior(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_biospace_page(_NOISE_ITEM))

    monkeypatch.setattr(industry_boards.requests, "get", fake_get)
    industry_boards.fetch_biospace_jobs(limit=5, keywords="")

    assert "Keywords" not in captured["params"]


def test_fetch_biospace_jobs_parses_filtered_results(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(_biospace_page(_IT_ITEM))

    monkeypatch.setattr(industry_boards.requests, "get", fake_get)
    jobs = industry_boards.fetch_biospace_jobs(limit=1)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Sr Information Technology Operations Analyst"
    assert jobs[0]["organization"] == "Genentech"
    assert jobs[0]["source"] == "BioSpace"
