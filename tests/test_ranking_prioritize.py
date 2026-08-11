from ranking.prioritize import dedupe_across_sources, find_freshness_downgrade_targets, weight_for


def test_weight_for_matches_case_insensitively():
    roles = [{"name": "Head of IT", "priority_weight": 5}]
    assert weight_for("Head of IT - Aerospike", roles) == 5
    assert weight_for("HEAD OF IT", roles) == 5


def test_weight_for_no_match_returns_zero():
    roles = [{"name": "Head of IT", "priority_weight": 5}]
    assert weight_for("Software Engineer", roles) == 0


def test_weight_for_picks_highest_matching_weight():
    roles = [
        {"name": "IT", "priority_weight": 1},
        {"name": "Head of IT", "priority_weight": 9},
    ]
    assert weight_for("Head of IT", roles) == 9


def test_weight_for_empty_title():
    assert weight_for("", [{"name": "IT", "priority_weight": 5}]) == 0
    assert weight_for(None, [{"name": "IT", "priority_weight": 5}]) == 0


def test_dedupe_merges_board_posting_into_matching_company_site_posting():
    company_site = {"source": "Aerospike", "organization": "Aerospike", "title": "Head of IT"}
    board_posting = {"source": "ZipRecruiter", "organization": "Aerospike, Inc.", "title": "Head of IT"}
    result = dedupe_across_sources([company_site, board_posting])
    assert result == [company_site]
    assert company_site["_cross_source_duplicates"] == [board_posting]


def test_dedupe_does_not_merge_different_titles():
    company_site = {"source": "Aerospike", "organization": "Aerospike", "title": "Head of IT"}
    board_posting = {"source": "ZipRecruiter", "organization": "Aerospike", "title": "VP Engineering"}
    result = dedupe_across_sources([company_site, board_posting])
    assert result == [company_site, board_posting]


def test_dedupe_no_company_site_postings_returns_input_unchanged():
    jobs = [
        {"source": "ZipRecruiter", "organization": "Acme", "title": "Engineer"},
        {"source": "Dice", "organization": "Acme", "title": "Engineer"},
    ]
    assert dedupe_across_sources(jobs) == jobs


# --- find_freshness_downgrade_targets (2026-08-10, Zahir's product call
# on #14 from Mirror's audit) ---

def test_find_freshness_downgrade_targets_includes_the_removed_companys_own_postings():
    jobs = [{"source": "Eisai", "organization": "Eisai", "title": "Director", "job_id": "/job/1"}]
    targets = find_freshness_downgrade_targets(jobs, "Eisai")
    assert targets == [("Eisai", "/job/1")]


def test_find_freshness_downgrade_targets_includes_known_cross_source_duplicates():
    # Zahir's explicit scoping: not just the company's own postings - also
    # whatever dedupe_across_sources() would fold into them as the same
    # real opening, found via a different board.
    jobs = [
        {"source": "Eisai", "organization": "Eisai", "title": "Director", "job_id": "/job/1"},
        {"source": "Adzuna", "organization": "Eisai", "title": "Director", "job_id": "adzuna-1"},
    ]
    targets = find_freshness_downgrade_targets(jobs, "Eisai")
    assert set(targets) == {("Eisai", "/job/1"), ("Adzuna", "adzuna-1")}


def test_find_freshness_downgrade_targets_excludes_unrelated_jobs():
    # Not a blanket downgrade - a different company's postings, and a
    # different (non-matching) posting from the removed company's own
    # board, are both left alone.
    jobs = [
        {"source": "Eisai", "organization": "Eisai", "title": "Director", "job_id": "/job/1"},
        {"source": "IQVIA", "organization": "IQVIA", "title": "Director", "job_id": "/job/2"},
        {"source": "Adzuna", "organization": "Acme Corp", "title": "VP Engineering", "job_id": "adzuna-2"},
    ]
    targets = find_freshness_downgrade_targets(jobs, "Eisai")
    assert targets == [("Eisai", "/job/1")]


def test_find_freshness_downgrade_targets_empty_when_company_has_no_stored_jobs():
    jobs = [{"source": "IQVIA", "organization": "IQVIA", "title": "Director", "job_id": "/job/1"}]
    assert find_freshness_downgrade_targets(jobs, "Eisai") == []


def test_find_freshness_downgrade_targets_does_not_mutate_caller_list_identity():
    # dedupe_across_sources() mutates job dicts in place (attaches
    # _cross_source_duplicates) - this must operate on a copy of the list
    # so the caller's own list object isn't the one iterated internally,
    # even though the dict objects themselves are shared (same as every
    # other dedupe_across_sources() call site in this codebase).
    jobs = [{"source": "Eisai", "organization": "Eisai", "title": "Director", "job_id": "/job/1"}]
    original_list_id = id(jobs)
    find_freshness_downgrade_targets(jobs, "Eisai")
    assert id(jobs) == original_list_id
    assert len(jobs) == 1  # not appended/removed from in place
