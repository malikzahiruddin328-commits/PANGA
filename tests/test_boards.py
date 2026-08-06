from search.boards import normalize_dice_job, normalize_indeed_jobs, normalize_ziprecruiter_job


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
    # - this source is structurally JD-less, not a code gap.
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
