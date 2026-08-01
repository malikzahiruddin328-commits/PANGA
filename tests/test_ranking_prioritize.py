from ranking.prioritize import dedupe_across_sources, weight_for


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
