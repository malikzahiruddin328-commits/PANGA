"""Real tests for the canonical-taxonomy wiring in profile/interview.py
(2026-08-11) - every gap answer must get a real canonical_skill_id, and
_already_answered() must catch real same-fact matches that plain
skills_match() alone would miss."""

from profile.interview import _already_answered, save_answer
from profile.storage import load_profile
from skills.canonical_taxonomy import load_taxonomy


def test_save_answer_stamps_a_real_canonical_skill_id(isolated_data):
    save_answer(skill="Databricks", role_context="Director at Acme", answer="Yes, 3 years.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert answers[0].get("canonical_skill_id")  # a real id, not blank/missing


def test_save_answer_never_overwrites_the_original_free_text_skill_field(isolated_data):
    save_answer(skill="Databricks hands-on exposure", role_context="X", answer="Yes.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert answers[0]["skill"] == "Databricks hands-on exposure"  # exact original text, untouched


def test_save_answer_reuses_the_same_canonical_id_for_a_skills_match_caught_variant(isolated_data):
    # "5+ years salesforce leadership" vs "5+ years Salesforce leadership" -
    # a real pair from tonight's audit that skills_match() DOES catch
    # (normalized-equality) - both rounds must land on the same canonical id.
    save_answer(skill="5+ years salesforce leadership", role_context="X", answer="Yes.", date_captured="2026-08-01")
    save_answer(skill="5+ years Salesforce leadership", role_context="Y", answer="Confirmed again.", date_captured="2026-08-06")

    answers = load_profile()["gap_interview_answers"]
    assert len(answers) == 1  # existing skills_match()-based update-in-place still works
    assert answers[0]["canonical_skill_id"]


def test_already_answered_catches_a_real_variant_skills_match_alone_would_miss(isolated_data):
    # Real pair from tonight's audit skills_match() CANNOT catch on its own
    # (no shared substring) - "CSAT/NPS numeric scores on consulting
    # engagements" vs "Customer satisfaction scores on consulting
    # engagements". Once the taxonomy has been told (via an explicit
    # canonical entry) that these are the same real concept, the
    # canonical-id path in _already_answered() must catch it even though
    # skills_match() itself never would.
    from skills.canonical_taxonomy import add_canonical_entry, save_taxonomy

    taxonomy = load_taxonomy()
    add_canonical_entry(
        taxonomy, "Consulting Delivery", "Client satisfaction scores on consulting engagements",
        aliases=["CSAT/NPS numeric scores on consulting engagements", "Customer satisfaction scores on consulting engagements"],
    )
    save_taxonomy(taxonomy)

    save_answer(skill="CSAT/NPS numeric scores on consulting engagements", role_context="X", answer="9.2 average.", date_captured="2026-08-01")

    assert _already_answered("Customer satisfaction scores on consulting engagements") is True


def test_already_answered_still_uses_skills_match_for_legacy_entries_with_no_canonical_id(isolated_data):
    # A pre-migration entry with no canonical_skill_id at all must still
    # be caught via the existing skills_match() fallback - real coverage
    # never narrows versus before this change.
    from profile.storage import load_profile as _load, save_profile as _save

    profile = _load()
    profile.setdefault("gap_interview_answers", []).append({
        "skill": "Databricks", "role_context": "X", "answer": "Yes.", "date_captured": "2026-08-01",
        "is_disqualifier": False, "question": "",
    })
    _save(profile)

    assert _already_answered("databricks,") is True  # same case/punctuation-insensitive check as before
