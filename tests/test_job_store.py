import search.exclusion_filter as exclusion_filter
import search.job_store as job_store
import tailoring.applications as applications
from security.crypto_store import read_json, write_json


def test_save_jobs_dedupes_by_source_and_job_id(isolated_data):
    added_first = job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    added_second = job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director (repost)"}])
    assert added_first == 1
    assert added_second == 0
    jobs = job_store.load_jobs()
    assert len(jobs) == 1
    # Existing record is left untouched by the duplicate save, not updated.
    assert jobs[0]["title"] == "Director"


def test_save_jobs_stamps_date_added_on_new_records_only(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
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
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    job_store.update_job_score("Dice", "1", 85, "Strong match")
    job = job_store.load_jobs()[0]
    assert job["fit_score"] == 85
    assert job["fit_rationale"] == "Strong match"


def test_update_job_address_caches_empty_string_as_a_real_value(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    job_store.update_job_address("Dice", "1", "")
    job = job_store.load_jobs()[0]
    # "" means "searched, not found" - distinct from the key being absent
    # entirely ("never searched"). Both must be preserved distinctly.
    assert "organization_address" in job
    assert job["organization_address"] == ""


def test_update_job_ats_keywords_sets_both_lists_on_matching_job(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    job_store.update_job_ats_keywords("Dice", "1", ["python", "sql"], ["aws"])
    job = job_store.load_jobs()[0]
    assert job["ats_required_keywords"] == ["python", "sql"]
    assert job["ats_preferred_keywords"] == ["aws"]


def test_update_job_ats_keywords_caches_empty_lists_as_a_real_value(isolated_data):
    # Empty lists mean "extracted, genuinely no such keywords" - distinct
    # from the keys being absent entirely ("never extracted").
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    job_store.update_job_ats_keywords("Dice", "1", [], [])
    job = job_store.load_jobs()[0]
    assert job["ats_required_keywords"] == []
    assert job["ats_preferred_keywords"] == []


def test_update_job_ats_keywords_stamps_extractor_version_when_given(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    job_store.update_job_ats_keywords("Dice", "1", ["python"], [], extractor_version=3)
    job = job_store.load_jobs()[0]
    assert job["ats_keywords_extractor_version"] == 3


def test_update_job_ats_keywords_extractor_version_stays_backward_compatible(isolated_data):
    # A caller that doesn't know about extractor_version (existing tests
    # above, any future caller) must not be forced to supply one or have
    # it default to something misleading.
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
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


def test_flag_freshness_check_downgraded_sets_fields_on_every_target(isolated_data):
    job_store.save_jobs([
        {"source": "Eisai", "job_id": "1", "title": "Director"},
        {"source": "Adzuna", "job_id": "a1", "title": "Director"},
        {"source": "IQVIA", "job_id": "2", "title": "Director"},  # not a target - left alone
    ])
    flagged = job_store.flag_freshness_check_downgraded(
        [("Eisai", "1"), ("Adzuna", "a1")], reason="\"Eisai\" was removed from job-board sources",
    )
    assert flagged == 2
    by_key = {(j["source"], j["job_id"]): j for j in job_store.load_jobs()}
    assert by_key[("Eisai", "1")]["freshness_check_downgraded"] is True
    assert by_key[("Eisai", "1")]["freshness_check_downgrade_reason"] == "\"Eisai\" was removed from job-board sources"
    assert by_key[("Adzuna", "a1")]["freshness_check_downgraded"] is True
    assert "freshness_check_downgraded" not in by_key[("IQVIA", "2")]


def test_flag_freshness_check_downgraded_skips_targets_that_do_not_exist(isolated_data):
    job_store.save_jobs([{"source": "Eisai", "job_id": "1", "title": "Director"}])
    flagged = job_store.flag_freshness_check_downgraded([("Eisai", "1"), ("Eisai", "does-not-exist")], reason="test")
    assert flagged == 1


def test_flag_freshness_check_downgraded_empty_targets_is_a_no_op(isolated_data):
    job_store.save_jobs([{"source": "Eisai", "job_id": "1", "title": "Director"}])
    flagged = job_store.flag_freshness_check_downgraded([], reason="test")
    assert flagged == 0
    assert "freshness_check_downgraded" not in job_store.load_jobs()[0]


# --- Review gate (2026-08-13 basket/review build) ---
#
# Every save_jobs() call below also passes apply_exclusion=False (added
# post-merge with the 2026-08-12 search-time exclusion filter, master):
# these tests are about the review-gate/basket mechanics specifically, not
# the exclusion filter, and bare titles like "Engineer"/"Manager" trip
# exclusion_filter's IC-tier rule (no executive qualifier) merged in from
# master - without this override save_jobs() would silently add 0 jobs and
# every assertion below would IndexError on an empty store.

def test_save_jobs_stamps_pending_review_status_by_default(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}], apply_exclusion=False)
    job = job_store.load_jobs()[0]
    assert job["review_status"] == "pending"


def test_save_jobs_review_required_false_stamps_accepted(isolated_data):
    job_store.save_jobs(
        [{"source": "Dice", "job_id": "1", "title": "Engineer"}],
        apply_exclusion=False, review_required=False,
    )
    job = job_store.load_jobs()[0]
    assert job["review_status"] == "accepted"


def test_save_jobs_does_not_overwrite_review_status_on_existing_record(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}], apply_exclusion=False)
    job_store.set_review_status("Dice", "1", "accepted")
    # A re-announced posting from a later search run must not silently
    # reset an already-reviewed job back to "pending" - save_jobs() only
    # ever touches genuinely new records.
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer (repost)"}], apply_exclusion=False)
    job = job_store.load_jobs()[0]
    assert job["review_status"] == "accepted"


def test_add_manual_job_skips_the_review_gate(isolated_data):
    job = job_store.add_manual_job(
        title="Head of IT", organization="Aerospike", location="Remote",
        description="d", posting_url="https://example.com/careers/1", source="company_site",
    )
    assert job["review_status"] == "accepted"


def test_set_review_status_updates_matching_job(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}], apply_exclusion=False)
    job_store.set_review_status("Dice", "1", "rejected")
    assert job_store.load_jobs()[0]["review_status"] == "rejected"


def test_set_review_status_is_a_no_op_for_unknown_job(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}], apply_exclusion=False)
    job_store.set_review_status("Dice", "does-not-exist", "accepted")
    assert job_store.load_jobs()[0]["review_status"] == "pending"


# --- Basket (2026-08-13 build) ---
# Same apply_exclusion=False note as the review-gate block above applies here.

def test_add_to_basket_sets_in_basket_true(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}], apply_exclusion=False)
    job_store.add_to_basket("Dice", "1")
    assert job_store.load_jobs()[0]["in_basket"] is True


def test_remove_from_basket_deletes_the_key_entirely(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}], apply_exclusion=False)
    job_store.add_to_basket("Dice", "1")
    job_store.remove_from_basket("Dice", "1")
    assert "in_basket" not in job_store.load_jobs()[0]


def test_basket_membership_persists_independently_per_job(isolated_data):
    job_store.save_jobs([
        {"source": "Dice", "job_id": "1", "title": "Engineer"},
        {"source": "Dice", "job_id": "2", "title": "Manager"},
    ], apply_exclusion=False)
    job_store.add_to_basket("Dice", "1")
    jobs = {j["job_id"]: j for j in job_store.load_jobs()}
    assert jobs["1"].get("in_basket") is True
    assert "in_basket" not in jobs["2"]


def test_remove_from_basket_is_a_no_op_for_job_never_added(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Engineer"}], apply_exclusion=False)
    job_store.remove_from_basket("Dice", "1")  # should not raise
    assert "in_basket" not in job_store.load_jobs()[0]


# --- Search-time exclusion filter wiring (2026-08-12) -----------------------

def test_save_jobs_excludes_ic_tier_job_from_the_store(isolated_data):
    added = job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Sr. Systems Engineer"}])
    assert added == 0
    assert job_store.load_jobs() == []


def test_save_jobs_excludes_clinical_job_from_the_store(isolated_data):
    added = job_store.save_jobs([{
        "source": "Indeed", "job_id": "1",
        "title": "Senior Medical Director, Hematology Clinical Development",
    }])
    assert added == 0
    assert job_store.load_jobs() == []


def test_save_jobs_logs_every_excluded_job(isolated_data):
    job_store.save_jobs([{
        "source": "Indeed", "job_id": "1", "title": "Sr. Systems Engineer",
        "organization": "AbbVie", "location": "Remote",
    }])
    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert len(entries) == 1
    assert entries[0]["source"] == "Indeed"
    assert entries[0]["job_id"] == "1"
    assert entries[0]["title"] == "Sr. Systems Engineer"
    assert entries[0]["organization"] == "AbbVie"
    assert entries[0]["location"] == "Remote"
    assert "seniority_mismatch" in entries[0]["exclusion_reason"]


def test_save_jobs_still_saves_a_plausible_executive_job_normally(isolated_data):
    added = job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director, IT Service Continuity"}])
    assert added == 1
    jobs = job_store.load_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Director, IT Service Continuity"
    assert jobs[0]["date_added"]


def test_save_jobs_mixed_batch_keeps_only_the_plausible_ones(isolated_data):
    added = job_store.save_jobs([
        {"source": "Dice", "job_id": "1", "title": "Sr. Systems Engineer"},
        {"source": "Dice", "job_id": "2", "title": "Director, IT Service Continuity"},
        {"source": "Dice", "job_id": "3", "title": "Registered Nurse"},
    ])
    assert added == 1
    jobs = job_store.load_jobs()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "2"
    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert {e["job_id"] for e in entries} == {"1", "3"}


def test_save_jobs_apply_exclusion_false_bypasses_the_filter(isolated_data):
    # Used by add_manual_job() below - Zahir's own manual paste UI and
    # job_alert_scan.py's email-digest extraction are both explicitly
    # exempt from search-time exclusion (see save_jobs()'s own docstring).
    added = job_store.save_jobs(
        [{"source": "linkedin", "job_id": "1", "title": "Sr. Systems Engineer"}],
        apply_exclusion=False,
    )
    assert added == 1
    assert len(job_store.load_jobs()) == 1
    assert read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[]) == []


def test_add_manual_job_is_never_excluded(isolated_data):
    # Real regression guard: add_manual_job() feeds both Zahir's own
    # manual-paste UI and job_alert_scan.py's email-digest intake, which has
    # a standing, explicit "add every listing found" rule (CLAUDE.md,
    # "Processing job-alert emails into job records") that predates and
    # must not be broken by this filter.
    job = job_store.add_manual_job(
        title="Sr. Systems Engineer", organization="Acme", location="Remote",
        description="d", posting_url="https://example.com/jobs/1",
    )
    jobs = job_store.load_jobs()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job["job_id"]
    assert read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[]) == []


def _seed_application(source, job_id, status, resume_text=""):
    applications.upsert_application(source, job_id, status=status, resume_text=resume_text)


def test_archive_stale_jobs_dry_run_does_not_write(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director"}])
    job_store.set_review_status("Dice", "1", "rejected")
    result = job_store.archive_stale_jobs(dry_run=True)
    assert result["candidate_count"] == 1
    assert len(job_store.load_jobs()) == 1
    assert job_store.load_archived_jobs() == []


def test_archive_stale_jobs_moves_rejected_jobs(isolated_data):
    job_store.save_jobs([
        {"source": "Dice", "job_id": "1", "title": "Rejected One"},
        {"source": "Dice", "job_id": "2", "title": "Still Live"},
    ])
    job_store.set_review_status("Dice", "1", "rejected")
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 1
    remaining = job_store.load_jobs()
    assert len(remaining) == 1
    assert remaining[0]["job_id"] == "2"
    archived = job_store.load_archived_jobs()
    assert len(archived) == 1
    assert archived[0]["job_id"] == "1"


def test_archive_stale_jobs_moves_not_interested_applications(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Passed"}])
    job_store.set_review_status("Dice", "1", "accepted")
    _seed_application("Dice", "1", status="not interested")
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 1
    assert job_store.load_jobs() == []


def test_archive_stale_jobs_moves_low_fit_score_regardless_of_age(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Poor Fit"}])
    job_store.set_review_status("Dice", "1", "accepted")
    job_store.update_job_score("Dice", "1", 32, "Not a fit")
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 1
    assert job_store.load_jobs() == []


def test_archive_stale_jobs_never_touches_unscored_jobs(isolated_data):
    # Missing fit_score ("never scored") must never be conflated with
    # "scored below 60" - only a real numeric score under 60 qualifies.
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Unscored"}])
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 0
    assert len(job_store.load_jobs()) == 1


def test_archive_stale_jobs_protects_in_basket(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "In Basket"}])
    job_store.update_job_score("Dice", "1", 10, "Low score")
    job_store.add_to_basket("Dice", "1")
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 0
    assert len(job_store.load_jobs()) == 1


def test_archive_stale_jobs_protects_pending_review(isolated_data):
    # save_jobs() stamps review_status="pending" by default.
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Awaiting review"}])
    job_store.update_job_score("Dice", "1", 10, "Low score")
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 0
    assert len(job_store.load_jobs()) == 1


def test_archive_stale_jobs_protects_real_engagement_over_low_score(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Applied Anyway"}])
    job_store.set_review_status("Dice", "1", "accepted")
    job_store.update_job_score("Dice", "1", 20, "Low score")
    _seed_application("Dice", "1", status="applied")
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 0
    assert len(job_store.load_jobs()) == 1


def test_archive_stale_jobs_protects_a_real_generated_resume(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Has Resume"}])
    job_store.set_review_status("Dice", "1", "accepted")
    job_store.update_job_score("Dice", "1", 20, "Low score")
    _seed_application("Dice", "1", status="not interested", resume_text="Real drafted resume text.")
    result = job_store.archive_stale_jobs(dry_run=False)
    assert result["candidate_count"] == 0
    assert len(job_store.load_jobs()) == 1


def test_archive_stale_jobs_never_deletes_appends_to_archive_across_runs(isolated_data):
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "First"}])
    job_store.set_review_status("Dice", "1", "rejected")
    job_store.archive_stale_jobs(dry_run=False)

    job_store.save_jobs([{"source": "Dice", "job_id": "2", "title": "Second"}])
    job_store.set_review_status("Dice", "2", "rejected")
    job_store.archive_stale_jobs(dry_run=False)

    archived = job_store.load_archived_jobs()
    assert {j["job_id"] for j in archived} == {"1", "2"}


# --- Archive dedup (2026-08-13, save_jobs() checking jobs-archive.json too) -

def test_save_jobs_skips_job_matching_archived_source_and_job_id(isolated_data):
    write_json(job_store.ARCHIVE_PATH, [
        {"source": "Dice", "job_id": "1", "title": "Rejected Long Ago", "review_status": "rejected"},
    ])
    added = job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Rejected Long Ago (repost)"}])
    assert added == 0
    # Never resurrected into the live store at all - not added-then-hidden.
    assert job_store.load_jobs() == []


def test_save_jobs_still_adds_a_genuinely_new_job_alongside_an_archived_one(isolated_data):
    write_json(job_store.ARCHIVE_PATH, [
        {"source": "Dice", "job_id": "1", "title": "Archived One"},
    ])
    added = job_store.save_jobs([
        {"source": "Dice", "job_id": "1", "title": "Archived One (repost)"},
        {"source": "Dice", "job_id": "2", "title": "Director, Genuinely New"},
    ])
    assert added == 1
    jobs = job_store.load_jobs()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "2"


def test_save_jobs_logs_archive_dedup_skip(isolated_data):
    write_json(job_store.ARCHIVE_PATH, [
        {"source": "Indeed", "job_id": "99", "title": "Director of IT", "organization": "AbbVie", "location": "Remote"},
    ])
    job_store.save_jobs([{
        "source": "Indeed", "job_id": "99", "title": "Director of IT",
        "organization": "AbbVie", "location": "Remote",
    }])
    entries = read_json(job_store.ARCHIVE_DEDUP_LOG_PATH, default=[])
    assert len(entries) == 1
    assert entries[0]["source"] == "Indeed"
    assert entries[0]["job_id"] == "99"
    assert entries[0]["title"] == "Director of IT"
    assert "skip_reason" in entries[0] and entries[0]["skip_reason"]


def test_save_jobs_archive_dedup_skip_log_does_not_grow_unboundedly_across_runs(isolated_data):
    # Same reasoning as exclusion_filter.log_exclusions()'s own test: a
    # still-open archived posting keeps surfacing in every search run, so
    # the log must de-dupe against what's already logged, not grow by one
    # entry per run forever.
    write_json(job_store.ARCHIVE_PATH, [{"source": "Dice", "job_id": "1", "title": "Archived"}])
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Archived"}])
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Archived"}])
    entries = read_json(job_store.ARCHIVE_DEDUP_LOG_PATH, default=[])
    assert len(entries) == 1


def test_save_jobs_no_archive_skip_log_written_when_nothing_matches(isolated_data):
    write_json(job_store.ARCHIVE_PATH, [{"source": "Dice", "job_id": "999", "title": "Unrelated"}])
    job_store.save_jobs([{"source": "Dice", "job_id": "1", "title": "Director, Genuinely New"}])
    assert read_json(job_store.ARCHIVE_DEDUP_LOG_PATH, default=[]) == []


def test_save_jobs_apply_exclusion_false_bypasses_archive_dedup_too(isolated_data):
    # add_manual_job() below relies on this: Zahir's own manual paste UI
    # (and job_alert_scan.py's email-digest extraction) must be able to
    # re-add a specific posting even if it matches something archived -
    # same reasoning as the exclusion-filter bypass, a considered explicit
    # add should never be silently refused.
    write_json(job_store.ARCHIVE_PATH, [{"source": "linkedin", "job_id": "1", "title": "Archived"}])
    added = job_store.save_jobs(
        [{"source": "linkedin", "job_id": "1", "title": "Archived (Zahir re-adding on purpose)"}],
        apply_exclusion=False,
    )
    assert added == 1
    assert len(job_store.load_jobs()) == 1
    assert read_json(job_store.ARCHIVE_DEDUP_LOG_PATH, default=[]) == []


def test_add_manual_job_can_re_add_a_job_that_matches_the_archive(isolated_data):
    write_json(job_store.ARCHIVE_PATH, [{"source": "linkedin", "job_id": "4123456789", "title": "Old"}])
    job = job_store.add_manual_job(
        title="Head of IT", organization="Aerospike", location="Remote",
        description="Reconsidering this one.", posting_url="https://www.linkedin.com/jobs/view/4123456789/",
    )
    assert job["job_id"] == "4123456789"
    assert len(job_store.load_jobs()) == 1


def test_save_jobs_loads_archive_once_per_batch_not_once_per_job(isolated_data, monkeypatch):
    # Performance sanity check (CLAUDE.md's perf-review rule, and this
    # fix's own brief): jobs-archive.json is 2,652+ real records and
    # growing - loading/scanning it once per incoming job in a batch would
    # be an O(n) file load per job instead of one load for the whole call.
    write_json(job_store.ARCHIVE_PATH, [{"source": "Dice", "job_id": "999", "title": "Archived"}])
    call_count = {"n": 0}
    real_load_archived_jobs = job_store.load_archived_jobs

    def counting_load_archived_jobs():
        call_count["n"] += 1
        return real_load_archived_jobs()

    monkeypatch.setattr(job_store, "load_archived_jobs", counting_load_archived_jobs)

    job_store.save_jobs([
        {"source": "Dice", "job_id": "1", "title": "Director One"},
        {"source": "Dice", "job_id": "2", "title": "Director Two"},
        {"source": "Dice", "job_id": "3", "title": "Director Three"},
        {"source": "Dice", "job_id": "999", "title": "Archived (repost)"},
    ])
    assert call_count["n"] == 1
