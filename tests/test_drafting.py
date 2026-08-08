from tailoring.drafting import (
    ATS_KEYWORDS_SYSTEM_PROMPT,
    RESUME_SPEC,
    RESUME_SPEC_USAJOBS,
    _drop_years_experience_keywords,
    _merge_keyword_gap_questions,
    _questions_worth_asking,
    _strip_rank_prefixes,
    _suggested_answer_for_keyword_gap,
    check_regenerate_impact,
    save_gap_answers,
)


def test_ats_keywords_prompt_excludes_years_of_experience_title_lists_and_soft_skills():
    # Real gap Zahir hit live 2026-08-07: the keyword extractor was pulling
    # years-of-experience thresholds ("8+ years"), alternate-title lists
    # ("IT director, solutions architect, technology consultant"), and
    # generic soft-skill phrases ("executive presence", "c-suite
    # stakeholders") out as if they were discrete checkable skills - a
    # candidate with 25+ years of exactly this experience got asked "do
    # you have real, genuine experience with it?" for things that were
    # never legitimate gaps, just the wrong category of thing extracted.
    assert "Years-of-experience thresholds" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "Alternate-title lists" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "Generic soft-skill" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "8+ years" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "executive presence" in ATS_KEYWORDS_SYSTEM_PROMPT


def test_ats_keywords_prompt_explains_either_or_group_extraction():
    # score-first-resume-flow spec item 2: a JD's "Master's degree, OR
    # Bachelor's degree plus 8+ years" must be extracted as ONE
    # {"any_of": [...]} group, not two independent flat keywords - a
    # candidate satisfying either alternative should never be dinged for
    # a "missing" requirement they've genuinely met a different way.
    assert "any_of" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "Either/or qualification groups" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "Master's degree, OR Bachelor's degree" in ATS_KEYWORDS_SYSTEM_PROMPT


def test_ats_keywords_schema_supports_either_or_group_items():
    # Anthropic's structured-output schema rejects "oneOf" (confirmed
    # live, 2026-08-08) - "anyOf" is the supported equivalent, used here.
    from tailoring.drafting import _ats_keywords_schema

    schema = _ats_keywords_schema()
    required_item_schema = schema["properties"]["required_keywords"]["items"]
    assert "anyOf" in required_item_schema
    assert "oneOf" not in required_item_schema
    branch_types = {branch.get("type") for branch in required_item_schema["anyOf"]}
    assert branch_types == {"string", "object"}


def test_drop_years_experience_keywords_passes_through_group_items_unchanged():
    # Group items ({"any_of": [...]}) must not crash the years-of-
    # experience filter (which used to assume every item was a plain
    # string) and must never be dropped by it - a years threshold
    # wouldn't sensibly appear as one side of a real either/or group.
    keywords = [{"any_of": ["Master's degree", "Bachelor's degree"]}, "8+ years", "Python"]
    assert _drop_years_experience_keywords(keywords) == [
        {"any_of": ["Master's degree", "Bachelor's degree"]}, "Python",
    ]


def test_drop_years_experience_keywords_filters_whole_phrase_variants():
    # Deterministic backstop, not left to prompt compliance alone - same
    # lesson as the rank-prefix/keyword-synonym fixes this week. Must
    # catch the exact real strings a real posting produced: "10+ years IT
    # leadership", "5 years executive technology".
    keywords = [
        "8+ years", "10+ years IT leadership", "5 years executive technology",
        "3 years experience", "Python", "AWS",
    ]
    assert _drop_years_experience_keywords(keywords) == ["Python", "AWS"]


def test_drop_years_experience_keywords_leaves_unrelated_terms_alone():
    keywords = ["SQL", "Project Management", "PMP certification"]
    assert _drop_years_experience_keywords(keywords) == keywords


def test_drop_years_experience_keywords_does_not_eat_a_real_skill_containing_a_number():
    # Must not overcorrect into stripping any keyword that merely contains
    # a digit - only ones that START with a number-of-years pattern.
    keywords = ["3D modeling", "Office 365", "24/7 on-call rotation"]
    assert _drop_years_experience_keywords(keywords) == keywords


def test_resume_spec_conditionally_keeps_or_drops_seniority_parenthetical():
    # Real complaint 2026-08-06 (Zahir, via Mirror): a seniority
    # parenthetical ("Head of IT (CIO-equivalent)") on a role below that
    # level looks like applying beneath himself - but for a VP+ target
    # role, the same qualifier supports the case he's already operated at
    # that level, so this is a per-job judgment call (confirmed with Zahir
    # 2026-08-06), not a blanket strip-always rule. The prompt still asks
    # for this (it worked in two live tests before failing a third), but
    # _strip_rank_prefixes now backstops it deterministically too - see
    # test_strip_rank_prefixes_also_removes_seniority_parentheticals below.
    for spec in (RESUME_SPEC, RESUME_SPEC_USAJOBS):
        assert "not inventing or embellishing" in spec
        assert "VP-level or higher, print the parenthetical in FULL" in spec
        assert "below VP-level, drop the parenthetical" in spec
        assert "'Head of IT (CIO-equivalent)'" in spec


def test_resume_spec_defers_rank_prefix_stripping_to_the_app():
    # Real gap found live 2026-08-06: asking the model to also drop a
    # leading rank-prefix ("Vice President, Head of Applications" ->
    # "Head of Applications") in the text itself was unreliable even with
    # an explicit instruction naming the exact string - it kept the prefix
    # on a below-VP posting twice in a row. Moved to a deterministic
    # code-level strip (_strip_rank_prefixes) gated on the model's own
    # target_seniority_at_least_vp judgment instead - the prompt just
    # needs to say "write it in full, the app handles stripping."
    for spec in (RESUME_SPEC, RESUME_SPEC_USAJOBS):
        assert "target_seniority_at_least_vp" in spec
        assert "the app strips that prefix automatically" in spec


def test_strip_rank_prefixes_removes_leading_rank_titles():
    text = (
        "Zahir Uddin\n\nPROFESSIONAL EXPERIENCE\n"
        "Vice President, Head of Applications\nMarch 2020 - December 2023\n"
        "- Did a thing mentioning the President, who approved the budget.\n"
    )
    result = _strip_rank_prefixes(text)
    assert "Head of Applications" in result
    assert "Vice President, Head of Applications" not in result
    # Only strips at the start of a line - must not eat a mid-sentence
    # "President," inside real bullet prose.
    assert "the President, who approved the budget" in result


def test_strip_rank_prefixes_handles_svp_evp_and_plain_president():
    for prefix, rest in [
        ("SVP, ", "Global Sales"),
        ("EVP, ", "Operations"),
        ("President, ", "North America"),
        ("Senior Vice President, ", "Engineering"),
        ("Executive Vice President, ", "Product"),
    ]:
        result = _strip_rank_prefixes(f"{prefix}{rest}\nJanuary 2020 - Present\n")
        assert result.startswith(rest), (prefix, result)


def test_strip_rank_prefixes_leaves_plain_titles_untouched():
    text = "Head of IT\nJanuary 2024 - January 2026\n"
    assert _strip_rank_prefixes(text) == text


def test_strip_rank_prefixes_also_removes_seniority_parentheticals():
    # Real gap found live 2026-08-07: the model reliably dropped this
    # parenthetical for a below-VP posting in two earlier live tests, then
    # kept it anyway on a later run of the exact same posting - the same
    # "prompt says X, model inconsistently does X" failure mode as the
    # rank-prefix, so it gets the same deterministic backstop.
    text = "Head of IT (CIO-equivalent)\nJanuary 2024 - January 2026\n- Did a thing.\n"
    result = _strip_rank_prefixes(text)
    assert "Head of IT" in result
    assert "(CIO-equivalent)" not in result


def test_strip_rank_prefixes_only_strips_parentheticals_containing_equivalent():
    # Narrow on purpose - must not eat an unrelated parenthetical.
    text = "Head of IT (Paramus, NJ)\nJanuary 2024 - January 2026\n"
    assert _strip_rank_prefixes(text) == text


def test_resume_spec_folds_target_role_alignment_into_summary_not_its_own_header():
    # Real problem Zahir hit live 2026-08-06: a job-application portal's
    # own auto-parser expected the first employer entry right after the
    # summary, saw the old standalone bold-caps "TARGET ROLE ALIGNMENT"
    # header there instead, and parsed its content straight into the
    # Company field of Work Experience 1. It's not a standard ATS section,
    # so it must not be styled to look like one anymore.
    for spec in (RESUME_SPEC, RESUME_SPEC_USAJOBS):
        assert "Do NOT give the job-to-experience alignment content its own separate" in spec
        assert "fold this content directly into the PROFESSIONAL SUMMARY" in spec


def test_maxed_score_suppresses_clarifying_questions():
    questions = [{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    assert _questions_worth_asking(questions, 100) == []


def test_below_max_score_keeps_clarifying_questions():
    questions = [{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    assert _questions_worth_asking(questions, 99) == questions


def test_empty_questions_stay_empty_regardless_of_score():
    assert _questions_worth_asking([], 42) == []
    assert _questions_worth_asking([], 100) == []


def test_suggested_answer_for_keyword_gap_is_honest_when_no_signal_at_all():
    result = _suggested_answer_for_keyword_gap("Databricks", {"name": "Jane Doe", "seniority": "Director"})
    assert result != ""
    # Never asserted as fact - must not claim the candidate has the skill.
    assert "unknown" in result.lower() or "please describe" in result.lower()


def test_suggested_answer_for_keyword_gap_surfaces_a_real_profile_mention():
    profile = {"name": "Jane Doe", "notes": "Led a Databricks migration in 2023."}
    result = _suggested_answer_for_keyword_gap("Databricks", profile)
    assert "Databricks" in result
    # Still hedged/asking for confirmation, not stated as settled fact.
    assert "confirm" in result.lower() or "?" in result


def test_suggested_answer_for_keyword_gap_handles_none_profile():
    result = _suggested_answer_for_keyword_gap("Databricks", None)
    assert result != ""


def _missing(*labels):
    # score-first-resume-flow spec item 2 (2026-08-08): missing_required_
    # keywords is now [{"label": str, "point_value": float}, ...], not flat
    # strings - this builds that shape for tests that don't care about the
    # exact point_value.
    return [{"label": label, "point_value": 5.0} for label in labels]


def test_merge_keyword_gap_questions_adds_a_real_skill_gap_question():
    # 2026-08-06: missing required keywords used to sit as inert
    # ats_next_actions bullet text - now they become real, answerable
    # clarifying_questions, same shape/mechanism as every other one.
    merged = _merge_keyword_gap_questions([], _missing("Databricks"))
    assert len(merged) == 1
    q = merged[0]
    assert q["type"] == "skill_gap"
    assert q["skill"] == "Databricks"
    assert "Databricks" in q["question"]
    # Zahir's correction 2026-08-06: even here, a real starting guess beats
    # a blank box - never fabricated as a fact, but something to react to.
    assert q["suggested_answer"] != ""
    # Real, scorer-computed point value threaded through (score-first-
    # resume-flow spec item 2/UI contract) - not re-derived by the caller.
    assert q["point_value"] == 5.0


def test_merge_keyword_gap_questions_adds_one_per_missing_keyword():
    merged = _merge_keyword_gap_questions([], _missing("Databricks", "Kubernetes"))
    assert {q["skill"] for q in merged} == {"Databricks", "Kubernetes"}


def test_merge_keyword_gap_questions_dedupes_against_existing_question_by_skill():
    existing = [{"skill": "Databricks experience", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(existing, _missing("Databricks"))
    # The AI already asked about this same skill in its own words - must
    # not show up twice under two different phrasings.
    assert len(merged) == 1
    assert merged[0] is existing[0]


def test_merge_keyword_gap_questions_does_not_bare_substring_false_match():
    # Real gap flagged by Mirror 2026-08-08: the old bidirectional bare
    # substring check ("x in y or y in x") would wrongly treat "IT" as
    # already asked just because it's a substring of an unrelated label
    # like "Credit risk modeling" - same class of bug as this week's
    # ats_score.py "it"-pronoun fix. A genuinely distinct question must
    # still be added.
    existing = [{"skill": "Credit risk modeling", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(existing, _missing("IT"))
    assert len(merged) == 2
    assert {q["skill"] for q in merged} == {"Credit risk modeling", "IT"}


def test_merge_keyword_gap_questions_dedup_is_case_insensitive():
    existing = [{"skill": "databricks", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(existing, _missing("Databricks"))
    assert len(merged) == 1


def test_merge_keyword_gap_questions_preserves_existing_questions():
    existing = [{"skill": "SK Life Science team size", "type": "skill_gap", "question": "?", "suggested_answer": "8-10?"}]
    merged = _merge_keyword_gap_questions(existing, _missing("Databricks"))
    assert existing[0] in merged
    assert len(merged) == 2


def test_merge_keyword_gap_questions_no_op_with_no_missing_keywords():
    existing = [{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    assert _merge_keyword_gap_questions(existing, []) == existing


def test_merge_keyword_gap_questions_does_not_re_ask_a_previously_answered_skill():
    # Real bug caught before shipping: without checking profile history, a
    # keyword the candidate already said "no, I don't have that" to would
    # keep coming back as a "new" question every single regenerate forever
    # - the deterministic keyword match has no memory of its own, and the
    # resume text will never gain a skill the candidate confirmed they
    # don't have. Same "a real answer means don't ask again" precedent as
    # profile/interview.py's own _already_answered().
    merged = _merge_keyword_gap_questions([], _missing("Databricks"), previously_answered_skills=["Databricks"])
    assert merged == []


def test_merge_keyword_gap_questions_previously_answered_dedup_is_case_insensitive_and_partial():
    merged = _merge_keyword_gap_questions([], _missing("Databricks"), previously_answered_skills=["databricks certification"])
    assert merged == []


def test_merge_keyword_gap_questions_still_asks_about_a_different_unanswered_skill():
    merged = _merge_keyword_gap_questions([], _missing("Databricks", "Terraform"), previously_answered_skills=["Databricks"])
    assert {q["skill"] for q in merged} == {"Terraform"}


def test_save_gap_answers_threads_the_question_text_through(isolated_data):
    # The "Previously answered" view (app.py) needs the original question
    # wording, not just the short skill label - must survive the full
    # save_gap_answers -> profile.interview.save_answer round trip.
    from profile.storage import load_profile

    job = {"title": "Director", "organization": "Acme"}
    save_gap_answers(job, [{
        "skill": "Databricks", "type": "skill_gap", "answer": "Yes, 3 years.",
        "question": "Do you have real experience with Databricks?",
    }])

    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["question"] == "Do you have real experience with Databricks?"


def test_save_gap_answers_tolerates_missing_question_field(isolated_data):
    # Older-shape entries (before "question" was threaded through) must not
    # crash this call - question is optional.
    from profile.storage import load_profile

    job = {"title": "Director", "organization": "Acme"}
    save_gap_answers(job, [{"skill": "Databricks", "type": "skill_gap", "answer": "Yes."}])

    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["question"] == ""


def test_save_gap_answers_never_triggers_document_generation(isolated_data, monkeypatch):
    # Locks the contract the score-first-resume-flow spec's item 4 depends
    # on (docs/score-first-resume-flow-spec.md): an answer must persist to
    # the profile regardless of whether a resume is ever generated. This
    # was already true (save_gap_answers only ever calls
    # profile.interview.save_answer) - today's UI just happens to always
    # chain a regenerate after it in the same button handler, a UI-layer
    # coupling, not a backend one. This test makes that a guaranteed
    # contract rather than an incidental fact, by failing loudly if
    # save_gap_answers is ever changed to call into drafting/generation or
    # the applications store itself.
    import tailoring.applications as applications
    import tailoring.drafting as drafting_module

    def _fail(*args, **kwargs):
        raise AssertionError("save_gap_answers must never call this")

    monkeypatch.setattr(drafting_module, "generate_documents", _fail)
    monkeypatch.setattr(applications, "upsert_application", _fail)

    job = {"title": "Director", "organization": "Acme"}
    save_gap_answers(job, [{"skill": "Databricks", "type": "skill_gap", "answer": "Yes, 3 years."}])

    from profile.storage import load_profile
    assert load_profile()["gap_interview_answers"][0]["skill"] == "Databricks"


def test_check_regenerate_impact_no_new_info_returns_last_real_cost(isolated_data):
    # score-first-resume-flow spec item 6.
    from cost_log import log_api_cost

    job = {"source": "linkedin", "job_id": "1"}
    app_record = {"resume_ats_score": 76, "documents_drafted_at": "2026-08-08T19:00:00+00:00"}
    profile = {"gap_interview_answers": [{"skill": "Old skill", "date_captured": "2026-08-01"}]}

    log_api_cost(purpose="draft_resume", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.42, job_key=("linkedin", "1"))

    result = check_regenerate_impact(job, app_record, profile)
    assert result["has_new_info"] is False
    assert result["new_fact_count"] == 0
    assert result["current_score"] == 76
    assert result["estimated_new_score"] is None
    assert result["cost_estimate"] is None
    assert result["last_generation_cost"] == 0.42


def test_check_regenerate_impact_detects_new_answers_since_last_draft(isolated_data):
    job = {"source": "linkedin", "job_id": "1"}
    app_record = {
        "resume_ats_score": 76,
        "documents_drafted_at": "2026-08-08T19:00:00+00:00",
        "resume_clarifying_questions": [
            {"type": "skill_gap", "skill": "Clinical development", "point_value": 5.0},
        ],
    }
    profile = {
        "gap_interview_answers": [
            {"skill": "Old skill", "date_captured": "2026-08-01"},
            {"skill": "Clinical development", "date_captured": "2026-08-08"},
        ],
    }

    result = check_regenerate_impact(job, app_record, profile)
    assert result["has_new_info"] is True
    assert result["new_fact_count"] == 1
    assert result["current_score"] == 76
    assert result["estimated_new_score"] == 81
    assert result["last_generation_cost"] is None


def test_check_regenerate_impact_estimated_score_is_capped_at_100(isolated_data):
    job = {"source": "linkedin", "job_id": "1"}
    app_record = {
        "resume_ats_score": 95,
        "documents_drafted_at": "2026-08-08T19:00:00+00:00",
        "resume_clarifying_questions": [
            {"type": "skill_gap", "skill": "Clinical development", "point_value": 20.0},
        ],
    }
    profile = {"gap_interview_answers": [{"skill": "Clinical development", "date_captured": "2026-08-08"}]}

    result = check_regenerate_impact(job, app_record, profile)
    assert result["estimated_new_score"] == 100


def test_check_regenerate_impact_ignores_answers_from_before_the_last_draft(isolated_data):
    job = {"source": "linkedin", "job_id": "1"}
    app_record = {"resume_ats_score": 76, "documents_drafted_at": "2026-08-08T19:00:00+00:00"}
    profile = {"gap_interview_answers": [{"skill": "Old skill", "date_captured": "2026-08-01"}]}

    result = check_regenerate_impact(job, app_record, profile)
    assert result["has_new_info"] is False


def test_check_regenerate_impact_treats_no_prior_draft_date_as_everything_new(isolated_data):
    job = {"source": "linkedin", "job_id": "1"}
    app_record = {"resume_ats_score": 0, "documents_drafted_at": None}
    profile = {"gap_interview_answers": [{"skill": "Some skill", "date_captured": "2026-08-01"}]}

    result = check_regenerate_impact(job, app_record, profile)
    assert result["has_new_info"] is True
    assert result["new_fact_count"] == 1
