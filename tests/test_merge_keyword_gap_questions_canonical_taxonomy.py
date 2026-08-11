"""Real test proving _merge_keyword_gap_questions' new canonical-taxonomy
fallback catches a real repeat-question case skills_match() alone cannot -
the exact problem Zahir hit across 8 real days of use (the same real fact
worded differently round to round, e.g. "SS&C platform experience" vs
"SS&C administration experience")."""

from tailoring.drafting import _merge_keyword_gap_questions
from skills.canonical_taxonomy import add_canonical_entry, save_taxonomy, load_taxonomy


def _missing(*labels):
    return [{"label": label, "point_value": 5.0} for label in labels]


def test_merge_dedupes_against_previously_answered_via_canonical_id_when_skills_match_alone_would_miss(isolated_data):
    # Real pair from the taxonomy audit that skills_match() genuinely
    # cannot catch (no shared vocabulary at all).
    taxonomy = load_taxonomy()
    add_canonical_entry(
        taxonomy, "Consulting Delivery", "Client satisfaction scores on consulting engagements",
        aliases=["CSAT/NPS numeric scores on consulting engagements", "Customer satisfaction scores on consulting engagements"],
    )
    save_taxonomy(taxonomy)

    merged = _merge_keyword_gap_questions(
        [], _missing("Customer satisfaction scores on consulting engagements"),
        previously_answered_skills=["CSAT/NPS numeric scores on consulting engagements"],
    )

    # Without the canonical-id fallback this would come back re-asking the
    # same real fact under new wording - the exact bug Zahir reported.
    assert merged == []


def test_merge_still_falls_back_to_plain_skills_match_when_taxonomy_has_no_entry(isolated_data):
    # Confirms the fallback is additive, not a replacement - with an
    # empty taxonomy this behaves exactly as before.
    merged = _merge_keyword_gap_questions([], _missing("Databricks"), previously_answered_skills=["Databricks"])
    assert merged == []
