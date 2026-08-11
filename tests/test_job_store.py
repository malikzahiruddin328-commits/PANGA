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


def test_add_manual_job_blank_url_falls_back_to_content_hash_not_empty_string_hash(isolated_data):
    # Real gap found 2026-08-08: hashing "" always produces the same
    # job_id, so two genuinely different blank-URL listings would collide
    # and the second would be silently dropped as a "duplicate" by
    # save_jobs()'s (source, job_id) dedup.
    job_a = job_store.add_manual_job(title="CIO", organization="Acme Corp", location="Remote", description="d1", posting_url="", source="lensa")
    job_b = job_store.add_manual_job(title="VP IT", organization="Beta Inc", location="NYC", description="d2", posting_url="", source="lensa")
    assert job_a["job_id"] != job_b["job_id"]
    assert len(job_store.load_jobs()) == 2


def test_add_manual_job_blank_url_same_content_still_dedupes(isolated_data):
    # Two blank-URL listings with genuinely identical title/organization/
    # description ARE meant to collide - that's a real duplicate, not the
    # bug above.
    job_a = job_store.add_manual_job(title="CIO", organization="Acme Corp", location="Remote", description="d1", posting_url="", source="lensa")
    job_b = job_store.add_manual_job(title="CIO", organization="Acme Corp", location="Remote", description="d1", posting_url="", source="lensa")
    assert job_a["job_id"] == job_b["job_id"]
    assert len(job_store.load_jobs()) == 1


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


def test_update_job_ats_keywords_stamps_extractor_version_when_given(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    job_store.update_job_ats_keywords("Dice", "1", ["python"], [], extractor_version=3)
    job = job_store.load_jobs()[0]
    assert job["ats_keywords_extractor_version"] == 3


def test_update_job_ats_keywords_extractor_version_stays_backward_compatible(isolated_data):
    # A caller that doesn't know about extractor_version (existing tests
    # above, any future caller) must not be forced to supply one or have
    # it default to something misleading.
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}])
    job_store.update_job_ats_keywords("Dice", "1", ["python"], [])
    job = job_store.load_jobs()[0]
    assert "ats_keywords_extractor_version" not in job


def test_update_job_description_sets_description(isolated_data):
    job_store.save_jobs([{"source": "Eisai", "job_id": "1", "title": "Director"}])
    job_store.update_job_description("Eisai", "1", "Real JD text.")
    job = job_store.load_jobs()[0]
    assert job["description"] == "Real JD text."


def test_update_job_description_clears_stale_empty_ats_keyword_cache(isolated_data):
    # Real bug this guards against: if a job was cached with empty keyword
    # lists BEFORE it had any real JD text, drafting.py's
    # _extract_ats_keywords() would treat that empty cache as "already
    # tried, genuinely nothing there" forever - even after real text is
    # backfilled - since it only re-attempts when the keys are absent, not
    # merely empty. Backfilling description must reset that cache so the
    # next regenerate re-extracts for real.
    job_store.save_jobs([{"source": "Eisai", "job_id": "1", "title": "Director"}])
    job_store.update_job_ats_keywords("Eisai", "1", [], [])
    job_store.update_job_description("Eisai", "1", "Real JD text.")
    job = job_store.load_jobs()[0]
    assert job["description"] == "Real JD text."
    assert "ats_required_keywords" not in job
    assert "ats_preferred_keywords" not in job


def test_update_job_description_no_op_when_job_not_found(isolated_data):
    job_store.save_jobs([{"source": "Eisai", "job_id": "1", "title": "Director"}])
    job_store.update_job_description("Eisai", "does-not-exist", "text")
    job = job_store.load_jobs()[0]
    assert "description" not in job


def test_flag_employer_attribution_uncertain_sets_fields(isolated_data):
    job_store.save_jobs([{"source": "Indeed", "job_id": "1", "title": "Director", "organization": "Suncoast Credit Union"}])
    job_store.flag_employer_attribution_uncertain("Indeed", "1", likely_organization="Celldex")
    job = job_store.load_jobs()[0]
    assert job["employer_attribution_uncertain"] is True
    assert job["likely_organization"] == "Celldex"
    # organization itself is left alone - flagged, not auto-corrected, so
    # Zahir judges for himself same as everywhere else in this app.
    assert job["organization"] == "Suncoast Credit Union"


def test_flag_employer_attribution_uncertain_without_a_guess(isolated_data):
    job_store.save_jobs([{"source": "Indeed", "job_id": "1", "title": "Director"}])
    job_store.flag_employer_attribution_uncertain("Indeed", "1")
    job = job_store.load_jobs()[0]
    assert job["employer_attribution_uncertain"] is True
    assert job["likely_organization"] is None
