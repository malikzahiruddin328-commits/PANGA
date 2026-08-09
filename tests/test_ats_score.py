from tailoring.ats_score import (
    detect_matched_keyword_regressions,
    extract_keywords,
    plateau_note_for_gaps,
    score_resume_against_keywords,
    score_resume_ats,
)

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
    missing_labels = {m["label"] for m in result["missing_required_keywords"]}
    assert missing_labels >= {"python", "sql", "aws"}
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
    assert [m["label"] for m in result["missing_required_keywords"]] == ["python", "sql"]
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
    assert [m["label"] for m in result["missing_required_keywords"]] == ["computer science"]


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
    assert [m["label"] for m in result["missing_required_keywords"]] == ["information technology"]


def test_lowercase_cs_style_token_in_prose_does_not_falsely_satisfy_computer_science():
    resume = "PROFESSIONAL EXPERIENCE\nSales rep vs cs team on pricing disputes.\n"
    result = score_resume_against_keywords(["computer science"], [], resume)
    assert [m["label"] for m in result["missing_required_keywords"]] == ["computer science"]


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
    missing_labels = {m["label"] for m in result["missing_required_keywords"]}
    assert "bachelor's degree" not in missing_labels
    assert "information technology" not in missing_labels


def test_either_or_group_satisfied_by_one_alternative_is_not_a_missing_gap():
    # score-first-resume-flow spec item 2: a JD's "Master's degree, OR
    # Bachelor's degree plus 8+ years" must not flag "Master's degree" as
    # a flat, independently-required keyword when the candidate genuinely
    # satisfies the Bachelor's-side alternative instead.
    required = [{"any_of": ["Master's degree", "Bachelor's degree"]}, "Python"]
    resume = "EDUCATION\nBachelor of Science, Computer Science\n\nSKILLS\nPython\n"
    result = score_resume_against_keywords(required, [], resume)
    assert result["missing_required_keywords"] == []
    # Full keyword coverage - the remaining gap below 100 (if any) is
    # structural formatting, not this either/or group.
    assert result["ats_score"] >= 75


def test_either_or_group_unsatisfied_becomes_one_consolidated_missing_item():
    required = [{"any_of": ["Master's degree", "Bachelor's degree plus 8+ years experience"]}, "Python"]
    resume = "SKILLS\nPython\n"
    result = score_resume_against_keywords(required, [], resume)
    labels = [m["label"] for m in result["missing_required_keywords"]]
    assert labels == ["Master's degree OR Bachelor's degree plus 8+ years experience"]


def test_either_or_group_explanation_names_the_satisfying_alternative():
    required = [{"any_of": ["Master's degree", "Bachelor's degree"]}, "Python"]
    resume = "EDUCATION\nBachelor of Science\n\nSKILLS\nPython\n"
    result = score_resume_against_keywords(required, [], resume)
    assert "Bachelor's degree" in result["ats_rationale"]
    assert "satisfied via" in result["ats_rationale"]


def test_point_value_is_computed_from_the_real_scoring_formula_not_guessed():
    # UI contract: point_value must come from re-running the same formula
    # with one more hypothetical match, not a separately hand-derived
    # number that could drift.
    result = score_resume_against_keywords(["python", "sql", "aws", "kubernetes"], [], "SKILLS\nPython\n")
    missing = {m["label"]: m["point_value"] for m in result["missing_required_keywords"]}
    assert set(missing) == {"sql", "aws", "kubernetes"}
    # Each of the 3 missing required keywords should be worth the same
    # amount here (symmetric weighting, no preferred keywords, no
    # structural penalty differences between them).
    values = set(missing.values())
    assert len(values) == 1
    assert next(iter(values)) > 0


def test_required_point_value_outweighs_an_equivalent_preferred_one():
    # The scoring formula weights required keywords 3x preferred (0.75 vs
    # 0.25 split) - point values must reflect that real weighting, and
    # must match the actual observed score delta (computed by re-running
    # the same formula, not a separately hand-derived approximation).
    required, preferred = ["python", "sql"], ["docker", "kubernetes"]
    base_resume = "SKILLS\nJava\n"
    gains_required = "SKILLS\nJava, Python\n"
    gains_preferred = "SKILLS\nJava, Docker\n"

    base = score_resume_against_keywords(required, preferred, base_resume)
    with_required = score_resume_against_keywords(required, preferred, gains_required)["ats_score"]
    with_preferred = score_resume_against_keywords(required, preferred, gains_preferred)["ats_score"]

    required_gain = with_required - base["ats_score"]
    preferred_gain = with_preferred - base["ats_score"]
    assert required_gain > preferred_gain > 0

    # point_value keeps 1-decimal precision while the displayed ats_score
    # is rounded to a whole int, so these can differ by rounding alone -
    # not a bug, just two different precisions of the same real formula.
    python_point_value = next(m["point_value"] for m in base["missing_required_keywords"] if m["label"] == "python")
    assert abs(python_point_value - required_gain) <= 1


def test_plateau_note_is_none_when_nothing_notable_to_explain():
    result = score_resume_against_keywords(["python"], [], "SKILLS\nPython\n")
    assert result["plateau_note"] is None


def test_plateau_note_names_the_real_remaining_gap():
    result = score_resume_against_keywords(["python", "sql"], [], "SKILLS\nPython\n")
    assert result["plateau_note"] is not None
    assert "sql" in result["plateau_note"].lower()


def test_plateau_note_credits_a_satisfied_either_or_group():
    required = [{"any_of": ["Master's degree", "Bachelor's degree"]}]
    resume = "EDUCATION\nBachelor of Science\n"
    result = score_resume_against_keywords(required, [], resume)
    assert result["plateau_note"] is not None
    assert "already satisfy a different way" in result["plateau_note"]


def test_missing_preferred_keywords_are_returned_with_point_values():
    # 2026-08-09: previously computed internally (to build ats_next_actions
    # text) but never handed back to the caller - drafting.py's
    # analyze_fit_before_drafting needs these real numbers to attach a
    # point_value to a free-form AI clarifying_question that happens to
    # correspond to a preferred (not required) keyword, and
    # detect_matched_keyword_regressions() needs them to catch a dropped
    # PREFERRED keyword too, not just required ones.
    result = score_resume_against_keywords(["python"], ["kubernetes"], "SKILLS\nPython\n")
    assert result["missing_preferred_keywords"] == [
        {"label": "kubernetes", "point_value": result["missing_preferred_keywords"][0]["point_value"]},
    ]
    assert result["missing_preferred_keywords"][0]["point_value"] > 0


def test_matched_group_explanations_are_returned():
    required = [{"any_of": ["Master's degree", "Bachelor's degree"]}]
    resume = "EDUCATION\nBachelor of Science\n"
    result = score_resume_against_keywords(required, [], resume)
    assert result["matched_group_explanations"]


def test_plateau_note_for_gaps_is_public_and_reusable():
    # Exported (not module-private) so drafting.py's analyze_fit_before_
    # drafting can recompute this against a narrowed missing_required list
    # (already-confirmed-but-not-yet-drafted gaps removed) without
    # reimplementing this exact sentence-building logic a second time.
    assert plateau_note_for_gaps([], []) is None
    note = plateau_note_for_gaps([{"label": "sql", "point_value": 5.0}], [])
    assert "sql" in note


def test_detect_matched_keyword_regressions_catches_a_dropped_required_keyword():
    # Real bug live-reproduced 2026-08-09 (Upstream Bio job, CLAUDE.md
    # known failure pattern #2): a regenerate fixed one required keyword
    # ("Engineering") while silently dropping a previously-matched one
    # ("life sciences") - net score stayed flat (27/30 both times), which
    # is exactly why this needs a real before/after diff, not just
    # trusting the net score didn't get worse.
    required = ["Engineering", "life sciences"]
    old_text = "SKILLS\nlife sciences background\n"
    new_text = "SKILLS\nEngineering background\n"
    assert detect_matched_keyword_regressions(required, [], old_text, new_text) == ["life sciences"]


def test_detect_matched_keyword_regressions_empty_when_nothing_lost():
    required = ["Python"]
    old_text = "SKILLS\nPython\n"
    new_text = "SKILLS\nPython, Databricks\n"  # only gained, nothing lost
    assert detect_matched_keyword_regressions(required, [], old_text, new_text) == []


def test_detect_matched_keyword_regressions_catches_a_dropped_preferred_keyword():
    old_text = "SKILLS\nKubernetes, Python\n"
    new_text = "SKILLS\nPython\n"
    assert detect_matched_keyword_regressions(["Python"], ["Kubernetes"], old_text, new_text) == ["Kubernetes"]


def test_detect_matched_keyword_regressions_returns_nothing_with_no_prior_text():
    # A first-ever draft has nothing to regress against.
    assert detect_matched_keyword_regressions(["Python"], [], None, "SKILLS\nPython\n") == []
    assert detect_matched_keyword_regressions(["Python"], [], "", "SKILLS\nPython\n") == []
