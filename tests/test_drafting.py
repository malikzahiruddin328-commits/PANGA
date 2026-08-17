from tailoring.drafting import (
    ATS_KEYWORDS_SYSTEM_PROMPT,
    RESUME_SPEC,
    RESUME_SPEC_USAJOBS,
    _draft_group,
    _draft_one,
    _finalize_resume_draft,
    _drop_generic_soft_skill_keywords,
    _drop_years_experience_keywords,
    _merge_keyword_gap_questions,
    _profile_narrative_units,
    _profile_supports_skill,
    _questions_worth_asking,
    _render_education_section_verbatim,
    _resume_schema,
    _strip_degree_in_prefix_keywords,
    _strip_rank_prefixes,
    _suggested_answer_for_keyword_gap,
    _total_years_of_experience,
    analyze_fit_before_drafting,
    check_regenerate_impact,
    generate_documents,
    request_additional_gap_questions,
    save_gap_answers,
)
from tailoring.ats_score import keyword_literally_present


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


def test_draft_one_escalates_max_tokens_on_a_genuine_truncation(monkeypatch):
    # Real gap flagged live 2026-08-09 (General): verify the MAIN drafting
    # path has the same truncation-escalation protection request_
    # additional_gap_questions already got, not just newer code paths.
    # This resume path's fixed 20000-token budget was already far more
    # generous than the 3000 that caused a real crash elsewhere, but a
    # single fixed ceiling with no escalation is still the same class of
    # risk for a large enough profile/JD combination.
    import tailoring.drafting as drafting

    max_tokens_seen = []

    def _fake_call_structured(client, **kwargs):
        max_tokens_seen.append(kwargs["max_tokens"])
        if kwargs["max_tokens"] < 40000:
            raise drafting.LLMResponseTruncated("The response was cut off before finishing. Try again.")
        return {
            "text": "PROFESSIONAL EXPERIENCE\nReal resume text.",
            "target_seniority_at_least_vp": True, "suggested_strategy_tag": "",
            "clarifying_questions": [], "unconfirmed_claims": [],
        }

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    result = _draft_one(object(), [], "resume", None, job=job, profile={})

    assert max_tokens_seen == [20000, 40000]
    assert result["text"] == "PROFESSIONAL EXPERIENCE\nReal resume text."


def test_draft_one_raises_if_even_the_largest_tier_truncates(monkeypatch):
    import pytest

    import tailoring.drafting as drafting

    def _always_truncates(client, **kwargs):
        raise drafting.LLMResponseTruncated("The response was cut off before finishing. Try again.")

    monkeypatch.setattr(drafting, "call_structured", _always_truncates)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    with pytest.raises(drafting.LLMResponseTruncated):
        _draft_one(object(), [], "resume", None, job=job, profile={})


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


# --- _finalize_resume_draft() (2026-08-13, extracted from _draft_one()'s
# resume branch so a subscription-covered caller - tailoring.
# subscription_resume_qa, feature/in-app-subscription-qa - can run its own
# raw draft through the SAME deterministic safety gates, not a
# reimplementation of them.) ---


def test_finalize_resume_draft_matches_draft_one_for_the_same_raw_data(monkeypatch):
    import tailoring.drafting as drafting

    raw_data = {
        "text": "SKILLS\nLed a team of 8-10 engineers?",
        "target_seniority_at_least_vp": False,
        "suggested_strategy_tag": "concise-2-page",
        "clarifying_questions": [],
        "unconfirmed_claims": [{"skill": "Team size", "text": "Led a team of 8-10 engineers?"}],
    }
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    def _fake_call_structured(client, **kwargs):
        return raw_data
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    via_draft_one = _draft_one(object(), [], "resume", None, job=job, profile={})
    via_finalize_directly = _finalize_resume_draft(raw_data, job, {})

    assert via_draft_one == via_finalize_directly


def test_draft_one_resume_renders_education_verbatim_from_the_profile(monkeypatch):
    # 2026-08-10 fix: _draft_one must not trust the AI's own freeform
    # EDUCATION wording, even when the AI's motive (hitting a literal
    # "Bachelor's degree" keyword) is legitimate - the profile's own
    # structured fields win.
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        return {
            "text": "EDUCATION\nBachelor's degree - Bachelor of Science (BSc), Info Systems\n",
            "target_seniority_at_least_vp": True,
            "suggested_strategy_tag": "",
            "clarifying_questions": [],
            "unconfirmed_claims": [],
        }

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    profile = {"education": [{"degree": "Bachelor of Science, Information Systems", "institution": "Brunel University London"}]}

    result = _draft_one(object(), [], "resume", None, job=job, profile=profile)
    assert result["text"] == "EDUCATION\nBachelor of Science, Information Systems, Brunel University London"
    assert "BSc" not in result["text"]


def test_draft_one_resume_deterministically_flags_an_unhedged_fabricated_employer(monkeypatch):
    # Real gap General flagged live 2026-08-09: the "?" hedge only ever
    # catches what the AI ITSELF flags as uncertain - nothing verified an
    # UNHEDGED claim was real. _draft_one must run the deterministic
    # verifier (claim_verification.flag_unverified_resume_claims) on the
    # drafted text and fold what it finds into the SAME unconfirmed_claims
    # list the AI's own self-reports feed - reusing
    # tailoring.unconfirmed_claims.find_unconfirmed_markers()'s existing
    # gate/UI wholesale, not a second detection surface.
    import tailoring.drafting as drafting
    from tailoring.unconfirmed_claims import find_unconfirmed_markers

    def _fake_call_structured(client, **kwargs):
        return {
            "text": "PROFESSIONAL EXPERIENCE\nFabricated Startup Inc. - VP Eng  Jan 2015 - Jan 2020\n- Invented role.",
            "target_seniority_at_least_vp": True,
            "suggested_strategy_tag": "",
            "clarifying_questions": [],
            "unconfirmed_claims": [],  # the AI itself reported nothing uncertain here
        }

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    profile = {"work_history": [{"employer": "Real Employer Co.", "start": "2010", "end": "2020"}]}

    result = _draft_one(object(), [], "resume", None, job=job, profile=profile)

    assert result["text"].splitlines()[1].endswith("?")  # the fabricated line now carries the hedge marker
    assert {"skill": None, "text": "Fabricated Startup Inc. - VP Eng  Jan 2015 - Jan 2020"} in result["unconfirmed_claims"]
    # End-to-end proof this reuses the EXISTING gate wholesale - no new
    # detection surface needed on the reading side.
    app_record = {"resume_text": result["text"], "resume_unconfirmed_claims_ai_reported": result["unconfirmed_claims"]}
    assert find_unconfirmed_markers(app_record) == [
        {"field": "resume_text", "skill": None, "line": "Fabricated Startup Inc. - VP Eng  Jan 2015 - Jan 2020?"}
    ]


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
    # cover_letter + exec_bio requested together route through _draft_group
    # (2026-08-11) - mocked explicitly here for the same reason noted on
    # the ordering test above.
    import tailoring.drafting as drafting

    seen_resume_text_for = {}

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        seen_resume_text_for[doc_key] = resume_text_for_consistency
        # ats_score >= the self-correction target (90) so this unrelated
        # cross-document-consistency test doesn't also exercise the
        # self-correction retry loop.
        return {
            "text": "PROFESSIONAL EXPERIENCE\nReal resume text.", "ats_score": 95,
            "ats_rationale": "", "ats_next_actions": [], "clarifying_questions": [], "unconfirmed_claims": [],
            "missing_required_keywords": [], "missing_preferred_keywords": [],
        }

    def _fake_draft_group(client, shared_context, doc_keys, model, on_progress, indices, doc_total, job, profile, resume_text_for_consistency):
        for doc_key in doc_keys:
            seen_resume_text_for[doc_key] = resume_text_for_consistency
        return {doc_key: "drafted text" for doc_key in doc_keys}, {}

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    monkeypatch.setattr(drafting, "_draft_group", _fake_draft_group)
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


def test_generate_documents_threads_the_model_override_into_the_address_lookup(monkeypatch):
    # Real gap found 2026-08-11 (fit_score cheaper-model test): this call
    # never received generate_documents' own `model` override at all, so it
    # silently ran on call_with_web_search's default model regardless of
    # what the caller requested - every cost figure measured for a
    # non-default-model test run was wrong by however much this one call
    # actually cost, on whatever model it silently used instead.
    import tailoring.drafting as drafting

    captured = {}

    def _fake_call_with_web_search(client, **kwargs):
        captured["model"] = kwargs.get("model")
        return "123 Main St; Springfield, IL 62701", 0.01

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", lambda *a, **kw: "drafted text")
    monkeypatch.setattr(drafting, "call_with_web_search", _fake_call_with_web_search)
    monkeypatch.setattr("search.job_store.update_job_address", lambda *a, **kw: None)
    job = {"source": "linkedin", "job_id": "1", "organization": "Acme Corp"}

    drafting.generate_documents(job, {}, ["cover_letter"], model="claude-sonnet-5")

    assert captured["model"] == "claude-sonnet-5"


def test_generate_documents_drafts_non_resume_docs_concurrently(monkeypatch):
    # Real concurrency, not just "the code technically uses a thread pool" -
    # two workers must genuinely overlap in wall-clock time, proven with a
    # barrier both must reach before either is allowed to return. If a
    # future regression accidentally serialized these calls again, this
    # test would hang past its own timeout rather than pass by accident.
    #
    # Deliberately uses exec_bio + apply_answers, not cover_letter +
    # exec_bio (2026-08-11): cover_letter/exec_bio/leadership_summary now
    # combine into ONE _draft_group work item when 2+ of them are
    # requested together (see _draft_group's own docstring) - they're no
    # longer two independent concurrent calls in that case by design. This
    # test still needs two genuinely independent work items, so it picks
    # one group-eligible key alone (stays on the solo _draft_one path)
    # plus apply_answers (never grouped).
    import threading

    import tailoring.drafting as drafting

    barrier = threading.Barrier(2, timeout=5)

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        barrier.wait()  # raises threading.BrokenBarrierError if the other worker never arrives
        return f"drafted {doc_key}"

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}

    result = drafting.generate_documents(job, {}, ["exec_bio", "apply_answers"])

    assert result["exec_bio"] == "drafted exec_bio"
    assert result["apply_answers"] == "drafted apply_answers"
    assert result["_errors"] == {}


def test_generate_documents_resume_always_drafts_before_the_concurrent_pool(monkeypatch):
    # The self-correction loop and cross-document consistency both depend
    # on the resume being fully drafted before any other doc starts (see
    # generate_documents()'s own docstring) - resume must never be
    # submitted into the same thread pool as the other doc_keys.
    #
    # cover_letter + exec_bio requested together now route through
    # _draft_group (2026-08-11, combined-schema cache fix), not two
    # separate _draft_one calls - mocks both so this test still exercises
    # the real routing decision instead of accidentally falling through
    # _draft_group's own call_structured-failure fallback path.
    import tailoring.drafting as drafting

    call_order = []

    def _fake_self_correction(client, shared_context, model, on_progress, doc_index, doc_total, job, profile):
        call_order.append("resume")
        return {
            "text": "Real resume text.", "ats_score": 95, "ats_rationale": "", "ats_next_actions": [],
            "clarifying_questions": [], "unconfirmed_claims": [],
            "missing_required_keywords": [], "missing_preferred_keywords": [],
        }

    def _fake_draft_group(client, shared_context, doc_keys, model, on_progress, indices, doc_total, job, profile, resume_text_for_consistency):
        call_order.extend(doc_keys)
        # If this ever runs before the resume, resume_text_for_consistency
        # would still be None here instead of the resume's real text.
        assert resume_text_for_consistency == "Real resume text."
        return {doc_key: f"drafted {doc_key}" for doc_key in doc_keys}, {}

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_resume_with_self_correction", _fake_self_correction)
    monkeypatch.setattr(drafting, "_draft_group", _fake_draft_group)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    drafting.generate_documents(job, {}, ["resume", "cover_letter", "exec_bio"])

    assert call_order[0] == "resume"
    assert set(call_order[1:]) == {"cover_letter", "exec_bio"}


def test_generate_documents_one_doc_failing_does_not_lose_the_others(monkeypatch):
    # Core partial-failure contract: with 2+ doc_keys requested, one
    # document raising must never discard a sibling document that already
    # succeeded, and must never raise out of generate_documents() itself.
    # Uses exec_bio + apply_answers (not both group-eligible together) so
    # this stays a pure solo-path test - _draft_group's own partial/
    # catastrophic-failure handling is covered separately below.
    import tailoring.drafting as drafting
    from tailoring.drafting import DraftingFailed

    reported = []
    monkeypatch.setattr(drafting, "_report_drafting_failure", lambda job, doc_key, exc: reported.append((doc_key, str(exc))))

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        if doc_key == "exec_bio":
            raise DraftingFailed("simulated refusal")
        return f"drafted {doc_key}"

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}

    result = drafting.generate_documents(job, {}, ["exec_bio", "apply_answers"])

    assert result["apply_answers"] == "drafted apply_answers"
    assert "exec_bio" not in result
    assert result["_errors"] == {"exec_bio": "simulated refusal"}
    assert reported == [("exec_bio", "simulated refusal")]


def test_generate_documents_catches_and_reports_any_exception_type_not_just_drafting_errors(monkeypatch):
    # 2026-08-10 fix: a prior version only caught DraftingNotConfigured/
    # DraftingFailed inside the concurrent batch - any other exception
    # (a bug in local post-processing, not an anticipated API/refusal
    # error) escaped uncaught, crashed the whole generate_documents() call,
    # discarded every doc that HAD already succeeded in the same batch, and
    # never reported to Bhangi. This proves an arbitrary exception type is
    # now handled exactly like the anticipated ones. Uses exec_bio +
    # apply_answers - see comment on the test above for why.
    import tailoring.drafting as drafting

    reported = []
    monkeypatch.setattr(drafting, "_report_drafting_failure", lambda job, doc_key, exc: reported.append((doc_key, type(exc).__name__)))

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        if doc_key == "exec_bio":
            raise KeyError("unexpected response shape")
        return f"drafted {doc_key}"

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}

    result = drafting.generate_documents(job, {}, ["exec_bio", "apply_answers"])

    assert result["apply_answers"] == "drafted apply_answers"
    assert "exec_bio" in result["_errors"]
    assert reported == [("exec_bio", "KeyError")]


def test_generate_documents_single_doc_key_reraises_any_exception_type(monkeypatch):
    # Preserves the pre-existing single-doc contract (callers like the
    # resume-regen path rely on a raise, not a swallowed error) - extended
    # 2026-08-10 to cover exception types beyond DraftingNotConfigured/
    # DraftingFailed, since generate_documents() as a whole no longer
    # narrows what it catches.
    import tailoring.drafting as drafting

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        raise ValueError("boom")

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}

    import pytest
    with pytest.raises(ValueError, match="boom"):
        drafting.generate_documents(job, {}, ["cover_letter"])


def test_generate_documents_resume_failure_does_not_block_the_other_docs(monkeypatch):
    # The resume drafts synchronously before the pool - if IT fails, the
    # remaining doc_keys must still draft (without a consistency block,
    # since there's no fresh resume text to be consistent with), not be
    # skipped just because the batch's first document failed.
    import tailoring.drafting as drafting

    def _fake_self_correction(client, shared_context, model, on_progress, doc_index, doc_total, job, profile):
        raise RuntimeError("resume drafting blew up")

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        assert resume_text_for_consistency is None
        return f"drafted {doc_key}"

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_resume_with_self_correction", _fake_self_correction)
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    monkeypatch.setattr(drafting, "_report_drafting_failure", lambda job, doc_key, exc: None)
    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}

    result = drafting.generate_documents(job, {}, ["resume", "cover_letter"])

    assert "resume" not in result
    assert result["cover_letter"] == "drafted cover_letter"
    assert "resume" in result["_errors"]


def test_generate_documents_errors_key_always_present_on_full_success(monkeypatch):
    import tailoring.drafting as drafting

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        return f"drafted {doc_key}"

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}

    result = drafting.generate_documents(job, {}, ["cover_letter"])

    assert result["_errors"] == {}


def test_generate_documents_never_exceeds_max_concurrent_drafts(monkeypatch):
    # 2026-08-11: cover_letter/exec_bio/leadership_summary now combine into
    # ONE _draft_group pool work item instead of three separate _draft_one
    # ones (see _draft_group's own docstring), so requesting all 4
    # non-resume doc types together now submits 2 pool work items (the
    # group + apply_answers), not 4 - mocks both so this still verifies
    # real overlap between genuinely independent work items, just against
    # the new, smaller real ceiling rather than the old one.
    import threading

    import tailoring.drafting as drafting

    lock = threading.Lock()
    concurrent_count = 0
    max_seen = 0

    def _track_concurrency():
        nonlocal concurrent_count, max_seen
        with lock:
            concurrent_count += 1
            max_seen = max(max_seen, concurrent_count)
        import time
        time.sleep(0.05)
        with lock:
            concurrent_count -= 1

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        _track_concurrency()
        return f"drafted {doc_key}"

    def _fake_draft_group(client, shared_context, doc_keys, model, on_progress, indices, doc_total, job, profile, resume_text_for_consistency):
        _track_concurrency()
        return {doc_key: f"drafted {doc_key}" for doc_key in doc_keys}, {}

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    monkeypatch.setattr(drafting, "_draft_group", _fake_draft_group)
    job = {"source": "linkedin", "job_id": "1"}
    doc_keys = ["cover_letter", "exec_bio", "leadership_summary", "apply_answers"]  # 4 non-resume types, all requested

    drafting.generate_documents(job, {}, doc_keys)

    assert max_seen <= drafting.MAX_CONCURRENT_DRAFTS
    # Real overlap, not just "the code technically uses a pool" - the
    # group work item and apply_answers must genuinely overlap.
    assert max_seen > 1


# --- _draft_group: combined cover_letter/exec_bio/leadership_summary call (2026-08-11) ---

def test_draft_group_uses_one_combined_schema_call_for_all_requested_keys(monkeypatch):
    import tailoring.drafting as drafting

    captured = {}

    def _fake_call_structured(client, **kwargs):
        captured["schema"] = kwargs["schema"]
        captured["call_count"] = captured.get("call_count", 0) + 1
        return {"cover_letter": "Dear Hiring Team...", "exec_bio": "Jane Doe is...", "leadership_summary": "A proven leader..."}

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1"}
    indices = {"cover_letter": 2, "exec_bio": 3, "leadership_summary": 4}

    results, errors = _draft_group(
        object(), [], ["cover_letter", "exec_bio", "leadership_summary"], None, None, indices, 4,
        job=job, profile={}, resume_text_for_consistency=None,
    )

    assert captured["call_count"] == 1  # one call, not three
    assert set(captured["schema"]["properties"]) == {"cover_letter", "exec_bio", "leadership_summary"}
    assert results == {
        "cover_letter": "Dear Hiring Team...", "exec_bio": "Jane Doe is...", "leadership_summary": "A proven leader...",
    }
    assert errors == {}


def test_draft_group_retries_only_the_blank_field_not_the_whole_batch(monkeypatch):
    # A malformed field inside an otherwise-good combined response should
    # cost a small targeted retry, not discard the two good fields or
    # force a full expensive re-draft of all three.
    import tailoring.drafting as drafting

    call_structured_calls = []

    def _fake_call_structured(client, **kwargs):
        call_structured_calls.append(kwargs["schema"])
        return {"cover_letter": "Dear Hiring Team...", "exec_bio": "", "leadership_summary": "A proven leader..."}

    draft_one_calls = []

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        draft_one_calls.append(doc_key)
        return "Individually redrafted exec bio."

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}
    indices = {"cover_letter": 2, "exec_bio": 3, "leadership_summary": 4}

    results, errors = _draft_group(
        object(), [], ["cover_letter", "exec_bio", "leadership_summary"], None, None, indices, 4,
        job=job, profile={}, resume_text_for_consistency=None,
    )

    assert len(call_structured_calls) == 1  # the combined call itself is never retried in full
    assert draft_one_calls == ["exec_bio"]  # only the blank field gets an individual re-draft
    assert results["cover_letter"] == "Dear Hiring Team..."  # good fields kept as-is
    assert results["leadership_summary"] == "A proven leader..."
    assert results["exec_bio"] == "Individually redrafted exec bio."
    assert errors == {}


def test_draft_group_falls_back_to_independent_drafting_on_a_full_call_failure(monkeypatch):
    # The combined call itself failing outright (refusal, exhausted
    # truncation retries, API error) must not sink all three docs together
    # - falls back to today's existing independent-per-doc path so fault
    # isolation never regresses versus before this function existed.
    import tailoring.drafting as drafting
    from tailoring.drafting import DraftingFailed

    def _always_fails(client, **kwargs):
        raise DraftingFailed("simulated total failure")

    def _fake_draft_one(client, shared_context, doc_key, model, on_progress=None, doc_index=1, doc_total=1, job=None, profile=None, resume_text_for_consistency=None):
        if doc_key == "exec_bio":
            raise DraftingFailed("this one also fails independently")
        return f"drafted {doc_key}"

    monkeypatch.setattr(drafting, "call_structured", _always_fails)
    monkeypatch.setattr(drafting, "_draft_one", _fake_draft_one)
    job = {"source": "linkedin", "job_id": "1"}
    indices = {"cover_letter": 2, "exec_bio": 3, "leadership_summary": 4}

    results, errors = _draft_group(
        object(), [], ["cover_letter", "exec_bio", "leadership_summary"], None, None, indices, 4,
        job=job, profile={}, resume_text_for_consistency=None,
    )

    # The two docs that succeed independently are NOT lost just because
    # the combined call and one sibling doc both failed.
    assert results == {"cover_letter": "drafted cover_letter", "leadership_summary": "drafted leadership_summary"}
    assert "exec_bio" in errors
    assert "exec_bio" not in results


def test_draft_group_escalates_max_tokens_on_a_genuine_truncation(monkeypatch):
    import tailoring.drafting as drafting

    calls = []

    def _fake_call_structured(client, **kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) == 1:
            raise drafting.LLMResponseTruncated("truncated")
        return {"cover_letter": "text", "exec_bio": "text"}

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    job = {"source": "linkedin", "job_id": "1"}
    indices = {"cover_letter": 2, "exec_bio": 3}

    results, errors = _draft_group(
        object(), [], ["cover_letter", "exec_bio"], None, None, indices, 3,
        job=job, profile={}, resume_text_for_consistency=None,
    )

    assert len(calls) == 2
    assert calls[1] > calls[0]  # escalated to the larger tier
    assert errors == {}


def test_draft_group_job_json_is_uncached_and_placed_after_the_cached_profile_block(monkeypatch):
    # Core cache-fix assertion: the cache_control marker must stay only on
    # the job-invariant shared_context block, and job-specific text must
    # never be folded into that same cached block, or cross-job cache
    # reuse (the whole point of this fix) silently breaks again.
    import tailoring.drafting as drafting

    captured = {}

    def _fake_call_structured(client, **kwargs):
        captured["user_content"] = kwargs["user_content"]
        return {"cover_letter": "text", "exec_bio": "text"}

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    shared_context = [{"type": "text", "text": "CANDIDATE'S MASTER PROFILE:\n{}", "cache_control": {"type": "ephemeral"}}]
    job = {"source": "linkedin", "job_id": "1", "title": "A Very Specific Job Title"}
    indices = {"cover_letter": 2, "exec_bio": 3}

    _draft_group(
        object(), shared_context, ["cover_letter", "exec_bio"], None, None, indices, 3,
        job=job, profile={}, resume_text_for_consistency=None,
    )

    blocks = captured["user_content"]
    cached_blocks = [b for b in blocks if b.get("cache_control")]
    assert len(cached_blocks) == 1
    assert "A Very Specific Job Title" not in cached_blocks[0]["text"]
    job_blocks = [b for b in blocks if "A Very Specific Job Title" in b["text"]]
    assert len(job_blocks) == 1
    assert "cache_control" not in job_blocks[0]
    # The job block must come after the cached profile block in the
    # list, per Anthropic's own prefix-cache ordering requirement.
    assert blocks.index(job_blocks[0]) > blocks.index(cached_blocks[0])


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


def _profile_with_education(*entries):
    return {"education": list(entries)}


def test_render_education_section_verbatim_replaces_a_freely_reworded_line():
    # Real Merck 4449005464 case, 2026-08-10: the AI prepended "Bachelor's
    # degree - " and invented "(BSc)" that appears nowhere in the real
    # profile, flattening the original's parenthetical grouping. The
    # verbatim render must win regardless of what the AI wrote.
    text = (
        "Zahir Uddin\n\nEDUCATION\n"
        "Bachelor's degree - Bachelor of Science (BSc), Information Systems, "
        "Artificial Intelligence major, Honours, Brunel University London, "
        "Middlesex, UK, 1997 - 2001\n\nCERTIFICATIONS\nPMP\n"
    )
    profile = _profile_with_education({
        "degree": "Bachelor of Science, Information Systems (Artificial Intelligence major, Honours)",
        "institution": "Brunel University London",
        "location": "Middlesex, UK",
        "years": "1997 - 2001",
    })
    result = _render_education_section_verbatim(text, profile)
    assert (
        "Bachelor of Science, Information Systems (Artificial Intelligence major, Honours), "
        "Brunel University London, Middlesex, UK, 1997 - 2001"
    ) in result
    assert "BSc" not in result
    assert "Bachelor's degree -" not in result
    # Content outside the EDUCATION section must survive untouched.
    assert "Zahir Uddin" in result
    assert "CERTIFICATIONS\nPMP" in result


def test_render_education_section_verbatim_preserves_blank_line_spacing():
    text = "EDUCATION\nOld reworded line.\n\nCERTIFICATIONS\nPMP\n"
    profile = _profile_with_education({"degree": "BS", "institution": "Some University"})
    result = _render_education_section_verbatim(text, profile)
    assert result == "EDUCATION\nBS, Some University\n\nCERTIFICATIONS\nPMP"


def test_render_education_section_verbatim_renders_multiple_degrees_each_on_own_line():
    text = "EDUCATION\nSome reworded run-on line covering both degrees.\n\nSKILLS\nPython\n"
    profile = _profile_with_education(
        {"degree": "MBA", "institution": "LBS", "years": "2010 - 2012"},
        {"degree": "BS", "institution": "Brunel", "years": "1997 - 2001"},
    )
    result = _render_education_section_verbatim(text, profile)
    assert "MBA, LBS, 2010 - 2012\nBS, Brunel, 1997 - 2001" in result


def test_render_education_section_verbatim_no_op_with_no_profile_education():
    text = "EDUCATION\nSomething the AI wrote.\n\nSKILLS\nPython\n"
    assert _render_education_section_verbatim(text, {"education": []}) == text
    assert _render_education_section_verbatim(text, None) == text


def test_render_education_section_verbatim_no_op_when_no_education_header_present():
    # Fails safe toward the AI's own text rather than guessing where to
    # insert a section that was never printed at all.
    text = "PROFESSIONAL EXPERIENCE\nEngineer.\n"
    profile = _profile_with_education({"degree": "BS", "institution": "Brunel"})
    assert _render_education_section_verbatim(text, profile) == text


def test_render_education_section_verbatim_skips_an_entry_with_no_real_fields():
    text = "EDUCATION\nOld line.\n"
    profile = _profile_with_education({}, {"degree": "BS", "institution": "Brunel"})
    result = _render_education_section_verbatim(text, profile)
    assert result == "EDUCATION\nBS, Brunel"


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


# --- Pre-flight score verification (feature/basket-badge-verification, 2026-08-17) ---
# Zahir's "hope and pray" complaint: a question's "+N pts" badge used to be
# shown with no guarantee that confirming its suggested_answer would make
# the literal required/preferred keyword phrase actually present anywhere -
# an eventual AI redraft could paraphrase instead of using it. Every
# question this function generates for a real missing keyword must now be
# guaranteed, by construction, to already contain the literal phrase.

def test_merge_keyword_gap_questions_generated_suggested_answer_literally_contains_the_keyword_no_profile_signal():
    # The "no real basis at all" branch (candidate's profile never mentions
    # the term anywhere) - historically just said "Unknown - please
    # describe..." with no mention of the actual term at all.
    merged = _merge_keyword_gap_questions([], _missing("shop-floor systems"))
    assert len(merged) == 1
    q = merged[0]
    assert keyword_literally_present("shop-floor systems", q["suggested_answer"])
    assert q["keyword_verified"] is True
    # Never fabricates a claim the candidate has it - still an honest ask.
    assert "yes" not in q["suggested_answer"].lower()


def test_merge_keyword_gap_questions_generated_suggested_answer_literally_contains_the_keyword_with_profile_signal():
    profile = {"notes": "Owned shop-floor systems integration in 2022."}
    merged = _merge_keyword_gap_questions([], _missing("shop-floor systems"), profile=profile)
    assert len(merged) == 1
    q = merged[0]
    assert keyword_literally_present("shop-floor systems", q["suggested_answer"])
    assert q["keyword_verified"] is True


def test_merge_keyword_gap_questions_preferred_keyword_suggested_answer_is_also_verified():
    merged = _merge_keyword_gap_questions([], [], missing_preferred_keywords=_missing("clinical-stage organizations"))
    assert len(merged) == 1
    q = merged[0]
    assert q["is_preferred"] is True
    assert keyword_literally_present("clinical-stage organizations", q["suggested_answer"])
    assert q["keyword_verified"] is True


def test_merge_keyword_gap_questions_backfilled_ai_question_suggested_answer_gets_patched_and_verified():
    # The AI's own free-form question/suggested_answer, backfilled with a
    # real point_value because it matches a missing keyword by skill label -
    # this is the genuinely at-risk case (real AI text, no guarantee it
    # happened to use the literal phrase the scorer checks for).
    existing = [{
        "type": "skill_gap", "skill": "shop-floor systems", "question": "?",
        "suggested_answer": "Likely yes, based on your SAP and CMO/3PL relationships - can you confirm?",
    }]
    merged = _merge_keyword_gap_questions(existing, _missing("shop-floor systems"))
    assert len(merged) == 1
    q = merged[0]
    assert q["point_value"] == 5.0
    assert q["keyword_verified"] is True
    assert keyword_literally_present("shop-floor systems", q["suggested_answer"])
    # Original AI text preserved, not thrown away - only the literal term
    # was woven in.
    assert "SAP and CMO/3PL relationships" in q["suggested_answer"]


def test_merge_keyword_gap_questions_backfilled_ai_question_already_containing_keyword_is_untouched():
    existing = [{
        "type": "skill_gap", "skill": "shop-floor systems", "question": "?",
        "suggested_answer": 'Yes, I directly built and owned "shop-floor systems" for two plants.',
    }]
    merged = _merge_keyword_gap_questions(existing, _missing("shop-floor systems"))
    assert merged[0]["suggested_answer"] == existing[0]["suggested_answer"]
    assert merged[0]["keyword_verified"] is True


def test_merge_keyword_gap_questions_does_not_set_keyword_verified_when_no_point_value_backfilled():
    # A free-form AI question with no corresponding keyword gap at all -
    # nothing to verify, must not falsely claim a guarantee it never made.
    existing = [{"type": "skill_gap", "skill": "team size", "question": "?", "suggested_answer": "Roughly 8-10?"}]
    merged = _merge_keyword_gap_questions(existing, [])
    assert merged == existing
    assert "keyword_verified" not in merged[0]


def test_merge_keyword_gap_questions_does_not_double_insert_on_a_second_pass():
    # Guards the real duplicate-insertion risk called out in the design:
    # running the same missing keyword through this function twice (e.g.
    # re-merging on a later round) must never append the clause twice.
    first_pass = _merge_keyword_gap_questions([], _missing("shop-floor systems"))
    second_pass = _merge_keyword_gap_questions(first_pass, _missing("shop-floor systems"))
    assert len(second_pass) == 1
    assert second_pass[0]["suggested_answer"].lower().count("shop-floor systems") == 1


def _sample_profile():
    return {
        "skills": {"Technical": ["Databricks", "Python"]},
        "work_history": [
            {"title": "Solutions Architect", "bullets": ["Led stakeholder engagement across 12 business units."]},
        ],
        "client_engagements": [
            {"role": "Consultant", "bullets": ["Owned client engagement for a Fortune 500 rollout."]},
        ],
        "certifications": [{"name": "PMP"}],
        "education": [{"degree": "BS Computer Science"}],
    }


def test_profile_narrative_units_flattens_every_profile_section_as_distinct_entries():
    units = _profile_narrative_units(_sample_profile())
    assert "Databricks" in units
    assert "Solutions Architect" in units
    assert "Led stakeholder engagement across 12 business units." in units
    assert "Owned client engagement for a Fortune 500 rollout." in units
    assert "PMP" in units
    assert "BS Computer Science" in units


def test_profile_narrative_units_handles_a_missing_or_empty_profile():
    assert _profile_narrative_units(None) == []
    assert _profile_narrative_units({}) == []


def test_profile_supports_skill_true_for_a_literal_skills_list_entry():
    assert _profile_supports_skill("Databricks", _sample_profile()) is True


def test_profile_supports_skill_true_for_a_phrase_contained_in_a_bullet():
    assert _profile_supports_skill("client engagement", _sample_profile()) is True


def test_profile_supports_skill_false_for_a_genuinely_absent_term():
    assert _profile_supports_skill("Kubernetes", _sample_profile()) is False


def test_profile_supports_skill_does_not_catch_a_real_synonym_not_stated_literally():
    # Documents the known, accepted limit of this fix (2026-08-10): skills_
    # match() is not a semantic matcher, so a genuinely different word for
    # the same real experience ("customer engagement" vs. the profile's own
    # "client engagement"/"stakeholder engagement") is NOT caught here -
    # that gap is handled by the prompt-level instruction in
    # request_additional_gap_questions instead, not this deterministic check.
    assert _profile_supports_skill("Customer Engagement", _sample_profile()) is False


def test_profile_supports_skill_does_not_false_positive_across_unrelated_bullets():
    # Real false-positive shape this function was designed to avoid: two
    # unrelated bullets that would read as one coincidental compound phrase
    # if ever concatenated into a single blob first.
    profile = {
        "work_history": [
            {"title": "Engineer", "bullets": ["Owned system design.", "Applications for internal tooling."]},
        ],
    }
    assert _profile_supports_skill("design applications", profile) is False


def test_profile_supports_skill_catches_a_fact_only_a_conjunctive_multi_word_match_can_see():
    # Real production incident, 2026-08-17: skills_match()'s per-unit
    # phrase-containment check (the loop this function already ran before
    # this fix) genuinely cannot catch a multi-word term whose words are
    # scattered differently within a real bullet - "onshore/offshore
    # teams" (the AI's proposed label) vs. the real bullet "Led a team of
    # 8 (onshore/offshore)." (singular "team", different word order).
    # skill_evidenced_in_text()'s conjunctive-within-one-unit check closes
    # this without reopening the cross-bullet false positive the sibling
    # test above (test_profile_supports_skill_does_not_false_positive_
    # across_unrelated_bullets) guards against - both must stay true at
    # once, which is exactly what this pair of tests checks.
    profile = {
        "work_history": [
            {"title": "Delivery Director", "bullets": ["Led a team of 8 (onshore/offshore)."]},
        ],
    }
    assert _profile_supports_skill("onshore/offshore teams", profile) is True


def test_profile_supports_skill_catches_a_fact_buried_in_a_gap_answers_free_text():
    # Same real incident: the fact was ALSO confirmed inside the free-text
    # "answer" of a gap_interview_answers entry stored under a completely
    # different "skill" label ("si partner relationships") - previously
    #_answered_skills-style label matching can never see this, since it
    # only ever compares "skill" labels, never answer text.
    profile = {
        "gap_interview_answers": [
            {
                "skill": "si partner relationships",
                "answer": "I coordinated onshore and offshore delivery teams across several engagements.",
            },
        ],
    }
    assert _profile_supports_skill("onshore/offshore teams", profile) is True


def test_merge_keyword_gap_questions_does_not_re_ask_the_real_onshore_offshore_question():
    # End-to-end version of the two tests above, through the actual
    # merge/dedup entry point _finalize_resume_draft() and
    # request_additional_gap_questions() both call.
    profile = {
        "gap_interview_answers": [
            {"skill": "si partner relationships", "answer": "I coordinated onshore and offshore delivery teams."},
        ],
    }
    merged = _merge_keyword_gap_questions(
        [{"type": "skill_gap", "skill": "onshore/offshore teams", "question": "?", "suggested_answer": ""}],
        [], profile=profile,
    )
    assert merged == []


def test_merge_keyword_gap_questions_does_not_ask_about_a_profile_supported_keyword():
    merged = _merge_keyword_gap_questions([], _missing("Databricks"), profile=_sample_profile())
    assert merged == []


def test_merge_keyword_gap_questions_still_asks_about_a_profile_unsupported_keyword():
    merged = _merge_keyword_gap_questions([], _missing("Databricks", "Kubernetes"), profile=_sample_profile())
    assert {q["skill"] for q in merged} == {"Kubernetes"}


def test_merge_keyword_gap_questions_profile_support_also_suppresses_a_preferred_keyword():
    merged = _merge_keyword_gap_questions(
        [], [], profile=_sample_profile(), missing_preferred_keywords=_missing("Databricks"),
    )
    assert merged == []


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


def test_merge_keyword_gap_questions_generates_a_real_question_for_a_missing_preferred_keyword():
    # Real gap General found live 2026-08-09: missing PREFERRED keywords
    # had no question-generation path at all - only required ones did.
    # Real case: "clinical-stage organizations" (a genuine, real fact
    # Zahir has - Protiovant/SK Life Science Labs through its Phase 1
    # transition) sat as passive "How to raise it" text forever with no
    # way to actually surface it.
    merged = _merge_keyword_gap_questions([], [], missing_preferred_keywords=_missing("clinical-stage organizations"))
    assert len(merged) == 1
    q = merged[0]
    assert q["type"] == "skill_gap"
    assert q["skill"] == "clinical-stage organizations"
    assert "clinical-stage organizations" in q["question"]
    assert q["point_value"] == 5.0
    # Distinguishable from a required-keyword question so a caller (UI
    # refinement's call) can render it with lower-urgency framing.
    assert q["is_preferred"] is True


def test_merge_keyword_gap_questions_required_question_is_not_marked_preferred():
    merged = _merge_keyword_gap_questions([], _missing("Databricks"))
    assert "is_preferred" not in merged[0]


def test_merge_keyword_gap_questions_preferred_question_dedupes_against_existing_and_answered():
    existing = [{"skill": "Kubernetes experience", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(
        existing, [], previously_answered_skills=["Terraform"],
        missing_preferred_keywords=_missing("Kubernetes", "Terraform", "Docker"),
    )
    # "Kubernetes" already has a question (backfilled, not duplicated);
    # "Terraform" was already answered; only "Docker" is genuinely new.
    assert len(merged) == 2  # the existing Kubernetes question + the new Docker one
    new_questions = [q for q in merged if q.get("is_preferred")]
    assert {q["skill"] for q in new_questions} == {"Docker"}


def test_merge_keyword_gap_questions_preferred_does_not_duplicate_a_matching_required_gap():
    # Defensive dedup: the same term shouldn't realistically appear in
    # both lists, but if it somehow did, it must not generate two
    # separate questions for the identical skill.
    merged = _merge_keyword_gap_questions(
        [], _missing("Kubernetes"), missing_preferred_keywords=_missing("Kubernetes"),
    )
    assert len(merged) == 1


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


def test_analyze_fit_before_drafting_credits_a_profile_supported_keyword_without_a_new_answer():
    # 2026-08-10 fix: a keyword the candidate's own profile already
    # substantively states (e.g. a skills-list entry) must count toward
    # projected_score and drop off open_questions even with zero gap-
    # interview answers - consistent with _merge_keyword_gap_questions no
    # longer asking about it in the first place.
    from tailoring.ats_score import score_resume_against_keywords

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": ["Python", "Databricks"], "ats_preferred_keywords": []}
    resume_text = "PROFESSIONAL EXPERIENCE\nEngineer.\n\nEDUCATION\nBS\n\nSKILLS\nPython"
    app_record = {"resume_text": resume_text}
    profile = {"gap_interview_answers": [], "skills": {"Technical": ["Databricks"]}}
    baseline = score_resume_against_keywords(job["ats_required_keywords"], [], resume_text)
    databricks_point_value = baseline["missing_required_keywords"][0]["point_value"]

    result = analyze_fit_before_drafting(job, profile, app_record)

    assert result["projected_score"] == round(baseline["ats_score"] + databricks_point_value)
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


def test_extract_ats_keywords_stamps_the_current_extractor_version(monkeypatch):
    # 2026-08-10, real gap found: no version marker existed at all, so a
    # future fix to the extraction/cleanup pipeline had no way to tell
    # which already-cached jobs it should apply to. A successful
    # extraction must stamp the current version alongside the keywords.
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        return {"required_keywords": ["Python"], "preferred_keywords": []}

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)
    monkeypatch.setattr("search.job_store.update_job_ats_keywords", lambda *a, **kw: None)

    job = {"source": "linkedin", "job_id": "1", "title": "Engineer", "description": "Needs Python."}
    drafting._extract_ats_keywords(object(), job, model=None)

    assert job["ats_keywords_extractor_version"] == drafting.ATS_KEYWORDS_EXTRACTOR_VERSION


def test_is_ats_keywords_stale_false_when_never_extracted():
    import tailoring.drafting as drafting

    assert drafting.is_ats_keywords_stale({"source": "linkedin", "job_id": "1"}) is False


def test_is_ats_keywords_stale_false_when_current_version():
    import tailoring.drafting as drafting

    job = {"ats_required_keywords": ["Python"], "ats_keywords_extractor_version": drafting.ATS_KEYWORDS_EXTRACTOR_VERSION}
    assert drafting.is_ats_keywords_stale(job) is False


def test_is_ats_keywords_stale_true_when_cached_under_an_older_or_missing_version():
    import tailoring.drafting as drafting

    # Cached before this feature existed at all - no version field.
    assert drafting.is_ats_keywords_stale({"ats_required_keywords": ["Python"]}) is True
    # Cached under an explicitly older version number.
    stale_job = {"ats_required_keywords": ["Python"], "ats_keywords_extractor_version": drafting.ATS_KEYWORDS_EXTRACTOR_VERSION - 1}
    assert drafting.is_ats_keywords_stale(stale_job) is True


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


def test_total_years_of_experience_uses_earliest_work_history_start_date():
    # Real profile shape (Zahir's actual master_profile.json): "start" is
    # either a bare "YYYY" or "MM/YYYY" - both must parse. Uses the
    # EARLIEST start across every entry, not just the first one in the
    # list, since work_history isn't guaranteed to be sorted.
    from datetime import date

    profile = {
        "work_history": [
            {"employer": "A", "start": "09/2010", "end": "01/2013"},
            {"employer": "B", "start": "2001", "end": "02/2008"},  # earliest
            {"employer": "C", "start": "01/2024", "end": "01/2026"},
        ],
    }
    years = _total_years_of_experience(profile)
    expected = (date.today() - date(2001, 1, 1)).days / 365.25
    assert years == expected


def test_total_years_of_experience_none_when_no_parseable_dates():
    assert _total_years_of_experience({"work_history": []}) is None
    assert _total_years_of_experience({"work_history": [{"employer": "A", "start": None}]}) is None
    assert _total_years_of_experience({"work_history": [{"employer": "A", "start": "not a date"}]}) is None
    assert _total_years_of_experience({}) is None


def test_years_of_experience_equivalency_flows_through_rescore_against_cached_keywords():
    # End-to-end wiring check: rescore_against_cached_keywords (the free,
    # no-AI-call path) must actually pass the derived years-of-experience
    # through to score_resume_against_keywords, not just have the
    # underlying scoring function support it in isolation.
    import tailoring.drafting as drafting

    job = {"ats_required_keywords": [], "ats_preferred_keywords": ["advanced degree"]}
    app_record = {
        "resume_text": "PROFESSIONAL EXPERIENCE\nEngineer.\n\nSKILLS\nPython",
        "resume_clarifying_questions": [],
    }
    profile = {"work_history": [{"employer": "A", "start": "1995", "end": "01/2026"}], "gap_interview_answers": []}

    result = drafting.rescore_against_cached_keywords(job, app_record, profile)

    assert "credited via" in result["ats_rationale"]


# --- Self-correcting Generate loop (2026-08-09) ---
# Zahir's explicit design, confirmed with General: he had to click
# "Generate" 5 separate times for one real job to get an acceptable
# resume. _draft_resume_with_self_correction() makes this a bounded,
# self-correcting loop inside a single click instead - see that
# function's own docstring for the full design (target score, stop
# conditions, regression guard).

_SELF_CORRECTION_JOB = {
    "source": "linkedin", "job_id": "1",
    "ats_required_keywords": ["Python", "Kubernetes"], "ats_preferred_keywords": [],
}


def _self_correction_response(text, unconfirmed_claims=None):
    return {
        "text": text, "target_seniority_at_least_vp": True, "suggested_strategy_tag": "",
        "clarifying_questions": [], "unconfirmed_claims": unconfirmed_claims or [],
    }


def _full_resume(skills_line: str) -> str:
    # Includes contact info + a date mention so structure_score hits 100%
    # - isolates these tests to keyword coverage specifically, rather
    # than incidentally failing on unrelated formatting checks
    # (ats_score._structure_score also checks headers/markdown/dates/
    # contact, not just keyword overlap). Deliberately does NOT use a
    # role-header-shaped "Employer - Title  Date - Date" line - these
    # tests pass an empty profile ({}), so claim_verification.py's
    # unhedged-claim check would correctly flag any such line as an
    # unverifiable employer claim (no work_history to trace it to),
    # appending a "?" that would break these tests' own literal-text
    # comparisons - a plain bullet mentioning a date avoids that
    # entirely without weakening the structure-score check.
    return (
        "JANE DOE\njane@example.com\n\nPROFESSIONAL EXPERIENCE\n"
        "Engineer\n- Built things starting Jan 2020.\n\n"
        f"SKILLS\n{skills_line}"
    )


def test_self_correction_returns_immediately_when_the_first_attempt_already_hits_target(monkeypatch):
    import tailoring.drafting as drafting

    calls = []

    def _fake_call_structured(client, **kwargs):
        calls.append(kwargs)
        return _self_correction_response(_full_resume("Python, Kubernetes"))

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    result = drafting._draft_resume_with_self_correction(
        object(), [], None, None, 1, 1, _SELF_CORRECTION_JOB, {},
    )

    assert len(calls) == 1  # no retry needed
    assert result["ats_score"] == 100
    assert result["self_correction_attempts"] == 1
    assert "self_correction_note" not in result


def test_self_correction_retries_and_closes_a_real_gap(monkeypatch):
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        user_text = " ".join(b.get("text", "") for b in kwargs["user_content"] if isinstance(b, dict))
        if "SELF-CORRECTION PASS" in user_text:
            return _self_correction_response(_full_resume("Python, Kubernetes"))
        return _self_correction_response(_full_resume("Python"))

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    progress_calls = []
    result = drafting._draft_resume_with_self_correction(
        object(), [], None, lambda *a: progress_calls.append(a), 1, 1, _SELF_CORRECTION_JOB, {},
    )

    assert result["ats_score"] == 100
    assert result["self_correction_attempts"] == 2
    assert "self_correction_note" not in result
    # Progress reporting (2026-08-09 design): a "Refining..." substatus
    # flows through the SAME existing on_progress callback, no new UI.
    assert any("Refining resume" in str(call) for call in progress_calls)


def test_self_correction_stops_when_no_progress_is_made(monkeypatch):
    # Real, permanent-looking gap (same class as "advanced degree" before
    # the years-of-experience equivalency fix) - every retry returns the
    # exact same text, so the missing-keyword set never changes. Must
    # stop before the hard attempt cap, not burn every attempt pointlessly.
    import tailoring.drafting as drafting

    calls = []

    def _fake_call_structured(client, **kwargs):
        calls.append(kwargs)
        return _self_correction_response(_full_resume("Python"))

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    result = drafting._draft_resume_with_self_correction(
        object(), [], None, None, 1, 1, _SELF_CORRECTION_JOB, {},
    )

    assert len(calls) == 2  # initial + exactly one retry, not the full 3-attempt cap
    assert result["ats_score"] < 90
    assert result["self_correction_attempts"] == 2
    assert "Kubernetes" in result["self_correction_note"]
    assert "real ceiling" in result["self_correction_note"]
    assert "Kubernetes" in result["ats_rationale"]  # folded in for the existing "Why this score" display


def test_self_correction_rejects_a_retry_that_regresses_a_previously_matched_keyword(monkeypatch):
    # Real, live-reproduced failure mode (CLAUDE.md known failure pattern
    # #2): a redraft can fix one requirement while silently dropping
    # another. The self-correction loop must not accept that trade - keep
    # the previous attempt and stop, rather than risk repeating it.
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        user_text = " ".join(b.get("text", "") for b in kwargs["user_content"] if isinstance(b, dict))
        if "SELF-CORRECTION PASS" in user_text:
            # Fixes Kubernetes but drops Python - a real regression.
            return _self_correction_response(_full_resume("Kubernetes"))
        return _self_correction_response(_full_resume("Python"))

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    result = drafting._draft_resume_with_self_correction(
        object(), [], None, None, 1, 1, _SELF_CORRECTION_JOB, {},
    )

    assert result["text"] == _full_resume("Python")  # the REJECTED candidate's text must not win
    assert result["self_correction_attempts"] == 2
    assert "traded away" in result["self_correction_note"]
    assert "Python" in result["self_correction_note"]


def test_self_correction_stops_at_the_hard_attempt_cap_even_while_still_improving(monkeypatch):
    # Progress IS being made each time (a different keyword closes per
    # attempt), but three real requirements are missing and the cap is 3
    # total attempts - must stop there, not loop unbounded chasing 90.
    import tailoring.drafting as drafting

    job = {
        "source": "linkedin", "job_id": "1",
        "ats_required_keywords": ["Python", "Kubernetes", "Terraform", "Docker"], "ats_preferred_keywords": [],
    }
    call_count = [0]

    def _fake_call_structured(client, **kwargs):
        call_count[0] += 1
        # Each attempt closes exactly one more keyword than the last,
        # never enough to reach the 90 target within the attempt cap.
        skills = ["Python", "Kubernetes", "Terraform"][: call_count[0]]
        return _self_correction_response(_full_resume(", ".join(skills)))

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    result = drafting._draft_resume_with_self_correction(object(), [], None, None, 1, 1, job, {})

    assert call_count[0] == drafting._RESUME_SELF_CORRECTION_MAX_ATTEMPTS
    assert result["self_correction_attempts"] == drafting._RESUME_SELF_CORRECTION_MAX_ATTEMPTS
    assert "attempt limit" in result["self_correction_note"]
    assert "Docker" in result["self_correction_note"]  # the one requirement never closed


def test_self_correction_retry_prompt_includes_previous_text_and_missing_keywords_with_point_values(monkeypatch):
    import tailoring.drafting as drafting

    captured_retry_content = []

    def _fake_call_structured(client, **kwargs):
        user_text_blocks = [b.get("text", "") for b in kwargs["user_content"] if isinstance(b, dict)]
        combined = " ".join(user_text_blocks)
        if "SELF-CORRECTION PASS" in combined:
            captured_retry_content.append(combined)
            return _self_correction_response(_full_resume("Python, Kubernetes"))
        return _self_correction_response(_full_resume("Python"))

    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    drafting._draft_resume_with_self_correction(object(), [], None, None, 1, 1, _SELF_CORRECTION_JOB, {})

    assert len(captured_retry_content) == 1
    retry_text = captured_retry_content[0]
    assert "Kubernetes" in retry_text
    assert "PREVIOUS ATTEMPT" in retry_text
    assert _full_resume("Python") in retry_text  # the actual previous draft, verbatim
    assert "do not fabricate" in retry_text.lower()


def test_generate_documents_routes_resume_through_self_correction(monkeypatch):
    # Integration check: generate_documents() must dispatch doc_key=="resume"
    # through the self-correction wrapper, not the plain single-shot path.
    import tailoring.drafting as drafting

    def _fake_call_structured(client, **kwargs):
        return _self_correction_response(_full_resume("Python, Kubernetes"))

    monkeypatch.setattr(drafting, "_client", lambda: object())
    monkeypatch.setattr(drafting, "call_structured", _fake_call_structured)

    result = drafting.generate_documents(_SELF_CORRECTION_JOB, {}, ["resume"])

    assert result["resume"]["ats_score"] == 100
    assert "self_correction_attempts" in result["resume"]


# --- Auto-fire free-form gap scan: fingerprint helpers (2026-08-09) ---

def test_gap_scan_baseline_fingerprint_uses_the_current_resume_text():
    import tailoring.drafting as drafting

    job = {"source": "linkedin", "job_id": "1", "ats_required_keywords": [], "ats_preferred_keywords": []}
    fp1 = drafting.gap_scan_baseline_fingerprint(job, {"resume_text": "SKILLS\nPython"})
    fp2 = drafting.gap_scan_baseline_fingerprint(job, {"resume_text": "SKILLS\nPython"})
    fp3 = drafting.gap_scan_baseline_fingerprint(job, {"resume_text": "SKILLS\nRust"})

    assert fp1 == fp2  # same text -> same fingerprint, deterministic
    assert fp1 != fp3  # different text -> different fingerprint


def test_gap_scan_baseline_fingerprint_falls_back_to_the_baseline_resume_when_no_draft_yet(monkeypatch):
    # Matches analyze_fit_before_drafting()'s own pre-first-draft fallback
    # - a job with no resume_text yet still gets a real, non-empty
    # fingerprint tied to whatever baseline text would actually be
    # compared against, not a constant "no resume" sentinel.
    import tailoring.drafting as drafting

    monkeypatch.setattr(drafting, "select_baseline_resume_text", lambda job: ("SKILLS\nJava", "a similar past resume"))
    job = {"source": "linkedin", "job_id": "1"}

    fp = drafting.gap_scan_baseline_fingerprint(job, {})

    assert fp == drafting.gap_scan_baseline_fingerprint(job, {"resume_text": "SKILLS\nJava"})


def test_gap_scan_is_current_true_only_when_the_stored_fingerprint_matches():
    import tailoring.drafting as drafting

    job = {"source": "linkedin", "job_id": "1"}
    app_record = {"resume_text": "SKILLS\nPython"}

    assert drafting.gap_scan_is_current(job, app_record) is False  # nothing stored yet

    app_record["resume_gap_scan_fingerprint"] = drafting.gap_scan_baseline_fingerprint(job, app_record)
    assert drafting.gap_scan_is_current(job, app_record) is True

    app_record["resume_text"] = "SKILLS\nRust"  # a fresh Generate changed the text
    assert drafting.gap_scan_is_current(job, app_record) is False
