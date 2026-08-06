from search.boards import normalize_dice_job, normalize_indeed_jobs, normalize_ziprecruiter_job


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
