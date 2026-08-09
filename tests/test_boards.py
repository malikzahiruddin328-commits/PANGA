import search.boards as boards
from search.boards import normalize_dice_job, normalize_indeed_jobs, normalize_ziprecruiter_job


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# Card with a company logo (name wrapped in <a>) - the common case.
_CARD_WITH_LOGO = """
<div data-testid="job-card" data-job-guid="guid-with-logo">
  <a data-testid="job-search-job-detail-link" href="/job-detail/guid-with-logo">Chief Information Officer</a>
  <a href="/company-profile/x"><p data-testid="job-card-company-name">Acme Corp</p></a>
  <p>Ann Arbor, Michigan<!-- -->&#8226;<!-- -->Today</p>
  <div aria-labelledby="salary-label"><p>USD 190,000.00 - 240,000.00 per year</p></div>
</div>
"""

# Card with no logo (name <p> is a direct sibling of location, not wrapped
# in <a>) - the real "Conexus" shape that crashed the first parser version.
_CARD_WITHOUT_LOGO = """
<div data-testid="job-card" data-job-guid="guid-without-logo">
  <a data-testid="job-search-job-detail-link" href="/job-detail/guid-without-logo">CIO</a>
  <p data-testid="job-card-company-name">Conexus</p>
  <p>Irvine, California<!-- -->&#8226;<!-- -->Today</p>
  <div aria-labelledby="salary-label"><p>USD 350,000.00 per year</p></div>
</div>
"""

# Card with no salary block at all.
_CARD_NO_SALARY = """
<div data-testid="job-card" data-job-guid="guid-no-salary">
  <a data-testid="job-search-job-detail-link" href="/job-detail/guid-no-salary">Deputy CIO</a>
  <p data-testid="job-card-company-name">State of Kansas</p>
  <p>No location provided</p>
</div>
"""

# Card with no title link at all - should be skipped entirely.
_CARD_NO_TITLE_LINK = """
<div data-testid="job-card" data-job-guid="guid-orphan">
  <p data-testid="job-card-company-name">Ghost Inc</p>
</div>
"""


def _search_page(*cards):
    return "<html><body>" + "".join(cards) + "</body></html>"


def test_normalize_dice_job_id_stable_despite_different_guid():
    # Real bug found 2026-08-06 in production data: the exact same real
    # posting showed up under a different "guid" on repeat searches (Dice's
    # own MCP response field, despite looking like a stable database id) -
    # job_id must be derived from the stable content fields, not the guid.
    raw1 = {"guid": "guid-one", "title": "CIO", "companyName": "Acme Corp", "jobLocation": {"displayName": "Remote"}}
    raw2 = {"guid": "guid-two", "title": "CIO", "companyName": "Acme Corp", "jobLocation": {"displayName": "Remote"}}
    assert normalize_dice_job(raw1)["job_id"] == normalize_dice_job(raw2)["job_id"]


def test_normalize_ziprecruiter_job_id_stable_despite_different_redirect_url():
    # Same bug, ZipRecruiter's side: match_token is a per-request signed
    # token, regenerated every search even for the same real posting.
    raw1 = {"job_redirect_url": "https://ziprecruiter.com/job-redirect?match_token=AAA", "title": "CIO", "company": "Acme", "location": "Remote"}
    raw2 = {"job_redirect_url": "https://ziprecruiter.com/job-redirect?match_token=BBB", "title": "CIO", "company": "Acme", "location": "Remote"}
    assert normalize_ziprecruiter_job(raw1)["job_id"] == normalize_ziprecruiter_job(raw2)["job_id"]


def test_normalize_indeed_jobs_id_stable_despite_different_redirect_url():
    # Same bug, Indeed's side: the "View Job URL" redirect is reissued on
    # repeat searches for the same real posting.
    def make_text(url):
        return (
            "**Job Title:** Chief Information Officer\n"
            "**Company:** Acme Corp\n"
            "**Location:** Remote\n"
            "**Compensation:** N/A\n"
            f"**View Job URL:** {url}\n"
        )
    job1 = normalize_indeed_jobs(make_text("https://to.indeed.com/aaa"))[0]
    job2 = normalize_indeed_jobs(make_text("https://to.indeed.com/bbb"))[0]
    assert job1["job_id"] == job2["job_id"]
    # posting_url still reflects the real (if unstable) redirect, so the
    # user can still click through to today's live link.
    assert job1["posting_url"] == "https://to.indeed.com/aaa"
    assert job2["posting_url"] == "https://to.indeed.com/bbb"


def test_stable_job_id_differs_for_genuinely_different_postings():
    raw_a = {"guid": "g1", "title": "CIO", "companyName": "Acme Corp", "jobLocation": {"displayName": "Remote"}}
    raw_b = {"guid": "g2", "title": "VP Engineering", "companyName": "Acme Corp", "jobLocation": {"displayName": "Remote"}}
    assert normalize_dice_job(raw_a)["job_id"] != normalize_dice_job(raw_b)["job_id"]


def test_fetch_dice_jobs_parses_card_with_company_logo(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_search_page(_CARD_WITH_LOGO)))
    jobs = boards.fetch_dice_jobs("Chief Information Officer")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "Dice"
    assert job["title"] == "Chief Information Officer"
    assert job["organization"] == "Acme Corp"
    assert job["location"] == "Ann Arbor, Michigan"
    assert job["pay_min"] == "190000"
    assert job["pay_max"] == "240000"
    assert job["posting_url"] == "https://www.dice.com/job-detail/guid-with-logo"


def test_fetch_dice_jobs_parses_card_without_company_logo(monkeypatch):
    # Real bug found 2026-08-06: a company without a logo has its name <p>
    # as a direct sibling of the location <p>, not wrapped in an <a> like
    # the common case - this crashed the first version of the parser.
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_search_page(_CARD_WITHOUT_LOGO)))
    jobs = boards.fetch_dice_jobs("CIO")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["organization"] == "Conexus"
    assert job["location"] == "Irvine, California"
    assert job["pay_min"] == "350000"
    assert job["pay_max"] == "350000"


def test_fetch_dice_jobs_handles_missing_salary(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_search_page(_CARD_NO_SALARY)))
    jobs = boards.fetch_dice_jobs("Deputy CIO")
    assert jobs[0]["pay_min"] is None
    assert jobs[0]["pay_max"] is None
    assert jobs[0]["location"] == "No location provided"


def test_fetch_dice_jobs_skips_cards_without_a_title_link(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_search_page(_CARD_NO_TITLE_LINK, _CARD_WITH_LOGO)))
    jobs = boards.fetch_dice_jobs("CIO")
    assert len(jobs) == 1
    assert jobs[0]["organization"] == "Acme Corp"


def test_fetch_dice_jobs_respects_limit(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_search_page(_CARD_WITH_LOGO, _CARD_WITHOUT_LOGO, _CARD_NO_SALARY)))
    jobs = boards.fetch_dice_jobs("CIO", limit=2)
    assert len(jobs) == 2


def test_fetch_dice_jobs_id_stable_across_repeat_fetches_of_same_posting(monkeypatch):
    # Direct-scrape guid is empirically stable (verified live) - unlike
    # normalize_dice_job()'s MCP-path guid, no _stable_job_id() substitution
    # was needed at the guid level, but the two calls must still agree.
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_search_page(_CARD_WITH_LOGO)))
    job1 = boards.fetch_dice_jobs("CIO")[0]
    job2 = boards.fetch_dice_jobs("CIO")[0]
    assert job1["job_id"] == job2["job_id"]


def test_fetch_dice_jobs_id_matches_mcp_path_for_same_content():
    # The whole point of unifying both Dice code paths onto _stable_job_id():
    # the same real posting found via either path must dedupe against
    # itself under source="Dice", regardless of which path found it first.
    scraped = boards._stable_job_id("Dice", "Chief Information Officer", "Acme Corp", "Ann Arbor, Michigan")
    mcp_normalized = normalize_dice_job({
        "guid": "totally-different-mcp-guid", "title": "Chief Information Officer",
        "companyName": "Acme Corp", "jobLocation": {"displayName": "Ann Arbor, Michigan"},
    })
    assert scraped == mcp_normalized["job_id"]


def test_normalize_dice_job_captures_summary_as_description():
    # Real bug found 2026-08-06: Dice's own search response already
    # includes a real JD excerpt in "summary" - it was being discarded,
    # leaving ATS keyword extraction with nothing to work from.
    raw = {
        "guid": "abc-123",
        "title": "Chief Information Officer",
        "companyName": "Acme Corp",
        "jobLocation": {"displayName": "Remote"},
        "salary": "$190,000 - $240,000",
        "summary": "About the role: leadership of the IT department...",
        "detailsPageUrl": "https://www.dice.com/job-detail/abc-123",
    }
    job = normalize_dice_job(raw)
    assert job["description"] == "About the role: leadership of the IT department..."


def test_normalize_dice_job_handles_missing_summary():
    raw = {"guid": "abc-123", "title": "CIO", "companyName": "Acme Corp"}
    job = normalize_dice_job(raw)
    assert job["description"] is None


def test_normalize_ziprecruiter_job_has_no_description_field():
    # ZipRecruiter's raw response genuinely contains no JD text at all
    # (live-confirmed 2026-08-06) - this documents that as intentional,
    # not an oversight to "fix" later.
    raw = {"job_redirect_url": "https://ziprecruiter.com/x", "title": "CIO", "company": "Acme"}
    job = normalize_ziprecruiter_job(raw)
    assert "description" not in job


def test_normalize_indeed_jobs_has_no_description_field():
    # Indeed's markdown search response genuinely contains no JD text
    # either (live-confirmed 2026-08-06), and its posting_url domain is
    # separately confirmed WAF-blocked for a live fetch (freshness_check.py)
    # - this parser has no way to get JD text, still true and still correct
    # here. NOT the same as "Indeed is JD-less" though: the scheduled
    # task's own SKILL.md (2026-08-07) now calls Indeed's separate
    # get_job_details MCP tool for newly-saved jobs and writes the result
    # via job_store.update_job_description() directly - a different code
    # path outside this module, since it needs a live MCP call this pure
    # text-normalization function can't make.
    raw_text = (
        "**Job Title:** Chief Information Officer\n"
        "**Company:** Acme Corp\n"
        "**Location:** Remote\n"
        "**Compensation:** N/A\n"
        "**View Job URL:** https://to.indeed.com/xyz\n"
    )
    jobs = normalize_indeed_jobs(raw_text)
    assert len(jobs) == 1
    assert "description" not in jobs[0]


# --- Built In fixtures (real markup shape, live-confirmed 2026-08-07) ---

_BUILT_IN_CARD_WITH_SALARY = """
<div data-id="job-card" id="job-card-1">
  <div class="left-side-tile-item-2">
    <a data-id="company-title" href="/company/acme"><span>Acme Corp</span></a>
  </div>
  <h2><a data-id="job-card-title" data-alias="/job/chief-information-officer/9053354" href="/job/x">Chief Information Officer</a></h2>
  <div class="bounded-attribute-section">
    <span class="font-barlow text-gray-04">Hybrid</span>
    <span class="font-barlow text-gray-04">Chicago, IL, USA</span>
    <span class="font-barlow text-gray-04">275K-310K Annually</span>
    <span class="font-barlow text-gray-04">Expert/Leader</span>
  </div>
</div>
"""

# Real shape found live: salary omitted entirely (not a placeholder) when
# Built In has no salary data for a posting - one fewer span, not blank.
_BUILT_IN_CARD_NO_SALARY = """
<div data-id="job-card" id="job-card-2">
  <div class="left-side-tile-item-2">
    <a data-id="company-title" href="/company/jpmc"><span>JPMorganChase</span></a>
  </div>
  <h2><a data-id="job-card-title" data-alias="/job/senior-engineer/10310265" href="/job/y">Senior Engineer</a></h2>
  <div class="bounded-attribute-section">
    <span class="font-barlow text-gray-04">Hybrid</span>
    <span class="font-barlow text-gray-04">Jersey City, NJ, USA</span>
    <span class="font-barlow text-gray-04">Senior level</span>
  </div>
</div>
"""


def _built_in_page(*cards):
    return "<html><body>" + "".join(cards) + "</body></html>"


def test_fetch_built_in_jobs_parses_card_with_salary(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_built_in_page(_BUILT_IN_CARD_WITH_SALARY)))
    jobs = boards.fetch_built_in_jobs("Chief Information Officer")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "Built In"
    assert job["title"] == "Chief Information Officer"
    assert job["organization"] == "Acme Corp"
    assert job["location"] == "Chicago, IL, USA"
    assert job["pay_min"] == "275000"
    assert job["pay_max"] == "310000"
    assert job["job_id"] == boards._stable_job_id("Built In", "Chief Information Officer", "Acme Corp", "Chicago, IL, USA")
    assert job["posting_url"] == "https://builtin.com/job/chief-information-officer/9053354"


def test_fetch_built_in_jobs_handles_missing_salary(monkeypatch):
    # Real shape found live: a card missing salary data has one fewer span
    # entirely, not an empty placeholder - this crashed a fixed-index-based
    # first draft of the parser during development.
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_built_in_page(_BUILT_IN_CARD_NO_SALARY)))
    jobs = boards.fetch_built_in_jobs("Engineer")
    assert jobs[0]["pay_min"] is None
    assert jobs[0]["pay_max"] is None
    assert jobs[0]["location"] == "Jersey City, NJ, USA"


def test_fetch_built_in_jobs_id_is_content_based_not_the_raw_href(monkeypatch):
    # Real bug found 2026-08-08 (Mirror's audit): this function originally
    # used data-alias/href directly as job_id, on the assumption it was a
    # stable permalink slug - unlike its sibling fetch_simplyhired_jobs(),
    # shipped in the same commit, which got the _stable_job_id() fix and a
    # disclosure explaining why. This one didn't, and it wasn't a
    # deliberate exception - just missed. Confirmed live in production
    # data: a real duplicate already existed ("Audit Project Manager - CIO"
    # at US Bank under two different /job/.../<id> hrefs) - Built In's own
    # "Reposted" labeling suggests a repost can get a genuinely new
    # numeric ID, same failure mode as Indeed/ZipRecruiter/Dice's MCP path
    # and SimplyHired's href token.
    same_posting_different_href = """
    <div data-id="job-card" id="job-card-3">
      <div class="left-side-tile-item-2">
        <a data-id="company-title" href="/company/acme"><span>Acme Corp</span></a>
      </div>
      <h2><a data-id="job-card-title" data-alias="/job/chief-information-officer/99999999" href="/job/z">Chief Information Officer</a></h2>
      <div class="bounded-attribute-section">
        <span class="font-barlow text-gray-04">Hybrid</span>
        <span class="font-barlow text-gray-04">Chicago, IL, USA</span>
      </div>
    </div>
    """
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_built_in_page(_BUILT_IN_CARD_WITH_SALARY)))
    job1 = boards.fetch_built_in_jobs("CIO")[0]
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(same_posting_different_href))
    job2 = boards.fetch_built_in_jobs("CIO")[0]
    assert job1["job_id"] == job2["job_id"]
    assert job1["posting_url"] != job2["posting_url"]  # links can differ, id must not


def test_fetch_built_in_jobs_respects_limit(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_built_in_page(_BUILT_IN_CARD_WITH_SALARY, _BUILT_IN_CARD_NO_SALARY)))
    jobs = boards.fetch_built_in_jobs("CIO", limit=1)
    assert len(jobs) == 1


# --- SimplyHired fixtures (real markup shape, live-confirmed 2026-08-07) ---

_SIMPLYHIRED_CARD = """
<div data-testid="searchSerpJob">
  <h2 data-testid="searchSerpJobTitle"><a href="/job/tokenAAA">IT Director</a></h2>
  <span data-testid="companyName">Confidential</span>
  <span data-testid="searchSerpJobLocation">Boca Raton, FL</span>
  <span data-testid="salaryChip-0">$120,000 - $140,000 a year</span>
  <span data-testid="searchSerpJobDateStamp">2d</span>
</div>
"""

_SIMPLYHIRED_CARD_NO_SALARY = """
<div data-testid="searchSerpJob">
  <h2 data-testid="searchSerpJobTitle"><a href="/job/tokenBBB">Business Unit CIO</a></h2>
  <span data-testid="companyName">Sysco</span>
  <span data-testid="searchSerpJobLocation">Houston, TX</span>
</div>
"""


def _simplyhired_page(*cards):
    return "<html><body>" + "".join(cards) + "</body></html>"


def test_fetch_simplyhired_jobs_parses_real_response_shape(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_simplyhired_page(_SIMPLYHIRED_CARD)))
    jobs = boards.fetch_simplyhired_jobs("Chief Information Officer")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "SimplyHired"
    assert job["title"] == "IT Director"
    assert job["organization"] == "Confidential"
    assert job["location"] == "Boca Raton, FL"
    assert job["pay_min"] == "120000"
    assert job["pay_max"] == "140000"
    assert job["posting_url"] == "https://www.simplyhired.com/job/tokenAAA"


def test_fetch_simplyhired_jobs_handles_missing_salary(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_simplyhired_page(_SIMPLYHIRED_CARD_NO_SALARY)))
    jobs = boards.fetch_simplyhired_jobs("CIO")
    assert jobs[0]["pay_min"] is None
    assert jobs[0]["pay_max"] is None


def test_fetch_simplyhired_jobs_id_is_content_based_not_the_url_token(monkeypatch):
    # Real bug found 2026-08-07: the /job/<token> href is NOT stable across
    # repeat fetches for the same real posting (live-tested: 7 of 8 stayed
    # the same, 1 changed) - job_id must be content-based, same fix already
    # applied to Indeed/ZipRecruiter/Dice's MCP path.
    same_posting_different_token = """
    <div data-testid="searchSerpJob">
      <h2 data-testid="searchSerpJobTitle"><a href="/job/tokenZZZ-different">IT Director</a></h2>
      <span data-testid="companyName">Confidential</span>
      <span data-testid="searchSerpJobLocation">Boca Raton, FL</span>
    </div>
    """
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_simplyhired_page(_SIMPLYHIRED_CARD)))
    job1 = boards.fetch_simplyhired_jobs("IT Director")[0]
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(same_posting_different_token))
    job2 = boards.fetch_simplyhired_jobs("IT Director")[0]
    assert job1["job_id"] == job2["job_id"]
    assert job1["posting_url"] != job2["posting_url"]  # links can differ, id must not


def test_fetch_simplyhired_jobs_respects_limit(monkeypatch):
    monkeypatch.setattr(boards.requests, "get", lambda url, params=None, headers=None, timeout=None: _FakeResponse(_simplyhired_page(_SIMPLYHIRED_CARD, _SIMPLYHIRED_CARD_NO_SALARY)))
    jobs = boards.fetch_simplyhired_jobs("CIO", limit=1)
    assert len(jobs) == 1
