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


def test_next_actions_flag_missing_required_keywords():
    resume = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nJava"
    result = score_resume_ats(POSTING, resume)
    joined = " ".join(result["ats_next_actions"]).lower()
    assert "python" in joined or "sql" in joined or "aws" in joined


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
