"""Feature 2 (title-cluster keyword-coverage sharing, 2026-08-17) boundary
tests: proves the two halves of its explicit design contract hold in code,
not just in a docstring's claim.

1. Cluster credit genuinely suppresses a duplicate QUESTION for a second
   job in the same cluster once a fact is confirmed on the first.
2. Cluster credit NEVER changes a real, per-job ats_score. ats_score.py's
   scoring functions (score_resume_against_keywords, keyword_literally_
   present, ensure_keyword_literally_present - Feature 1, already merged)
   take no title-cluster input at all and never import title_cluster_
   profiles - this is verified structurally here (a cluster profile
   confirming a keyword does not change score_resume_against_keywords'
   real, literal output for a resume that doesn't contain it) rather than
   just asserted in a comment.
"""

import yaml

import search.title_cluster as title_cluster
from tailoring.ats_score import score_resume_against_keywords
from tailoring.drafting import _merge_keyword_gap_questions
from tailoring.title_cluster_profiles import record_cluster_fact


def _configure_cluster(cluster_name: str, titles: list[str]):
    # Reference title_cluster.SETTINGS_PATH via the module (not a name
    # imported at module load time) - isolated_data monkeypatches the
    # module attribute AFTER this test module is already imported, so a
    # top-level `from ... import SETTINGS_PATH` would bind the real,
    # un-isolated repo path instead of the tmp_path one the fixture set up.
    title_cluster.SETTINGS_PATH.write_text(
        yaml.safe_dump({"title_clusters": [{"name": cluster_name, "titles": titles}]}),
        encoding="utf-8",
    )


def test_cluster_credit_suppresses_duplicate_question_for_second_job_in_cluster(isolated_data):
    from search.title_cluster import resolve_title_cluster
    from tailoring.title_cluster_profiles import get_cluster_known_skills, get_cluster_known_units

    _configure_cluster("Executive IT Leadership", ["CIO", "Head of IT"])

    job_a = {"title": "CIO", "organization": "Acme Corp"}
    job_b = {"title": "Head of IT", "organization": "Globex Inc"}

    missing_required = [{"label": "Onshore/offshore teams", "point_value": 6.0}]

    # Job A: nothing confirmed yet for this cluster - the question is asked.
    cluster_name_a = resolve_title_cluster(job_a["title"])
    assert cluster_name_a == "Executive IT Leadership"
    questions_before = _merge_keyword_gap_questions(
        [], missing_required, previously_answered_skills=[], profile={},
        cluster_known_skills=get_cluster_known_skills(cluster_name_a),
        cluster_known_units=get_cluster_known_units(cluster_name_a),
    )
    assert any(q["skill"] == "Onshore/offshore teams" for q in questions_before)

    # Confirm the fact for job A (what save_gap_answers does internally).
    record_cluster_fact(cluster_name_a, skill="Onshore/offshore teams", evidence="Led a team of 8 (onshore/offshore).")

    # Job B, a DIFFERENT job in the SAME cluster, with its own empty
    # profile/previously_answered_skills - the cluster fact alone must be
    # enough to suppress the near-duplicate question.
    cluster_name_b = resolve_title_cluster(job_b["title"])
    assert cluster_name_b == "Executive IT Leadership"
    questions_after = _merge_keyword_gap_questions(
        [], missing_required, previously_answered_skills=[], profile={},
        cluster_known_skills=get_cluster_known_skills(cluster_name_b),
        cluster_known_units=get_cluster_known_units(cluster_name_b),
    )
    assert not any(q["skill"] == "Onshore/offshore teams" for q in questions_after)


def test_cluster_credit_never_changes_a_different_jobs_real_ats_score(isolated_data):
    """The core safety boundary: confirming a cluster fact must never
    inflate a DIFFERENT job's literal ats_score without that job's own
    resume text actually containing the keyword. score_resume_against_
    keywords (Feature 1's real scoring function) is called directly here,
    exactly the way drafting.py's own scoring call sites call it - with NO
    cluster argument, because it doesn't accept one - so this proves the
    boundary structurally, not just by convention."""
    _configure_cluster("Executive IT Leadership", ["CIO", "Head of IT"])

    required_keywords = ["Databricks"]
    # Job B's real resume text genuinely does NOT mention "Databricks".
    resume_text_without_keyword = (
        "PROFESSIONAL EXPERIENCE\nHead of IT, Globex Inc, 2020-2026\n"
        "Led enterprise infrastructure modernization across 6 sites.\n"
        "EDUCATION\nBachelor of Science, Computer Science\n"
    )

    score_before = score_resume_against_keywords(required_keywords, [], resume_text_without_keyword)

    # Confirm "Databricks" for the SAME cluster this job belongs to - the
    # cluster now has a real confirmed fact for exactly this keyword.
    record_cluster_fact("Executive IT Leadership", skill="Databricks", evidence="Built our Databricks lakehouse.")

    # Re-score the exact same resume text against the exact same required
    # keywords, through the exact same real scoring function - untouched
    # by the cluster fact just recorded, since score_resume_against_
    # keywords has no notion of title clusters at all.
    score_after = score_resume_against_keywords(required_keywords, [], resume_text_without_keyword)

    assert score_after["ats_score"] == score_before["ats_score"]
    assert score_after["missing_required_keywords"] == score_before["missing_required_keywords"]
    assert any(item["label"] == "Databricks" for item in score_after["missing_required_keywords"])


def test_ats_score_module_has_no_title_cluster_dependency():
    """Static guarantee alongside the behavioral one above: ats_score.py
    (Feature 1's real scoring module) imports nothing from Feature 2's
    title-cluster modules, so there is no code path by which a cluster
    profile could reach the scoring functions even indirectly."""
    import tailoring.ats_score as ats_score
    source = open(ats_score.__file__, encoding="utf-8").read()
    assert "title_cluster" not in source
