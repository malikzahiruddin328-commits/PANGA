from skill_label_match import (
    build_already_known_units,
    filter_questions_evidenced_in_profile,
    normalize_skill_label,
    skill_evidenced_in_text,
    skills_match,
)


def test_normalize_skill_label_is_public_and_used_by_drafting_py():
    # Made public (was module-private) 2026-08-09 so drafting.py's
    # generic-soft-skill deny-list can reuse the same normalization
    # skills_match() already relies on internally, rather than a second,
    # independently-drifting implementation.
    assert normalize_skill_label("Master's Degree") == "masters degree"


def test_exact_match():
    assert skills_match("Databricks", "Databricks") is True


def test_case_and_punctuation_insensitive():
    assert skills_match("Databricks", "databricks,") is True
    assert skills_match("Master's Degree", "masters degree") is True


def test_whitespace_insensitive():
    assert skills_match("Databricks   certification", "Databricks certification") is True


def test_one_label_is_a_real_phrase_within_the_other():
    assert skills_match("Databricks certification", "Databricks") is True
    assert skills_match("Databricks", "Databricks certification") is True


def test_short_label_does_not_bare_substring_match_an_unrelated_word():
    # Real gap flagged by Mirror 2026-08-08 - same class of bug as this
    # week's ats_score.py "it"-pronoun fix: "IT" is a bare substring of
    # "credit"/"legitimate", but not a real phrase match.
    assert skills_match("IT", "Credit risk modeling") is False
    assert skills_match("IT", "Legitimate business interest analysis") is False


def test_genuinely_different_wording_does_not_match():
    # Documented, known limitation - not a bug this can fix without real
    # semantic understanding: normalization/word-boundary matching can't
    # tell that "AWS" and "cloud infrastructure" refer to the same fact.
    assert skills_match("AWS", "cloud infrastructure") is False


def test_empty_labels_never_match():
    assert skills_match("", "") is False
    assert skills_match("", "Databricks") is False


# --- 2026-08-17 real production incident (skill_label_match.py's own
# docstring item 3): Zahir was asked "The posting requires 'onshore/
# offshore teams' - do you have real, genuine experience with it?" even
# though (a) that exact fact is buried inside the free-text ANSWER to a
# DIFFERENTLY-labeled gap question ("si partner relationships", answered
# 2026-08-07 - the "skill" LABEL never mentions onshore/offshore at all)
# and (b) it's stated directly across several real work_history/
# client_engagements bullets. Both facts confirmed against the real
# production master_profile.json before writing this test - not a
# synthetic guess at the shape of the data. skills_match()/label-only
# dedup can never catch either case; build_already_known_units() +
# skill_evidenced_in_text() are the real fix.

_REAL_SI_PARTNER_GAP_ANSWER = (
    "Yes. I have substantial experience building and managing "
    "systems-integrator (SI), managed-service-provider, and strategic "
    "technology-partner relationships across enterprise transformation "
    "programmes.\n\nAt SK Life Science, I directed specialist partners "
    "supporting SAP S/4HANA, Ariba, Veeva, data/MDM, cybersecurity, "
    "validation, helpdesk, NOC, and SOC operations. I owned vendor "
    "selection, statements of work, delivery governance, performance "
    "management, budget oversight, service quality, and compliance "
    "accountability.\n\nEarlier, in consulting leadership roles, I "
    "managed multi-client delivery programmes and partner relationships "
    "with IBM, Microsoft, TIBCO, Tableau, QlikView, Tamr, and Kalido for "
    "organisations including Eisai, AbbVie, TD Bank, Great American "
    "Insurance, and Univision. I coordinated onshore and offshore "
    "delivery teams, aligned partners to business outcomes, and ensured "
    "delivery across data, analytics, MDM, cloud, and integration "
    "initiatives."
)


def _real_shaped_profile() -> dict:
    return {
        "gap_interview_answers": [
            {
                "skill": "si partner relationships",
                "answer": _REAL_SI_PARTNER_GAP_ANSWER,
                "canonical_skill_id": "systems_integrator_delivery_partner_names",
                "is_disqualifier": False,
            },
        ],
        "work_history": [
            {
                "employer": "Streebo, Inc.",
                "title": "US Professional Services Director",
                "bullets": ["Managed 25 US-based and 50 offshore consultants."],
            },
        ],
        "client_engagements": [
            {
                "client": "The Brick (Canada)",
                "bullets": ["Led team of 15 consultants (onshore/offshore)."],
            },
        ],
    }


def test_skill_label_alone_would_miss_the_real_onshore_offshore_case():
    # Documents WHY this bug reached a real user: the pre-existing,
    # label-only dedup (skills_match against gap_interview_answers'
    # "skill" field) genuinely cannot catch this - the label stored is
    # "si partner relationships", not "onshore/offshore teams".
    assert skills_match("onshore/offshore teams", "si partner relationships") is False


def test_skill_evidenced_in_text_catches_fact_buried_in_a_differently_labeled_answer():
    corpus = build_already_known_units(_real_shaped_profile())
    assert skill_evidenced_in_text("onshore/offshore teams", corpus) is True


def test_skill_evidenced_in_text_catches_fact_stated_only_in_a_resume_bullet():
    profile = {"gap_interview_answers": [], "work_history": [
        {"employer": "Eisai Pharmaceuticals", "bullets": ["Led a team of 8 (onshore/offshore)."]},
    ], "client_engagements": []}
    corpus = build_already_known_units(profile)
    assert skill_evidenced_in_text("onshore/offshore teams", corpus) is True


def test_skill_evidenced_in_text_requires_at_least_two_significant_words():
    # Single-word labels are deliberately left to skills_match()/
    # previously_answered_skills - a conjunctive one-word "match" would be
    # a bare, unqualified presence check and far too prone to false
    # positives (see this function's own docstring).
    corpus = build_already_known_units(_real_shaped_profile())
    assert skill_evidenced_in_text("delivery", corpus) is False


def test_skill_evidenced_in_text_does_not_false_positive_on_unrelated_label():
    corpus = build_already_known_units(_real_shaped_profile())
    assert skill_evidenced_in_text("kubernetes cluster administration", corpus) is False


def test_filter_questions_evidenced_in_profile_drops_the_real_reported_question():
    corpus = build_already_known_units(_real_shaped_profile())
    questions = [
        {
            "type": "skill_gap",
            "skill": "onshore/offshore teams",
            "question": "The posting requires \"onshore/offshore teams\" - do you have real, genuine experience with it?",
            "suggested_answer": "",
        },
        {
            "type": "skill_gap",
            "skill": "kubernetes cluster administration",
            "question": "The posting requires \"kubernetes cluster administration\" - do you have real, genuine experience with it?",
            "suggested_answer": "",
        },
    ]
    filtered = filter_questions_evidenced_in_profile(questions, corpus)
    assert [q["skill"] for q in filtered] == ["kubernetes cluster administration"]


def test_filter_questions_evidenced_in_profile_empty_corpus_keeps_everything():
    questions = [{"skill": "onshore/offshore teams", "question": "x"}]
    assert filter_questions_evidenced_in_profile(questions, "") == questions


def test_build_already_known_units_handles_missing_profile():
    assert build_already_known_units(None) == []
    assert build_already_known_units({}) == []
