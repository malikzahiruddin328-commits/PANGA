from tailoring.drafting import (
    ATS_KEYWORDS_SYSTEM_PROMPT,
    RESUME_SPEC,
    RESUME_SPEC_USAJOBS,
    _draft_one,
    _drop_generic_soft_skill_keywords,
    _drop_years_experience_keywords,
    _merge_keyword_gap_questions,
    _questions_worth_asking,
    _resume_schema,
    _strip_degree_in_prefix_keywords,
    _strip_rank_prefixes,
    _suggested_answer_for_keyword_gap,
    analyze_fit_before_drafting,
    check_regenerate_impact,
    generate_documents,
    request_additional_gap_questions,
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


def test_ats_keywords_prompt_explains_degree_field_lists():
    # Real gap Zahir hit live 2026-08-09 (Upstream Bio posting): "Bachelor's
    # degree in Information Technology, Computer Science, Engineering, or
    # a related field required" was extracted as 4 separate flat required
    # keywords instead of one any_of group for the field list - he was
    # dinged for missing "Computer Science" despite matching Information
    # Technology and Engineering, which the posting itself treats as
    # interchangeable.
    assert "Degree-field lists" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "Information Technology, Computer Science, Engineering, or a related field" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "a related field" in ATS_KEYWORDS_SYSTEM_PROMPT


def test_ats_keywords_prompt_forbids_silently_dropping_a_broad_named_field():
    # Real gap caught live 2026-08-09 across TWO separate real postings
    # (Net at Work, GELLERT GLOBAL GROUP): each listed "...Information
    # Technology, Computer Science, Business, or a related field" and the
    # extraction dropped "Business" entirely - not flattened like its
    # neighbors, just silently missing from the output altogether.
    assert "Business" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "quietly drop one" in ATS_KEYWORDS_SYSTEM_PROMPT


def test_ats_keywords_prompt_explains_multi_tier_either_or_chains():
    # Real gap caught live 2026-08-09 on 2 real Amgen postings: "Doctorate
    # degree OR Masters degree and 2 years of Computer Science, IT or
    # related field OR Bachelors degree and 4 years of [...] OR Associates
    # degree and 8 years of [...] OR High school diploma/GED and 10 years
    # of [...]" - a 5-tier chain with a nested field-list inside every
    # tier, extracted as 7 completely flat keywords instead of two any_of
    # groups (one for the degree levels, one for the shared field list).
    assert "Doctorate degree" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "High school diploma/GED" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "MORE than two alternatives" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "TWO separate any_of groups" in ATS_KEYWORDS_SYSTEM_PROMPT


def test_ats_keywords_prompt_explains_dual_role_terms():
    # Real gap caught live 2026-08-09 (GELLERT posting): "Supply Chain
    # Management" is both an acceptable degree field AND a separately
    # required substantive skill/experience area elsewhere in the same
    # posting - holding a degree in it doesn't prove real hands-on
    # experience in it, so it needs extracting both ways, not just once.
    assert "Supply Chain Management" in ATS_KEYWORDS_SYSTEM_PROMPT
    assert "BOTH as an any_of alternative AND as its own independent flat required keyword" in ATS_KEYWORDS_SYSTEM_PROMPT


def test_strip_degree_in_prefix_canonicalizes_a_bundled_flat_keyword():
    # Deterministic backstop for the rule above, same "backstop, not just
    # an instruction" bar as every other keyword-extraction fix this week:
    # even if the model still bundles the phrase despite the prompt's
    # instruction to keep it atomic, this canonicalizes it down to just
    # the field name before scoring ever sees it.
    assert _strip_degree_in_prefix_keywords(["Bachelor's degree in Computer Science"]) == ["Computer Science"]
    assert _strip_degree_in_prefix_keywords(["Master's in Data Science"]) == ["Data Science"]
    assert _strip_degree_in_prefix_keywords(["PhD in Chemistry"]) == ["Chemistry"]


def test_strip_degree_in_prefix_canonicalizes_any_of_members():
    keywords = [{"any_of": ["Bachelor's degree in Information Technology", "Bachelor's degree in Computer Science"]}]
    assert _strip_degree_in_prefix_keywords(keywords) == [{"any_of": ["Information Technology", "Computer Science"]}]


def test_strip_degree_in_prefix_leaves_unrelated_keywords_untouched():
    assert _strip_degree_in_prefix_keywords(["Python", "AWS", "Bachelor's degree"]) == ["Python", "AWS", "Bachelor's degree"]


def test_strip_degree_in_prefix_only_matches_at_the_start():
    # A field that happens to contain "degree" or "in" elsewhere must not
    # be mangled - only a genuine leading degree-level prefix is stripped.
    assert _strip_degree_in_prefix_keywords(["Degree Auditing Systems"]) == ["Degree Auditing Systems"]


def test_degree_field_group_fix_matches_the_real_upstream_bio_posting_score():
    # Live-verified, real regression: this job's stored resume_text
    # literally contains "Information Technology" and "Engineering" but
    # not "Computer Science" - before this fix, the 4 flat degree-related
    # keywords scored "Computer Science" as a real missing gap (1.9 pts)
    # even though the posting itself says any of the three fields
    # satisfies the requirement. Simulates the CORRECTED extraction shape
    # (the actual live AI call can't be re-run from this environment - no
    # API key here - so this locks in the deterministic scoring side,
    # which is what this fix can actually guarantee) against the real
    # resume text pulled from production data.
    from tailoring.ats_score import score_resume_against_keywords

    resume_text = (
        "PROFESSIONAL EXPERIENCE\nLed enterprise Information Technology strategy.\n"
        "Engineering background in systems architecture.\n"
        "EDUCATION\nBachelor's degree\n"
    )
    old_required = ["Bachelor's degree", "Information Technology", "Computer Science", "Engineering"]
    old_result = score_resume_against_keywords(old_required, [], resume_text)
    assert "Computer Science" in [m["label"] for m in old_result["missing_required_keywords"]]

    new_required = [
        "Bachelor's degree",
        {"any_of": ["Information Technology", "Computer Science", "Engineering"]},
    ]
    new_result = score_resume_against_keywords(new_required, [], resume_text)
    assert "Computer Science" not in [m["label"] for m in new_result["missing_required_keywords"]]
    assert new_result["ats_score"] > old_result["ats_score"]


def test_multi_tier_either_or_fix_matches_the_real_amgen_posting_score():
    # Live-verified, real regression (General's systemic sweep, 2026-08-09):
    # Amgen's real posting text is "Doctorate degree OR Masters degree and
    # 2 years of Computer Science, IT or related field OR Bachelors degree
    # and 4 years of [...] OR Associates degree and 8 years of [...] OR
    # High school diploma / GED and 10 years of [...]" - extracted as 7
    # completely flat keywords (5 degree levels + Computer Science + IT).
    # Zahir's real stored resume for this job scores a perfect match once
    # corrected to two any_of groups (degree level, field) - simulating
    # the corrected extraction shape against a realistic excerpt of his
    # real resume text (the actual live AI re-extraction can't be run from
    # this environment - no API key here - so this locks in the
    # deterministic scoring side, which is what this fix can guarantee).
    from tailoring.ats_score import score_resume_against_keywords

    resume_text = (
        "PROFESSIONAL EXPERIENCE\nLed IT transformation programs across enterprise systems.\n"
        "EDUCATION\nBachelor's degree, Information Systems\n"
    )
    old_required = ["Doctorate degree", "Masters degree", "Bachelors degree", "Associates degree", "High school diploma / GED", "Computer Science", "IT"]
    old_result = score_resume_against_keywords(old_required, [], resume_text)
    assert "Doctorate degree" in [m["label"] for m in old_result["missing_required_keywords"]]
    assert "Masters degree" in [m["label"] for m in old_result["missing_required_keywords"]]
    assert "Associates degree" in [m["label"] for m in old_result["missing_required_keywords"]]
    assert "High school diploma / GED" in [m["label"] for m in old_result["missing_required_keywords"]]
    assert "Computer Science" in [m["label"] for m in old_result["missing_required_keywords"]]

    new_required = [
        {"any_of": ["Doctorate degree", "Masters degree", "Bachelors degree", "Associates degree", "High school diploma / GED"]},
        {"any_of": ["Computer Science", "IT"]},
    ]
    new_result = score_resume_against_keywords(new_required, [], resume_text)
    assert new_result["missing_required_keywords"] == []
    assert new_result["ats_score"] > old_result["ats_score"]


def test_dual_role_term_is_still_honestly_flagged_as_a_separate_requirement():
    # Live-verified, real regression (GELLERT posting): "Supply Chain
    # Management" is both an acceptable degree field AND a separately
    # required substantive skill elsewhere in the same posting. A resume
    # that only shows the degree-field alternative satisfied via a
    # DIFFERENT field (e.g. Information Technology) must still show the
    # standalone "Supply Chain Management" experience requirement as a
    # real, honest gap - the group being satisfied must not silently
    # suppress the separate substantive requirement the posting also asks
    # for.
    from tailoring.ats_score import score_resume_against_keywords

    resume_text = "PROFESSIONAL EXPERIENCE\nLed enterprise Information Technology strategy.\nEDUCATION\nBachelor's degree\n"
    required = [
        "Bachelor's degree",
        {"any_of": ["Information Technology", "Computer Science", "Business", "Supply Chain Management"]},
        "Supply Chain Management",
    ]
    result = score_resume_against_keywords(required, [], resume_text)
    missing_labels = [m["label"] for m in result["missing_required_keywords"]]
    assert "Information Technology OR Computer Science OR Business OR Supply Chain Management" not in missing_labels
    assert "Supply Chain Management" in missing_labels


def test_resume_spec_explains_the_hedged_unconfirmed_claim_exception():
    # Zahir's explicit, deliberate exception to "never invent or embellish"
    # (2026-08-09): a plausible-but-unconfirmed guess may be written
    # directly into resume text with a trailing "?", matching the existing
    # suggested_answer hedge convention - but only with real contextual
    # basis, never a fact with zero basis, and every such guess must also
    # appear in the unconfirmed_claims field.
    for spec in (RESUME_SPEC, RESUME_SPEC_USAJOBS):
        assert "trailing '?'" in spec
        assert "unconfirmed_claims" in spec
        assert "Never guess a fact with NO real basis" in spec


def test_resume_spec_instructs_active_cross_referencing_of_profile_facts():
    # Real gap Zahir hit live 2026-08-09: a posting's preferred term
    # ("commercial scale readiness") was left as a passive next-action
    # suggestion even though the profile already had a real, on-point fact
    # supporting it (a revenue-growth story) - the drafting call has full
    # access to the profile and should actively look for that connection
    # before falling back to passive advice Zahir has to act on himself.
    for spec in (RESUME_SPEC, RESUME_SPEC_USAJOBS):
        assert "commercial scale readiness" in spec
        assert "Actively cross-reference" in spec
        # Must stay honest: only when a real fact genuinely supports the
        # term, never a forced/stretched connection.
        assert "without stretching its meaning" in spec
        assert "leave it uncovered" in spec
        # This is re-phrasing an ALREADY-TRUE fact, distinct from the
        # unconfirmed-guess "?" exception right below it - no hedge needed.
        assert "needs no '?' hedge" in spec


def test_resume_schema_requires_unconfirmed_claims_field():
    schema = _resume_schema()
    assert "unconfirmed_claims" in schema["properties"]
    assert "unconfirmed_claims" in schema["required"]
    item_props = schema["properties"]["unconfirmed_claims"]["items"]["properties"]
    assert set(item_props) == {"skill", "text"}


def test_draft_one_resume_threads_unconfirmed_claims_through(monkeypatch):
    # The AI's self-reported unconfirmed_claims must survive _draft_one's
    # post-processing (rank-prefix stripping, ATS rescoring, question
    # merging) and land in the returned dict unchanged - this is the only
    # snapshot tailoring.unconfirmed_claims.find_unconfirmed_markers() has
    # to attach a friendly skill label to a flagged "?" line.
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        return {
            "text": "SKILLS\nLed a team of 8-10 engineers?",
            "target_seniority_at_least_vp": False,
            "suggested_strategy_tag": "",
            "clarifying_questions": [],
            "unconfirmed_claims": [{"skill": "Team size", "text": "Led a team of 8-10 engineers?"}],
        }

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    result = _draft_one(object(), [], "resume", None, job=job, profile={})
    assert result["unconfirmed_claims"] == [{"skill": "Team size", "text": "Led a team of 8-10 engineers?"}]


def test_draft_one_injects_resume_consistency_block_for_cover_letter(monkeypatch):
    # Real gap Zahir hit live 2026-08-09: cover_letter/exec_bio/leadership_
    # summary each get an independent API call sharing only the raw
    # job+profile context, never the resume text drafted earlier in the
    # same batch - so a fact could be phrased or rounded differently across
    # documents, which reads as a red-flag inconsistency to a reviewer, not
    # cosmetic drift.
    import tailoring.drafting as drafting

    captured = {}

    def _fake_call_structured(client, **kwargs):
        captured["user_content"] = kwargs["user_content"]
        return {"cover_letter": "Dear Hiring Team, ..."}

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1"}

    _draft_one(
        object(), [], "cover_letter", None, job=job, profile={},
        resume_text_for_consistency="PROFESSIONAL EXPERIENCE\nGrew revenue from $500K to $1B.\n",
    )
    combined_text = " ".join(b["text"] for b in captured["user_content"] if b.get("type") == "text")
    assert "THE RESUME ALREADY WRITTEN FOR THIS CANDIDATE" in combined_text
    assert "Grew revenue from $500K to $1B." in combined_text
    assert "Stay factually consistent with it" in combined_text


def test_draft_one_does_not_inject_consistency_block_when_no_resume_text_given(monkeypatch):
    import tailoring.drafting as drafting

    captured = {}

    def _fake_call_structured(client, **kwargs):
        captured["user_content"] = kwargs["user_content"]
        return {"cover_letter": "Dear Hiring Team, ..."}

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1"}

    _draft_one(object(), [], "cover_letter", None, job=job, profile={}, resume_text_for_consistency=None)
    combined_text = " ".join(b["text"] for b in captured["user_content"] if b.get("type") == "text")
    assert "THE RESUME ALREADY WRITTEN" not in combined_text


def test_draft_one_does_not_inject_consistency_block_for_resume_itself(monkeypatch):
    # The resume is the source of truth other documents check against - it
    # never needs to check itself.
    import tailoring.drafting as drafting

    captured = {}

    def _fake_call_structured(client, **kwargs):
        captured["user_content"] = kwargs["user_content"]
        return {
            "text": "SKILLS\nPython", "target_seniority_at_least_vp": False,
            "suggested_strategy_tag": "", "clarifying_questions": [], "unconfirmed_claims": [],
        }

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    _draft_one(object(), [], "resume", None, job=job, profile={}, resume_text_for_consistency="some earlier text")
    combined_text = " ".join(b["text"] for b in captured["user_content"] if b.get("type") == "text")
    assert "THE RESUME ALREADY WRITTEN" not in combined_text


def test_generate_documents_passes_the_just_drafted_resume_to_later_docs_in_the_same_batch(monkeypatch):
    import tailoring.drafting as drafting

    seen_resume_text_for = {}

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        seen_resume_text_for[doc_key] = resume_text_for_consistency
        if doc_key == "resume":
            return {
                "text": "PROFESSIONAL EXPERIENCE\nReal resume text.", "ats_score": 80,
                "ats_rationale": "", "ats_next_actions": [], "clarifying_questions": [], "unconfirmed_claims": [],
            }
        return "drafted text"

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    drafting.generate_documents(job, {}, ["resume", "cover_letter", "exec_bio"])

    assert seen_resume_text_for["resume"] is None  # nothing to be consistent with yet
    assert seen_resume_text_for["cover_letter"] == "PROFESSIONAL EXPERIENCE\nReal resume text."
    assert seen_resume_text_for["exec_bio"] == "PROFESSIONAL EXPERIENCE\nReal resume text."


def test_generate_documents_uses_existing_resume_text_when_resume_not_in_this_batch(monkeypatch):
    # The "redraft just the cover letter, resume unchanged" case - resume
    # isn't in doc_keys at all, so the caller (app.py) passes the job's
    # already-stored resume_text in explicitly.
    import tailoring.drafting as drafting

    seen_resume_text_for = {}

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        seen_resume_text_for[doc_key] = resume_text_for_consistency
        return "drafted text"

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}

    drafting.generate_documents(job, {}, ["cover_letter"], existing_resume_text="An already-stored resume.")

    assert seen_resume_text_for["cover_letter"] == "An already-stored resume."


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


def test_drop_generic_soft_skill_keywords_filters_the_exact_named_examples():
    # Real gap Mirror's audit caught 2026-08-09: the 2026-08-07 fix
    # ("Stop ATS keyword extraction from pulling tenure/titles/soft-
    # skills") only ever gave years-of-experience a real deterministic
    # backstop - soft-skills relied on the prompt alone despite the
    # commit title reading as fully solved. This is the real backstop,
    # covering the exact phrases ATS_KEYWORDS_SYSTEM_PROMPT already names
    # as never-extract.
    keywords = ["executive presence", "c-suite stakeholders", "presentation", "Python", "AWS"]
    assert _drop_generic_soft_skill_keywords(keywords) == ["Python", "AWS"]


def test_drop_generic_soft_skill_keywords_is_case_and_punctuation_insensitive():
    keywords = ["Executive Presence", "C-Suite Stakeholders.", "Strong Communication Skills"]
    assert _drop_generic_soft_skill_keywords(keywords) == []


def test_drop_generic_soft_skill_keywords_passes_through_group_items_unchanged():
    keywords = [{"any_of": ["Master's degree", "Bachelor's degree"]}, "executive presence"]
    assert _drop_generic_soft_skill_keywords(keywords) == [{"any_of": ["Master's degree", "Bachelor's degree"]}]


def test_drop_generic_soft_skill_keywords_does_not_eat_a_real_skill_containing_a_denied_phrase():
    # Deny-list uses exact normalized equality, not skills_match's looser
    # phrase-containment - a real, specific term that happens to CONTAIN a
    # generic phrase as a whole word must survive.
    keywords = ["Presentation Layer Architecture", "Executive Presence Coaching Certification"]
    assert _drop_generic_soft_skill_keywords(keywords) == keywords


def test_drop_generic_soft_skill_keywords_leaves_unrelated_terms_alone():
    keywords = ["SQL", "Project Management", "PMP certification"]
    assert _drop_generic_soft_skill_keywords(keywords) == keywords


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
    assert merged[0]["skill"] == existing[0]["skill"]
    # Also gets the real point_value backfilled since it genuinely
    # corresponds to a missing required keyword - a second, deliberate
    # effect of the same skills_match lookup, not left blank just because
    # this question came from the AI's own wording rather than a
    # synthesized missing_required_keywords entry.
    assert merged[0]["point_value"] == 5.0


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


def test_merge_keyword_gap_questions_backfills_point_value_on_a_free_form_ai_question():
    # Real gap General caught Zahir hit live 2026-08-09: the AI's own
    # free-form clarifying_questions (from the same drafting call as the
    # resume text) never carried a point_value at all, so the UI's badge
    # silently never rendered for them - only questions synthesized from
    # missing_required_keywords got one. When a free-form question's
    # "skill" genuinely corresponds to a missing required/preferred
    # keyword the deterministic scorer already knows the value of, that
    # real number should get attached, not left blank.
    existing = [{"type": "skill_gap", "skill": "Databricks", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(existing, _missing("Databricks"))
    assert len(merged) == 1
    assert merged[0]["point_value"] == 5.0


def test_merge_keyword_gap_questions_backfill_checks_preferred_keywords_too():
    existing = [{"type": "skill_gap", "skill": "Kubernetes", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(
        existing, [], missing_preferred_keywords=_missing("Kubernetes"),
    )
    assert merged[0]["point_value"] == 5.0


def test_merge_keyword_gap_questions_leaves_point_value_none_with_no_matching_keyword():
    # A genuinely free-standing fact (team size, budget) with no
    # corresponding extracted keyword has no deterministic value to
    # attach - stays None rather than a guess; the UI is expected to say
    # so honestly (see test_results_tab_gap_questions.py) rather than
    # silently rendering nothing.
    existing = [{"type": "skill_gap", "skill": "SK Life Science team size", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(existing, _missing("Databricks"))
    assert merged[0].get("point_value") is None


def test_merge_keyword_gap_questions_does_not_overwrite_an_already_stored_point_value():
    # A question synthesized from missing_required_keywords in an earlier
    # draft already has its real point_value baked in when it's read back
    # here as part of clarifying_questions on a later call - must not be
    # clobbered by a fresh (possibly stale, if the score has since moved)
    # backfill lookup.
    existing = [{"type": "skill_gap", "skill": "Databricks", "question": "?", "suggested_answer": "", "point_value": 9.0}]
    merged = _merge_keyword_gap_questions(existing, _missing("Databricks"))
    assert merged[0]["point_value"] == 9.0


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


def test_save_gap_answers_stamps_date_captured_in_utc_not_local(isolated_data, monkeypatch):
    # Real bug found live 2026-08-08 (General): applications.py's
    # documents_drafted_at is stamped in UTC, but this used to stamp
    # date_captured with LOCAL date.today() - the two silently disagreed
    # for part of every day (e.g. any evening in a timezone behind UTC,
    # once UTC has already rolled to the next calendar date), making
    # check_regenerate_impact()'s "has new info since last draft"
    # comparison read backwards. Reproduces the exact real moment: local
    # time still reads 2026-08-08 but UTC has already rolled to 2026-08-09.
    import datetime as datetime_module

    class _FixedDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime_module.datetime(2026, 8, 9, 2, 30, tzinfo=tz)

    monkeypatch.setattr(datetime_module, "datetime", _FixedDatetime)

    job = {"title": "Director", "organization": "Acme"}
    save_gap_answers(job, [{"skill": "Databricks", "type": "skill_gap", "answer": "Yes, 3 years."}])

    from profile.storage import load_profile
    assert load_profile()["gap_interview_answers"][0]["date_captured"] == "2026-08-09"


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


def test_analyze_fit_before_drafting_score_does_not_move_with_no_new_answers():
    # Baseline behavior, unchanged: with nothing confirmed since the last
    # draft, "projected" is genuinely just the current drafted text's real
    # score - it should NOT move on its own with no new information, same
    # as before this fix.
    from tailoring.ats_score import score_resume_against_keywords

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": ["Python", "Databricks"], "ats_preferred_keywords": []}
    resume_text = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
    app_record = {"resume_text": resume_text}
    profile = {"gap_interview_answers": []}
    baseline = score_resume_against_keywords(job["ats_required_keywords"], [], resume_text)

    result = analyze_fit_before_drafting(job, profile, app_record)
    assert result["projected_score"] == baseline["ats_score"]


def test_analyze_fit_before_drafting_projects_a_confirmed_but_undrafted_answer():
    # Real gap General caught Zahir hit live 2026-08-09: "Projected score"
    # is supposed to be forward-looking, but a naive re-score of the SAME
    # unchanged resume_text can never move no matter how many Step-1
    # questions get answered - answering only saves the fact via
    # save_gap_answers(), it never touches resume_text (only a real
    # Generate does that). Once the candidate has genuinely confirmed a
    # fact that closes a real keyword gap, the projection must fold that
    # in as a hypothetical, using the SAME point_value arithmetic the
    # deterministic scorer already computed - not a separate guess.
    from tailoring.ats_score import score_resume_against_keywords

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": ["Python", "Databricks"], "ats_preferred_keywords": []}
    resume_text = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
    app_record = {"resume_text": resume_text}
    profile = {"gap_interview_answers": [
        {"skill": "Databricks", "date_captured": "2026-08-01", "answer": "Yes, 3 years."},
    ]}
    baseline = score_resume_against_keywords(job["ats_required_keywords"], [], resume_text)
    databricks_point_value = baseline["missing_required_keywords"][0]["point_value"]

    result = analyze_fit_before_drafting(job, profile, app_record)

    assert result["projected_score"] > baseline["ats_score"]
    assert result["projected_score"] == round(baseline["ats_score"] + databricks_point_value)
    # The confirmed gap must not still be described as an open, real gap.
    assert result["plateau_note"] is None or "Databricks" not in result["plateau_note"]
    # And it must not still be asked about as an open question either.
    assert not any(q.get("skill") == "Databricks" for q in result["open_questions"])


def test_analyze_fit_before_drafting_projection_is_capped_at_100():
    from tailoring.ats_score import score_resume_against_keywords

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": ["Databricks"], "ats_preferred_keywords": []}
    resume_text = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS"
    app_record = {"resume_text": resume_text}
    profile = {"gap_interview_answers": [{"skill": "Databricks", "date_captured": "2026-08-01"}]}

    result = analyze_fit_before_drafting(job, profile, app_record)
    assert result["projected_score"] <= 100


def test_analyze_fit_before_drafting_plateau_note_still_names_a_genuinely_unanswered_gap():
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": ["Python", "Databricks"], "ats_preferred_keywords": []}
    resume_text = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
    app_record = {"resume_text": resume_text}
    profile = {"gap_interview_answers": []}

    result = analyze_fit_before_drafting(job, profile, app_record)
    assert result["plateau_note"] is not None
    assert "Databricks" in result["plateau_note"]


def _fake_call_structured_returning(items):
    def _fake(client, **kwargs):
        return {"clarifying_questions": items}
    return _fake


def test_request_additional_gap_questions_returns_a_genuinely_new_question(monkeypatch):
    # Score-first-resume-flow spec item 5: clicking "Answer more questions"
    # is supposed to trigger a REAL new round of AI generation - real bug
    # Zahir hit live 2026-08-09 (General): the button used to be a bare
    # st.rerun(), so this never actually happened.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured_returning([
        {"type": "skill_gap", "skill": "Team size at Acme", "question": "How big was the team?", "suggested_answer": ""},
    ]))

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    app_record = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": []}
    profile = {"gap_interview_answers": []}

    result = request_additional_gap_questions(job, profile, app_record)
    assert result["added_count"] == 1
    assert result["new_questions"][0]["skill"] == "Team size at Acme"
    assert result["merged_clarifying_questions"] == result["new_questions"]


def test_request_additional_gap_questions_honest_empty_result(monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured_returning([]))

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    app_record = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": []}
    profile = {"gap_interview_answers": []}

    result = request_additional_gap_questions(job, profile, app_record)
    assert result["added_count"] == 0
    assert result["new_questions"] == []
    assert result["merged_clarifying_questions"] == []


def test_request_additional_gap_questions_filters_out_a_repeat_the_ai_shouldnt_have_returned(monkeypatch):
    # Real gap this guards against: prompt instructions alone aren't
    # trusted (CLAUDE.md known failure pattern #3) - if the AI ignores the
    # "don't repeat ALREADY COVERED" instruction anyway, this must still
    # not double up a question already asked or already confirmed.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured_returning([
        {"type": "skill_gap", "skill": "Databricks experience", "question": "?", "suggested_answer": ""},
        {"type": "skill_gap", "skill": "Genuinely new fact", "question": "?", "suggested_answer": ""},
    ]))

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    app_record = {
        "resume_text": "SKILLS\nPython",
        "resume_clarifying_questions": [{"type": "skill_gap", "skill": "Databricks", "question": "?", "suggested_answer": ""}],
    }
    profile = {"gap_interview_answers": []}

    result = request_additional_gap_questions(job, profile, app_record)
    assert result["added_count"] == 1
    assert result["new_questions"][0]["skill"] == "Genuinely new fact"


def test_request_additional_gap_questions_filters_out_a_confirmed_profile_skill(monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured_returning([
        {"type": "skill_gap", "skill": "Kubernetes", "question": "?", "suggested_answer": ""},
    ]))

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    app_record = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": []}
    profile = {"gap_interview_answers": [{"skill": "Kubernetes", "date_captured": "2026-08-01"}]}

    result = request_additional_gap_questions(job, profile, app_record)
    assert result["added_count"] == 0


def test_request_additional_gap_questions_filters_out_a_deterministic_missing_keyword(monkeypatch):
    # The AI shouldn't re-ask about a keyword gap the deterministic scorer
    # already surfaces separately via missing_required_keywords/
    # _merge_keyword_gap_questions.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured_returning([
        {"type": "skill_gap", "skill": "Databricks", "question": "?", "suggested_answer": ""},
    ]))

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": ["Databricks"], "ats_preferred_keywords": []}
    app_record = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": []}
    profile = {"gap_interview_answers": []}

    result = request_additional_gap_questions(job, profile, app_record)
    assert result["added_count"] == 0


def test_request_additional_gap_questions_preserves_existing_stored_questions(monkeypatch):
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured_returning([
        {"type": "skill_gap", "skill": "New fact", "question": "?", "suggested_answer": ""},
    ]))

    existing = [{"type": "skill_gap", "skill": "Old fact", "question": "?", "suggested_answer": ""}]
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    app_record = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": existing}
    profile = {"gap_interview_answers": []}

    result = request_additional_gap_questions(job, profile, app_record)
    assert len(result["merged_clarifying_questions"]) == 2
    assert existing[0] in result["merged_clarifying_questions"]


def test_request_additional_gap_questions_escalates_max_tokens_on_a_realistically_large_profile(monkeypatch):
    # Real crash found by RM's live-fire test against Zahir's ACTUAL
    # profile (2026-08-09): the prompt embeds the full master profile
    # (~98,000 characters on real data, not job-specific), and a fixed
    # max_tokens=3000 with no retry truncated on essentially the first
    # real call. Uses a realistically large synthetic profile (not a tiny
    # one) - a small mocked profile would never exercise the actual
    # token-budget problem and could let this silently regress.
    import json

    import tailoring.drafting as drafting

    large_profile = {
        "gap_interview_answers": [
            {"skill": f"Fact {i}", "date_captured": "2026-08-01", "answer": "x" * 500}
            for i in range(180)
        ],
    }
    assert len(json.dumps(large_profile)) > 90_000  # realistically large, matching the real report

    max_tokens_seen = []

    def _fake(client, **kwargs):
        max_tokens_seen.append(kwargs["max_tokens"])
        if kwargs["max_tokens"] < drafting._ANSWER_MORE_MAX_TOKENS_TIERS[-1]:
            raise drafting.LLMResponseTruncated("The response was cut off before finishing. Try again.")
        return {"clarifying_questions": []}

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake)

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    app_record = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": []}

    result = drafting.request_additional_gap_questions(job, large_profile, app_record)
    assert result["added_count"] == 0
    assert max_tokens_seen == drafting._ANSWER_MORE_MAX_TOKENS_TIERS


def test_request_additional_gap_questions_raises_if_even_the_largest_tier_truncates(monkeypatch):
    import pytest

    import tailoring.drafting as drafting

    def _always_truncates(client, **kwargs):
        raise drafting.LLMResponseTruncated("The response was cut off before finishing. Try again.")

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _always_truncates)

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    app_record = {"resume_text": "SKILLS\nPython", "resume_clarifying_questions": []}
    profile = {"gap_interview_answers": []}

    with pytest.raises(drafting.LLMResponseTruncated):
        drafting.request_additional_gap_questions(job, profile, app_record)


def test_request_additional_gap_questions_truncation_is_caught_by_the_ui_except_clause():
    # Verifies the exact claim in RM's live-fire report: app.py's button
    # handler catches (DraftingNotConfigured, DraftingFailed).
    # LLMResponseTruncated IS a subclass of LLMCallFailed/DraftingFailed
    # (same class, just aliased - see llm_client.LLMResponseTruncated's
    # own docstring), so this already catches it correctly - confirmed
    # here so that claim doesn't get silently assumed true or false again.
    from tailoring.drafting import DraftingFailed
    from llm_client import LLMResponseTruncated as _LLMResponseTruncated

    assert issubclass(_LLMResponseTruncated, DraftingFailed)


def test_rescore_against_cached_keywords_needs_no_api_call(monkeypatch):
    # No _client()/call_structured mock at all - proves this really is pure
    # arithmetic over what's already cached, not a disguised AI call. Real
    # case, 2026-08-09: Zahir's Upstream Bio job's cached keywords already
    # had the corrected either/or degree-field group (from an earlier
    # extraction) but the STORED score (82) hadn't been recomputed to
    # match - this function is exactly that free, instant recompute, and a
    # live check against his real data confirmed it (88, no missing
    # required keywords, the gap fully explained by 2 genuinely-missing
    # preferred keywords - not a keyword-extraction bug at all).
    import tailoring.drafting as drafting

    job = {
        "ats_required_keywords": [{"any_of": ["Information Technology", "Computer Science", "Engineering"]}],
        "ats_preferred_keywords": [],
    }
    app_record = {"resume_text": "Bachelor of Science in Information Technology.", "resume_clarifying_questions": []}

    result = drafting.rescore_against_cached_keywords(job, app_record, profile={})

    assert result["clarifying_questions"] == []
    assert "changed" not in result


def test_reextract_ats_keywords_and_rescore_replaces_the_stale_cached_list(monkeypatch):
    # Forced AI re-extraction path, for the rarer case where the job-level
    # keyword cache itself is still stale/buggy (not just the stored score
    # drifted from an already-correct cache - see
    # test_rescore_against_cached_keywords_needs_no_api_call above for
    # that cheaper, more common case). Exercised here against a resume
    # that only matches the CORRECTED single any_of group, not old buggy
    # flat keywords.
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        return {
            "required_keywords": [{"any_of": ["Information Technology", "Computer Science", "Engineering"]}],
            "preferred_keywords": [],
        }

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    job = {
        "source": "linkedin", "job_id": "1", "title": "Some role",
        "description": "Bachelor's degree in Information Technology, Computer Science, Engineering, or a related field.",
        "ats_required_keywords": ["Information Technology", "Computer Science", "Engineering"],
        "ats_preferred_keywords": [],
    }
    app_record = {"resume_text": "Bachelor of Science in Information Technology.", "resume_clarifying_questions": []}

    result = drafting.reextract_ats_keywords_and_rescore(job, app_record, profile={})

    assert result["changed"] is True
    # Old flat keywords would have dinged this resume for missing "Computer
    # Science"/"Engineering" even though it satisfies the field via
    # "Information Technology" - the corrected any_of group must not.
    assert job["ats_required_keywords"] == [{"any_of": ["Information Technology", "Computer Science", "Engineering"]}]
    assert result["clarifying_questions"] == []  # no gap left to ask about - the field group is satisfied
    # "Information Technology" moved from a flat OLD entry into a member
    # of the new any_of group - it hasn't disappeared, so this must not
    # be reported as wording drift.
    assert result["wording_regressions"] == []


def test_reextract_ats_keywords_and_rescore_surfaces_a_genuine_wording_regression(monkeypatch):
    # Real gap found live 2026-08-09 (General force-re-extracting Zahir's
    # Upstream Bio job): the same re-extraction call that fixed one bug
    # also reworded an unrelated, already-matching keyword purely from AI
    # non-determinism, silently costing a match. Verifies the caller can
    # actually see this rather than just getting a lower score with no
    # explanation.
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        return {"required_keywords": ["cloud infrastructure"], "preferred_keywords": []}

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    job = {
        "source": "linkedin", "job_id": "1", "title": "Some role", "description": "Needs cloud infra.",
        "ats_required_keywords": ["cloud-based infrastructure"], "ats_preferred_keywords": [],
    }
    app_record = {"resume_text": "SKILLS\nCloud-based infrastructure\n", "resume_clarifying_questions": []}

    result = drafting.reextract_ats_keywords_and_rescore(job, app_record, profile={})

    assert result["changed"] is True
    assert result["wording_regressions"] == ["cloud-based infrastructure"]


def test_reextract_ats_keywords_and_rescore_reports_unchanged_on_api_failure(monkeypatch):
    # A transient failure inside _extract_ats_keywords returns ([], [])
    # without touching the job dict at all (see that function's own
    # docstring) - the caller here must not mistake that for "genuinely no
    # keywords" and silently wipe out a real, previously-cached list.
    import tailoring.drafting as drafting
    from tailoring.drafting import DraftingFailed

    def _raising_call_structured(client, **kwargs):
        raise DraftingFailed("simulated transient failure")

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _raising_call_structured)

    old_required = ["Kubernetes"]  # genuinely absent from the resume below
    job = {
        "source": "linkedin", "job_id": "1", "title": "Some role", "description": "Needs Kubernetes.",
        "ats_required_keywords": old_required, "ats_preferred_keywords": [],
    }
    app_record = {"resume_text": "I know Python.", "resume_clarifying_questions": []}

    result = drafting.reextract_ats_keywords_and_rescore(job, app_record, profile={})

    assert result["changed"] is False
    assert job["ats_required_keywords"] == old_required  # untouched, not wiped to []
    assert result["wording_regressions"] == []  # nothing to diff - the call never went through
    # Still scored against the old, real keyword list, not an empty one -
    # a wipe-to-[] would report zero gaps, silently hiding the real one.
    # "0/1 required" proves it scored against the real 1-item old list, not
    # a wiped-to-[] "0/0 required" that would silently show zero gaps.
    assert "0/1 required" in result["ats_rationale"]
