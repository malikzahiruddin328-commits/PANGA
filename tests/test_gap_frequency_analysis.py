"""skills.gap_frequency_analysis (2026-08-17, feature/jd-keyword-taxonomy-
gaps, Phase 2) - pure Python, zero-AI-cost frequency tally + taxonomy
match over real extracted job keywords. No AI calls anywhere in this
module or these tests."""

from profile.storage import load_profile, save_profile
from search.job_store import save_jobs
from skills.canonical_taxonomy import load_taxonomy, save_taxonomy
from skills.gap_frequency_analysis import DEFAULT_MIN_RECURRENCE, analyze_recurring_gaps, build_review_questions


def _job(job_id, required=None, preferred=None, **extra):
    return {
        "source": "linkedin", "job_id": job_id, "title": f"Role {job_id}", "organization": f"Org {job_id}",
        "ats_required_keywords": required if required is not None else [],
        "ats_preferred_keywords": preferred if preferred is not None else [],
        **extra,
    }


def test_default_min_recurrence_is_three():
    assert DEFAULT_MIN_RECURRENCE == 3


def test_confirmed_gap_requires_the_recurrence_threshold(isolated_data):
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": []}]})
    save_jobs([
        _job("1", required=["SAP S/4HANA"]),
        _job("2", required=["SAP S/4HANA"]),
    ], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()

    assert analysis["confirmed_gaps"] == []  # only 2 distinct postings, threshold is 3


def test_confirmed_gap_appears_once_threshold_met_and_not_already_answered(isolated_data):
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": []}]})
    save_jobs([
        _job("1", required=["SAP S/4HANA"]),
        _job("2", required=["SAP S/4HANA"]),
        _job("3", preferred=["SAP S/4HANA"]),
    ], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()

    assert len(analysis["confirmed_gaps"]) == 1
    gap = analysis["confirmed_gaps"][0]
    assert gap["canonical_skill_id"] == "sap"
    assert gap["job_count"] == 3
    assert len(gap["examples"]) == 3


def test_confirmed_gap_is_excluded_once_already_confirmed_in_profile(isolated_data):
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": []}]})
    save_jobs([_job(str(i), required=["SAP S/4HANA"]) for i in range(3)], apply_exclusion=False, review_required=False)
    save_profile({"gap_interview_answers": [{"skill": "SAP S/4HANA", "canonical_skill_id": "sap", "answer": "Yes, 5 years."}]})

    analysis = analyze_recurring_gaps()

    assert analysis["confirmed_gaps"] == []


def test_same_job_counts_once_even_if_term_appears_in_both_required_and_preferred(isolated_data):
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": []}]})
    save_jobs([
        _job("1", required=["SAP S/4HANA"], preferred=["SAP S/4HANA"]),
        _job("2", required=["SAP S/4HANA"]),
        _job("3", required=["SAP S/4HANA"]),
    ], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()

    assert analysis["confirmed_gaps"][0]["job_count"] == 3


def test_new_concept_candidate_for_unmatched_recurring_term(isolated_data):
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": []}]})
    save_jobs([_job(str(i), required=["Workday HCM ownership"]) for i in range(3)], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()

    assert analysis["confirmed_gaps"] == []
    assert len(analysis["new_concept_candidates"]) == 1
    candidate = analysis["new_concept_candidates"][0]
    assert candidate["label"] == "Workday HCM ownership"
    assert candidate["job_count"] == 3


def test_any_of_alternatives_counted_as_independent_terms(isolated_data):
    save_taxonomy({"_meta": {}})
    save_jobs([
        _job(str(i), required=[{"any_of": ["Computer Science", "Business"]}])
        for i in range(3)
    ], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()

    labels = {c["label"] for c in analysis["new_concept_candidates"]}
    assert labels == {"Computer Science", "Business"}


def test_jobs_without_extracted_keywords_are_excluded_from_the_tally(isolated_data):
    save_taxonomy({"_meta": {}})
    jobs_with_none = [{"source": "linkedin", "job_id": str(i), "title": "x", "organization": "y"} for i in range(5)]
    save_jobs(jobs_with_none, apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()

    assert analysis["jobs_with_keywords"] == 0
    assert analysis["total_jobs"] == 5
    assert analysis["confirmed_gaps"] == []
    assert analysis["new_concept_candidates"] == []


def test_min_recurrence_is_overridable(isolated_data):
    save_taxonomy({"_meta": {}})
    save_jobs([_job(str(i), required=["Workday HCM ownership"]) for i in range(2)], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps(min_recurrence=2)

    assert len(analysis["new_concept_candidates"]) == 1


def test_build_review_questions_uses_canonical_label_for_confirmed_gaps(isolated_data):
    save_taxonomy({"ERP": [{"id": "sap", "canonical_label": "SAP S/4HANA", "aliases": ["S4HANA"]}]})
    save_jobs([_job(str(i), required=["S4HANA"]) for i in range(3)], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()
    questions = build_review_questions(analysis)

    assert len(questions) == 1
    q = questions[0]
    assert q["skill"] == "SAP S/4HANA"  # canonical label, not the raw alias that matched
    assert q["type"] == "confirmed_gap"
    assert "SAP S/4HANA" in q["question"]
    assert "3" in q["question"]


def test_build_review_questions_uses_raw_term_for_new_concepts(isolated_data):
    save_taxonomy({"_meta": {}})
    save_jobs([_job(str(i), required=["Workday HCM ownership"]) for i in range(3)], apply_exclusion=False, review_required=False)

    analysis = analyze_recurring_gaps()
    questions = build_review_questions(analysis)

    assert questions[0]["skill"] == "Workday HCM ownership"
    assert questions[0]["type"] == "new_concept"
    assert questions[0]["canonical_skill_id"] is None
