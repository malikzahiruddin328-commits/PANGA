from tailoring.drafting import _merge_keyword_gap_questions, _questions_worth_asking, save_gap_answers


def test_maxed_score_suppresses_clarifying_questions():
    questions = [{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    assert _questions_worth_asking(questions, 100) == []


def test_below_max_score_keeps_clarifying_questions():
    questions = [{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    assert _questions_worth_asking(questions, 99) == questions


def test_empty_questions_stay_empty_regardless_of_score():
    assert _questions_worth_asking([], 42) == []
    assert _questions_worth_asking([], 100) == []


def test_merge_keyword_gap_questions_adds_a_real_skill_gap_question():
    # 2026-08-06: missing required keywords used to sit as inert
    # ats_next_actions bullet text - now they become real, answerable
    # clarifying_questions, same shape/mechanism as every other one.
    merged = _merge_keyword_gap_questions([], ["Databricks"])
    assert len(merged) == 1
    q = merged[0]
    assert q["type"] == "skill_gap"
    assert q["skill"] == "Databricks"
    assert "Databricks" in q["question"]
    # No fabricated guess - there's no honest basis to hedge whether the
    # candidate has a specific named skill, unlike a number/scope question.
    assert q["suggested_answer"] == ""


def test_merge_keyword_gap_questions_adds_one_per_missing_keyword():
    merged = _merge_keyword_gap_questions([], ["Databricks", "Kubernetes"])
    assert {q["skill"] for q in merged} == {"Databricks", "Kubernetes"}


def test_merge_keyword_gap_questions_dedupes_against_existing_question_by_skill():
    existing = [{"skill": "Databricks experience", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(existing, ["Databricks"])
    # The AI already asked about this same skill in its own words - must
    # not show up twice under two different phrasings.
    assert len(merged) == 1
    assert merged[0] is existing[0]


def test_merge_keyword_gap_questions_dedup_is_case_insensitive():
    existing = [{"skill": "databricks", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    merged = _merge_keyword_gap_questions(existing, ["Databricks"])
    assert len(merged) == 1


def test_merge_keyword_gap_questions_preserves_existing_questions():
    existing = [{"skill": "SK Life Science team size", "type": "skill_gap", "question": "?", "suggested_answer": "8-10?"}]
    merged = _merge_keyword_gap_questions(existing, ["Databricks"])
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
    merged = _merge_keyword_gap_questions([], ["Databricks"], previously_answered_skills=["Databricks"])
    assert merged == []


def test_merge_keyword_gap_questions_previously_answered_dedup_is_case_insensitive_and_partial():
    merged = _merge_keyword_gap_questions([], ["Databricks"], previously_answered_skills=["databricks certification"])
    assert merged == []


def test_merge_keyword_gap_questions_still_asks_about_a_different_unanswered_skill():
    merged = _merge_keyword_gap_questions([], ["Databricks", "Terraform"], previously_answered_skills=["Databricks"])
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
