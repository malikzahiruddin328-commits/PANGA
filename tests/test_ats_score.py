from tailoring.ats_score import extract_keywords, score_resume_against_keywords, score_resume_ats

POSTING = (
    "Minimum Qualifications: Proficiency with Python, SQL, and AWS. "
    "Experience with data pipelines. "
    "Desired Qualifications: Familiarity with Terraform and Kubernetes."
)


def test_extract_keywords_splits_required_and_preferred():
    keywords = extract_keywords(POSTING)
    assert keywords.get("python") is True
    assert keywords.get("sql") is True
    assert keywords.get("aws") is True
    assert keywords.get("terraform") is False
    assert keywords.get("kubernetes") is False


def test_extract_keywords_handles_no_recognizable_sections():
    # No "Requirements"/"Preferred" markers anywhere - falls back to a
    # single required-weighted pool so postings with unusual formatting
    # still produce some real signal instead of an empty keyword set.
    keywords = extract_keywords("We use Python and PostgreSQL every day here.")
    assert keywords.get("python") is True


def test_extract_keywords_empty_on_blank_posting():
    assert extract_keywords("") == {}
    assert extract_keywords(None) == {}


def test_score_moves_when_resume_gains_a_required_keyword():
    resume_without = (
        "JANE DOE\njane@example.com\n\nPROFESSIONAL EXPERIENCE\n"
        "Senior Engineer - Acme - Jan 2020 - Present\n- Built things.\n\n"
        "EDUCATION\nBS Computer Science\n\nSKILLS\nJava"
    )
    resume_with = resume_without + ", Python, SQL, AWS"

    without_score = score_resume_ats(POSTING, resume_without)
    with_score = score_resume_ats(POSTING, resume_with)

    assert with_score["ats_score"] > without_score["ats_score"]


def test_score_is_deterministic_for_the_same_inputs():
    resume = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython, SQL, AWS"
    first = score_resume_ats(POSTING, resume)
    second = score_resume_ats(POSTING, resume)
    assert first == second


def test_score_falls_back_to_structure_only_when_posting_has_no_extractable_text():
    resume = (
        "jane@example.com\n\nPROFESSIONAL EXPERIENCE\nEngineer - Acme - Jan 2020 - Present\n"
        "- Did things.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
    )
    result = score_resume_ats("", resume)
    assert "structure" in result["ats_rationale"].lower() or "formatting" in result["ats_rationale"].lower()
    assert 0 <= result["ats_score"] <= 100


def test_missing_required_keywords_returned_separately_from_next_actions():
    # 2026-08-06: missing REQUIRED keywords used to show up as static
    # "How to raise it" bullet text with no way to answer/act on them.
    # drafting.py now turns these into real, answerable clarifying
    # questions instead (_merge_keyword_gap_questions) - so this function
    # must expose the raw list separately, and ats_next_actions must NOT
    # duplicate them as inert text.
    resume = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava"
    result = score_resume_ats(POSTING, resume)
    assert set(result["missing_required_keywords"]) >= {"python", "sql", "aws"}
    joined = " ".join(result["ats_next_actions"]).lower()
    assert "python" not in joined and "sql" not in joined and "aws" not in joined


def test_missing_required_keywords_empty_when_all_matched():
    result = score_resume_against_keywords(["python", "sql"], [], "SKILLS\nPython, SQL")
    assert result["missing_required_keywords"] == []


def test_next_actions_does_not_falsely_claim_resume_is_complete_when_required_keywords_still_missing():
    # Real edge case: structure checks all pass and there are no missing
    # PREFERRED keywords, but required keywords are still missing (now
    # living only in clarifying_questions, not ats_next_actions) - must
    # not fall back to "Resume already covers... well", which would be a
    # false claim while real gaps still exist.
    resume = (
        "jane@example.com\n\nPROFESSIONAL EXPERIENCE\nEngineer - Acme - Jan 2020 - Present\n"
        "- Did things.\n\nEDUCATION\nBS\n\nSKILLS\nJava"
    )
    result = score_resume_against_keywords(["python", "sql"], [], resume)
    assert result["missing_required_keywords"] == ["python", "sql"]
    assert "already covers" not in " ".join(result["ats_next_actions"]).lower()


def test_score_against_explicit_keywords_moves_when_resume_gains_one():
    # The primary path - drafting.py passes an AI-extracted keyword list
    # straight to this function instead of routing through the local regex
    # heuristic, so a clean explicit list should behave identically to the
    # heuristic path: more real matches -> higher score.
    resume_without = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava"
    resume_with = resume_without + ", Python, Kubernetes"

    without_score = score_resume_against_keywords(["python", "sql"], ["kubernetes"], resume_without)
    with_score = score_resume_against_keywords(["python", "sql"], ["kubernetes"], resume_with)

    assert with_score["ats_score"] > without_score["ats_score"]


def test_score_against_explicit_keywords_is_case_insensitive():
    result = score_resume_against_keywords(["Python", "SQL"], [], "SKILLS\npython, sql")
    assert result["ats_score"] > 50


def test_score_against_empty_keyword_lists_falls_back_to_structure_only():
    resume = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
    result = score_resume_against_keywords([], [], resume)
    assert "structure" in result["ats_rationale"].lower() or "formatting" in result["ats_rationale"].lower()


def test_next_actions_flag_missing_structural_elements():
    resume_no_headers_no_contact = "Just some plain text with no sections at all."
    result = score_resume_ats(POSTING, resume_no_headers_no_contact)
    joined = " ".join(result["ats_next_actions"]).lower()
    assert "header" in joined or "contact" in joined


def test_bachelors_degree_requirement_matches_bsc_on_resume():
    # Real bug Zahir hit live 2026-08-07: posting requires "bachelor's
    # degree", his actual resume says "Brunel University, BSc" - pure
    # literal matching marked this missing_required_keywords even though
    # it's plainly present, and that false-missing flowed straight into a
    # real, user-facing "do you have a bachelor's degree?" question.
    result = score_resume_against_keywords(
        ["bachelor's degree"], [], "EDUCATION\nBachelor of Science, Information Systems\nBrunel University London, BSc\n",
    )
    assert result["missing_required_keywords"] == []


def test_information_technology_requirement_matches_it_abbreviation():
    result = score_resume_against_keywords(
        ["information technology"], [], "PROFESSIONAL EXPERIENCE\nHead of IT\nJanuary 2024 - Present\n",
    )
    assert result["missing_required_keywords"] == []


def test_computer_science_requirement_matches_cs_abbreviation():
    result = score_resume_against_keywords(
        ["computer science"], [], "EDUCATION\nBSc, CS, Brunel University\n",
    )
    assert result["missing_required_keywords"] == []


def test_masters_degree_requirement_matches_mba():
    result = score_resume_against_keywords(
        ["master's degree"], [], "EDUCATION\nMBA, Wharton\n",
    )
    assert result["missing_required_keywords"] == []


def test_doctorate_requirement_matches_phd():
    result = score_resume_against_keywords(
        ["doctorate"], [], "EDUCATION\nPhD, Computer Science\n",
    )
    assert result["missing_required_keywords"] == []


def test_equivalence_is_symmetric_resume_uses_full_form_posting_uses_abbreviation():
    # A posting that asks for "BSc" should also count a resume that spells
    # out "Bachelor of Science" - same equivalence class, either direction.
    result = score_resume_against_keywords(
        ["bsc"], [], "EDUCATION\nBachelor of Science, Information Systems\n",
    )
    assert result["missing_required_keywords"] == []


def test_alias_matching_does_not_match_unrelated_terms():
    # "cs" must not falsely match "customer service" or similar unrelated
    # text just because the alias group exists.
    result = score_resume_against_keywords(
        ["computer science"], [], "PROFESSIONAL EXPERIENCE\nCustomer Service Representative\n",
    )
    assert result["missing_required_keywords"] == ["computer science"]


def test_lowercase_it_pronoun_in_prose_does_not_falsely_satisfy_information_technology():
    # Real gap found 2026-08-07 (General's review, before this shipped):
    # "it" is one of the most common words in English. Case-insensitive
    # alias matching would have let ANY resume containing an ordinary
    # sentence using the pronoun "it" silently satisfy "information
    # technology" - hiding a real gap instead of surfacing it, the
    # opposite (and worse, silent) failure mode from the bug this
    # equivalence table exists to fix. A candidate with zero IT background
    # must still show "information technology" as missing.
    resume = (
        "PROFESSIONAL EXPERIENCE\nSales Director - Acme - Jan 2020 - Present\n"
        "- Delivered it on time and managed it end-to-end for every major account.\n"
        "- Owned it from kickoff to close, presenting it to the board each quarter.\n"
    )
    result = score_resume_against_keywords(["information technology"], [], resume)
    assert result["missing_required_keywords"] == ["information technology"]


def test_lowercase_cs_style_token_in_prose_does_not_falsely_satisfy_computer_science():
    resume = "PROFESSIONAL EXPERIENCE\nSales rep vs cs team on pricing disputes.\n"
    result = score_resume_against_keywords(["computer science"], [], resume)
    assert result["missing_required_keywords"] == ["computer science"]


def test_uppercase_it_acronym_still_satisfies_information_technology():
    # The case-sensitivity fix must not overcorrect into never matching -
    # a real capitalized "IT" acronym should still count.
    result = score_resume_against_keywords(
        ["information technology"], [], "PROFESSIONAL EXPERIENCE\nHead of IT\n",
    )
    assert result["missing_required_keywords"] == []


def test_multi_word_aliases_stay_case_insensitive():
    # Multi-word aliases ("bachelor's degree", "information technology")
    # are unambiguous - no common English phrase collides with them - so
    # they should still match regardless of casing, unlike the short
    # acronym aliases above.
    result = score_resume_against_keywords(
        ["bsc"], [], "education\nbachelor of science, information systems\n",
    )
    assert result["missing_required_keywords"] == []


def test_extract_keywords_fallback_path_also_benefits_from_equivalence():
    # General's explicit ask: score_resume_ats()'s no-AI extract_keywords()
    # fallback funnels into the same score_resume_against_keywords() /
    # _phrase_in_text() call, so it must get the same fix, not just the
    # AI-extracted-keyword-list path.
    posting = "Minimum Qualifications: Bachelor's degree required. Experience with Information Technology systems."
    resume = "EDUCATION\nBSc, Brunel University\n\nPROFESSIONAL EXPERIENCE\nHead of IT\n"
    result = score_resume_ats(posting, resume)
    assert "bachelor's degree" not in result["missing_required_keywords"]
    assert "information technology" not in result["missing_required_keywords"]
