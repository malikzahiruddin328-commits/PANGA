import search.job_store as job_store


def test_save_jobs_dedupes_by_source_and_job_id(isolated_data):
    added_first = job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    added_second = job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer (repost)"}])
    assert added_first == 1
    assert added_second == 0
    jobs = job_store.load_jobs()
    assert len(jobs) == 1
    # Existing record is left untouched by the duplicate save, not updated.
    assert jobs[0]["title"] == "Engineer"


def test_save_jobs_stamps_date_added_on_new_records_only(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    jobs = job_store.load_jobs()
    assert jobs[0]["date_added"]


def test_add_manual_job_derives_job_id_from_linkedin_url(isolated_data):
    job = job_store.add_manual_job(
        title="Head of IT", organization="Aerospike", location="Remote",
        description="...", posting_url="https://www.linkedin.com/jobs/view/4123456789/",
    )
    assert job["job_id"] == "4123456789"


def test_add_manual_job_dedupes_on_reposting_same_url(isolated_data):
    url = "https://www.linkedin.com/jobs/view/4123456789/?trk=abc"
    job_store.add_manual_job(title="Head of IT", organization="Aerospike", location="Remote", description="d", posting_url=url)
    # Same job ID embedded in the URL, different tracking query string.
    job_store.add_manual_job(title="Head of IT", organization="Aerospike", location="Remote", description="d2", posting_url=url + "&extra=1")
    assert len(job_store.load_jobs()) == 1


def test_add_manual_job_hashes_url_when_no_linkedin_id_present(isolated_data):
    job = job_store.add_manual_job(
        title="Engineer", organization="Acme", location="Remote",
        description="d", posting_url="https://example.com/careers/engineer", source="company_site",
    )
    assert len(job["job_id"]) == 16


def test_update_job_score_sets_fields_on_matching_job(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    job_store.update_job_score("Dice", "1", 85, "Strong match")
    job = job_store.load_jobs()[0]
    assert job["fit_score"] == 85
    assert job["fit_rationale"] == "Strong match"


def test_update_job_address_caches_empty_string_as_a_real_value(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    job_store.update_job_address("Dice", "1", "")
    job = job_store.load_jobs()[0]
    # "" means "searched, not found" - distinct from the key being absent
    # entirely ("never searched"). Both must be preserved distinctly.
    assert "organization_address" in job
    assert job["organization_address"] == ""


def test_update_job_ats_keywords_sets_both_lists_on_matching_job(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    job_store.update_job_ats_keywords("Dice", "1", ["python", "sql"], ["aws"])
    job = job_store.load_jobs()[0]
    assert job["ats_required_keywords"] == ["python", "sql"]
    assert job["ats_preferred_keywords"] == ["aws"]


def test_update_job_ats_keywords_caches_empty_lists_as_a_real_value(isolated_data):
    # Empty lists mean "extracted, genuinely no such keywords" - distinct
    # from the keys being absent entirely ("never extracted").
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    job_store.update_job_ats_keywords("Dice", "1", [], [])
    job = job_store.load_jobs()[0]
    assert job["ats_required_keywords"] == []
    assert job["ats_preferred_keywords"] == []
