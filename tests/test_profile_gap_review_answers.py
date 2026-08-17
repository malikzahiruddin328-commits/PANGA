"""profile.interview.save_profile_gap_review_answers() (2026-08-17,
feature/jd-keyword-taxonomy-gaps, Phase 3) - the job-agnostic gap-answer
save path for the standalone "Review recurring profile gaps" panel. No AI
calls - resolve_or_create_canonical_id() is deterministic."""

from profile.interview import PROFILE_GAP_REVIEW_ROLE_CONTEXT, save_profile_gap_review_answers
from profile.storage import load_profile
from skills.canonical_taxonomy import save_taxonomy


def test_saves_a_real_answer_with_the_fixed_role_context(isolated_data):
    save_taxonomy({"_meta": {}})
    save_profile_gap_review_answers([{"skill": "Kubernetes", "answer": "Yes, ran a 40-node cluster.", "question": "Do you have real experience with Kubernetes?"}])

    profile = load_profile()
    answers = profile["gap_interview_answers"]
    assert len(answers) == 1
    assert answers[0]["skill"] == "Kubernetes"
    assert answers[0]["answer"] == "Yes, ran a 40-node cluster."
    assert answers[0]["role_context"] == PROFILE_GAP_REVIEW_ROLE_CONTEXT
    assert answers[0]["is_disqualifier"] is False
    assert answers[0]["canonical_skill_id"]


def test_resolves_to_the_existing_canonical_id_for_a_confirmed_gap(isolated_data):
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": []}]})
    save_profile_gap_review_answers([{"skill": "SAP S/4HANA", "answer": "10 years.", "question": "q?"}])

    profile = load_profile()
    assert profile["gap_interview_answers"][0]["canonical_skill_id"] == "sap"


def test_creates_a_new_taxonomy_entry_for_a_genuinely_new_concept(isolated_data):
    save_taxonomy({"_meta": {}})
    save_profile_gap_review_answers([{"skill": "Workday HCM ownership", "answer": "Yes.", "question": "q?"}])

    from skills.canonical_taxonomy import load_taxonomy
    taxonomy = load_taxonomy()
    all_labels = [e["canonical_label"] for entries in taxonomy.values() if isinstance(entries, list) for e in entries]
    assert "Workday HCM ownership" in all_labels


def test_blank_answers_are_never_saved(isolated_data):
    save_taxonomy({"_meta": {}})
    save_profile_gap_review_answers([{"skill": "Kubernetes", "answer": "   ", "question": "q?"}])

    profile = load_profile()
    assert profile.get("gap_interview_answers", []) == []


def test_reanswering_the_same_skill_updates_in_place_not_duplicates(isolated_data):
    save_taxonomy({"_meta": {}})
    save_profile_gap_review_answers([{"skill": "Kubernetes", "answer": "First answer.", "question": "q?"}])
    save_profile_gap_review_answers([{"skill": "Kubernetes", "answer": "Updated answer.", "question": "q?"}])

    profile = load_profile()
    assert len(profile["gap_interview_answers"]) == 1
    assert profile["gap_interview_answers"][0]["answer"] == "Updated answer."
