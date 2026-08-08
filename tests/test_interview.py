from profile.interview import save_answer
from profile.storage import load_profile


def test_save_answer_creates_a_new_entry(isolated_data):
    save_answer(skill="Databricks", role_context="Director at Acme", answer="Yes, 3 years.", date_captured="2026-08-06")
    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["skill"] == "Databricks"
    assert answers[0]["answer"] == "Yes, 3 years."


def test_save_answer_stores_the_original_question_text(isolated_data):
    save_answer(
        skill="Databricks", role_context="Director at Acme", answer="Yes, 3 years.",
        date_captured="2026-08-06", question="Do you have real experience with Databricks?",
    )
    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["question"] == "Do you have real experience with Databricks?"


def test_save_answer_updates_existing_entry_in_place_rather_than_duplicating(isolated_data):
    # Real bug fixed 2026-08-06: answering the same skill_gap question
    # again across rounds used to silently pile up duplicate, potentially
    # conflicting entries instead of reflecting the current, latest answer.
    save_answer(skill="Databricks", role_context="Director at Acme", answer="Not sure.", date_captured="2026-08-01")
    save_answer(skill="Databricks", role_context="Director at Beta", answer="Yes, 3 years.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["answer"] == "Yes, 3 years."
    assert answers[0]["role_context"] == "Director at Beta"
    assert answers[0]["date_captured"] == "2026-08-06"


def test_save_answer_does_not_touch_a_different_skills_entry(isolated_data):
    save_answer(skill="Databricks", role_context="Director at Acme", answer="Yes.", date_captured="2026-08-01")
    save_answer(skill="Kubernetes", role_context="Director at Acme", answer="Yes.", date_captured="2026-08-01")
    save_answer(skill="Databricks", role_context="Director at Acme", answer="Updated answer.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 2
    by_skill = {a["skill"]: a for a in answers}
    assert by_skill["Databricks"]["answer"] == "Updated answer."
    assert by_skill["Kubernetes"]["answer"] == "Yes."


def test_save_answer_preserves_is_disqualifier_flag_on_update(isolated_data):
    save_answer(skill="CISO roles", role_context="X", answer="Exclude these.", date_captured="2026-08-01", is_disqualifier=True)
    save_answer(skill="CISO roles", role_context="X", answer="Exclude these, confirmed again.", date_captured="2026-08-06", is_disqualifier=True)

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["is_disqualifier"] is True


def test_save_answer_dedup_is_case_and_punctuation_insensitive(isolated_data):
    # Real gap flagged by Mirror 2026-08-08: exact-string dedup against a
    # free-text, AI-generated skill label silently fails on the most
    # trivial phrasing drift ("Databricks" vs "databricks,"), let alone a
    # genuinely different round's wording - see skill_label_match.py.
    save_answer(skill="Databricks", role_context="Director at Acme", answer="Not sure.", date_captured="2026-08-01")
    save_answer(skill="databricks,", role_context="Director at Beta", answer="Yes, 3 years.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["answer"] == "Yes, 3 years."


def test_save_answer_dedup_matches_a_label_that_is_a_real_phrase_within_another(isolated_data):
    save_answer(skill="Databricks certification", role_context="X", answer="No.", date_captured="2026-08-01")
    save_answer(skill="Databricks", role_context="X", answer="Yes, 3 years.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["answer"] == "Yes, 3 years."


def test_save_answer_dedup_does_not_false_match_on_a_bare_substring(isolated_data):
    # "IT" must not match "Credit risk modeling" just because the letters
    # "it" appear inside an unrelated word - same class of bug as this
    # week's ats_score.py "it"-pronoun fix.
    save_answer(skill="IT", role_context="X", answer="Yes.", date_captured="2026-08-01")
    save_answer(skill="Credit risk modeling", role_context="X", answer="No.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 2
