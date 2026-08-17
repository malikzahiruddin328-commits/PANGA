from tailoring.title_cluster_profiles import (
    get_cluster_known_skills,
    get_cluster_known_units,
    load_cluster_profiles,
    record_cluster_fact,
)


def test_record_cluster_fact_is_noop_without_a_cluster(isolated_data):
    record_cluster_fact(None, skill="Onshore/offshore teams", evidence="Led a team of 8.")
    assert load_cluster_profiles() == {}


def test_record_cluster_fact_seeds_a_new_cluster(isolated_data):
    record_cluster_fact("Executive IT Leadership", skill="Onshore/offshore teams", evidence="Led a team of 8 (onshore/offshore).")
    profiles = load_cluster_profiles()
    assert "Executive IT Leadership" in profiles
    entry = profiles["Executive IT Leadership"][0]
    assert entry["skill"] == "Onshore/offshore teams"
    assert entry["evidence"] == "Led a team of 8 (onshore/offshore)."
    assert entry["confirmed_at"]


def test_record_cluster_fact_is_idempotent_for_an_equivalent_label(isolated_data):
    record_cluster_fact("Executive IT Leadership", skill="Multi-site operations", evidence="Ran 6 sites.")
    record_cluster_fact("Executive IT Leadership", skill="multi site operations", evidence="Ran 6 sites across 3 countries.")
    profiles = load_cluster_profiles()
    entries = profiles["Executive IT Leadership"]
    assert len(entries) == 1
    assert entries[0]["evidence"] == "Ran 6 sites across 3 countries."


def test_record_cluster_fact_appends_a_genuinely_distinct_skill(isolated_data):
    record_cluster_fact("Executive IT Leadership", skill="Multi-site operations", evidence="Ran 6 sites.")
    record_cluster_fact("Executive IT Leadership", skill="Budget ownership", evidence="Owned a $40M IT budget.")
    entries = load_cluster_profiles()["Executive IT Leadership"]
    assert len(entries) == 2


def test_get_cluster_known_skills_returns_empty_for_unknown_or_none_cluster(isolated_data):
    assert get_cluster_known_skills(None) == []
    assert get_cluster_known_skills("Nonexistent cluster") == []


def test_get_cluster_known_skills_returns_confirmed_labels(isolated_data):
    record_cluster_fact("Executive IT Leadership", skill="Multi-site operations", evidence="Ran 6 sites.")
    assert get_cluster_known_skills("Executive IT Leadership") == ["Multi-site operations"]


def test_get_cluster_known_units_includes_skill_and_evidence_as_separate_units(isolated_data):
    record_cluster_fact("Executive IT Leadership", skill="Multi-site operations", evidence="Ran 6 sites across 3 countries.")
    units = get_cluster_known_units("Executive IT Leadership")
    assert "Multi-site operations" in units
    assert "Ran 6 sites across 3 countries." in units
    assert len(units) == 2
