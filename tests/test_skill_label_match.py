from skill_label_match import normalize_skill_label, skills_match


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
