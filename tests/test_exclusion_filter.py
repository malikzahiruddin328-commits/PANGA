from datetime import datetime, timedelta, timezone

import pytest

from search import exclusion_filter
from security.crypto_store import read_json, write_json


def _job(title, source="Dice", job_id="1", organization="Acme", location="Remote"):
    return {"source": source, "job_id": job_id, "title": title, "organization": organization, "location": location}


# --- Layer 1: seniority-tier exclusion --------------------------------------

@pytest.mark.parametrize("title", [
    "Sr. Systems Engineer",
    "Senior Systems Engineer",
    "Data Scientist",
    "Business Analyst",
    "IT Specialist",
    "Management Consultant",
    "Customer Representative",
    "Project Coordinator",
    "Associate Product Manager",  # "Associate" IC word, no qualifier present
])
def test_seniority_layer_excludes_ic_tier_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result == {
        "rule": "seniority_mismatch",
        "reason": "individual-contributor-tier title with no executive-qualifying word present",
    }


@pytest.mark.parametrize("title", [
    "Senior Director",
    "Director, IT Service Continuity",
    "Associate Director",  # Director qualifies "Associate" the IC word
    "VP Information Technology",
    "Vice President, Engineering",
    "Head of IT",
    "Chief Technology Officer",
    "President, North America",
    "SVP, Data & Analytics",
    "EVP, Technology",
])
def test_seniority_layer_keeps_executive_qualified_titles(title):
    assert exclusion_filter.check_exclusion(_job(title)) is None


def test_seniority_layer_does_not_match_chief_inside_an_unrelated_word():
    # "mischief" contains "chief" as a substring but must not count as the
    # executive qualifier - word-boundary regression guard.
    result = exclusion_filter.check_exclusion(_job("Engineer of Mischief Management"))
    assert result is not None
    assert result["rule"] == "seniority_mismatch"


def test_seniority_layer_does_not_match_head_inside_headquarters():
    result = exclusion_filter.check_exclusion(_job("Analyst, Corporate Headquarters"))
    assert result is not None
    assert result["rule"] == "seniority_mismatch"


# --- Layer 2: clinical/medical domain exclusion -----------------------------

@pytest.mark.parametrize("title", [
    "Senior Medical Director, Hematology Clinical Development",
    "Medical Director",
    "Physician - Internal Medicine",
    "Nurse Practitioner",
    "Registered Nurse - ICU",
    "Clinical Research Director - Immunology",
    "Director, Clinical Pharmacology",
    "Medical Science Liaison - Dermatology",
    "Medical Advisor, Oncology",
])
def test_clinical_layer_excludes_medical_domain_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "clinical_domain"


def test_title_matching_both_layers_is_still_excluded():
    # "Clinical Scientist" carries "Scientist" (an IC-tier noun with no
    # executive qualifier present) AND matches the clinical pattern - both
    # layers independently agree to exclude it. Layer 1 is checked first in
    # check_exclusion(), so the reported rule is seniority_mismatch here,
    # not clinical_domain - what matters is that the job is excluded
    # either way, not which rule's label wins the race.
    result = exclusion_filter.check_exclusion(_job("Clinical Scientist, Oncology"))
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "clinical_domain")


def test_clinical_layer_fires_even_when_seniority_would_have_kept_it():
    # "Medical Director" carries "Director", which would satisfy the
    # seniority layer's exec-qualifier check on its own - layer 2 must
    # still exclude it independently.
    result = exclusion_filter.check_exclusion(_job("Senior Medical Director, Hematology Clinical Development"))
    assert result["rule"] == "clinical_domain"


def test_known_good_examples_are_kept():
    # The exact real-data validation examples from the request.
    assert exclusion_filter.check_exclusion(_job("Director, IT Service Continuity", organization="AbbVie")) is None


def test_known_bad_examples_are_excluded():
    assert exclusion_filter.check_exclusion(
        _job("Senior Medical Director, Hematology Clinical Development", organization="AbbVie")
    )["rule"] == "clinical_domain"
    assert exclusion_filter.check_exclusion(
        _job("Sr. Systems Engineer", organization="AbbVie")
    )["rule"] == "seniority_mismatch"


def test_no_exclusion_for_a_plausible_unrelated_title():
    assert exclusion_filter.check_exclusion(_job("Chief Information Officer")) is None


# --- Layer 3: custom user exclusions (2026-08-13, Settings tab build) ------

def test_custom_exclusion_matches_case_insensitive_substring():
    result = exclusion_filter.check_exclusion(
        _job("Senior Program Director, Clinical Ops"),
        custom_exclusions=["program director"],
    )
    assert result == {
        "rule": "custom_user_exclusion",
        "reason": 'matched custom excluded term "program director"',
    }


def test_custom_exclusion_is_a_free_form_substring_match_not_word_boundary():
    # Deliberately NOT \b-bounded like the built-in layers - a non-technical
    # user typing a fragment expects plain "contains this text", e.g. "CISO"
    # should catch "Deputy CISO" and "intern" should catch "Internship".
    assert exclusion_filter.check_exclusion(
        _job("Deputy CISO"), custom_exclusions=["CISO"],
    )["rule"] == "custom_user_exclusion"
    assert exclusion_filter.check_exclusion(
        _job("Summer Internship Program"), custom_exclusions=["Intern"],
    )["rule"] == "custom_user_exclusion"


def test_custom_exclusion_empty_list_has_no_effect():
    assert exclusion_filter.check_exclusion(
        _job("Director, IT Service Continuity"), custom_exclusions=[],
    ) is None


def test_custom_exclusion_term_matching_nothing_has_no_effect_and_no_error():
    assert exclusion_filter.check_exclusion(
        _job("Director, IT Service Continuity"),
        custom_exclusions=["Underwater Basket Weaving Lead"],
    ) is None


def test_custom_exclusion_handles_blank_and_whitespace_only_terms_gracefully():
    # A term list with stray empty strings (shouldn't happen given
    # ui/app.py's save-time cleaning, but this is a public function other
    # callers could hit directly) must not raise or match everything.
    assert exclusion_filter.check_exclusion(
        _job("Director, IT Service Continuity"),
        custom_exclusions=["", "   ", "Underwater Basket Weaving Lead"],
    ) is None


def test_custom_exclusion_still_fires_when_built_in_layers_pass():
    # "Program Director" alone passes both built-in layers (Director
    # qualifies past seniority, no clinical match) - layer 3 must still be
    # able to exclude it on its own.
    assert exclusion_filter.check_exclusion(_job("Program Director")) is None
    result = exclusion_filter.check_exclusion(
        _job("Program Director"), custom_exclusions=["Program Director"],
    )
    assert result["rule"] == "custom_user_exclusion"


def test_check_exclusion_defaults_to_loading_custom_exclusions_from_settings(isolated_data):
    exclusion_filter.SETTINGS_PATH.write_text(
        "custom_title_exclusions:\n- Program Director\n- CISO\n", encoding="utf-8",
    )
    result = exclusion_filter.check_exclusion(_job("Deputy CISO"))
    assert result["rule"] == "custom_user_exclusion"


def test_load_custom_title_exclusions_missing_file_returns_empty_list(isolated_data):
    assert exclusion_filter.load_custom_title_exclusions() == []


def test_load_custom_title_exclusions_missing_key_returns_empty_list(isolated_data):
    exclusion_filter.SETTINGS_PATH.write_text("industries:\n- Pharma\n", encoding="utf-8")
    assert exclusion_filter.load_custom_title_exclusions() == []


def test_load_custom_title_exclusions_reads_configured_terms(isolated_data):
    exclusion_filter.SETTINGS_PATH.write_text(
        "custom_title_exclusions:\n- Project Manager\n- Intern\n", encoding="utf-8",
    )
    assert exclusion_filter.load_custom_title_exclusions() == ["Project Manager", "Intern"]


# --- Layer 4: generic administrative/clerical/demo-support exclusion -------

@pytest.mark.parametrize("title", [
    "Administrative Assistant",
    "Senior Administrative Assistant",
    "Senior Administrative Assistant, IMCO, Eyecare & Specialty",  # real AbbVie job
    "Executive Assistant",
    "Executive Assistant to the CEO",
    "Office Manager",
    "Receptionist",
    "Value Proposition and Demonstration Manager - Onco Solids",  # real AbbVie job
    "Demonstration Manager",
    "Value Proposition Manager",
])
def test_admin_support_layer_excludes_generic_non_technical_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "administrative_support_role"


def test_front_desk_coordinator_excluded_by_seniority_layer_first():
    # "Front Desk Coordinator" also matches this layer's own pattern, but
    # layer 1 (Coordinator = IC-tier noun, no exec qualifier) runs first in
    # check_exclusion() and claims it - same "excluded either way, rule
    # label isn't the point" situation as the existing
    # test_title_matching_both_layers_is_still_excluded() case above.
    result = exclusion_filter.check_exclusion(_job("Front Desk Coordinator"))
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "administrative_support_role")


@pytest.mark.parametrize("title", [
    "Systems Administrator",
    "Database Administrator",
    "Network Administrator",
    "Operational Technology Systems Administrator",  # real AbbVie job
    "FVP/SVP, Credit Administrator",  # real AbbVie job - "Administrator" != "Administrative Assistant"
    "Associate CIO, Administrative Applications",  # real AbbVie job - "Administrative Applications" != "Administrative Assistant"
    "IT Office Manager",  # hedge: tech-qualified variant of a caught phrase
    "Digital Demonstration Manager",  # hedge: tech-qualified variant of a caught phrase
    "Chief Information Officer",
    "Director, IT Service Continuity",
    "VP Information Technology",
])
def test_admin_support_layer_does_not_catch_technical_or_unrelated_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "administrative_support_role"


def test_admin_support_layer_real_slipped_through_examples():
    # The exact two real jobs Zahir flagged as having slipped through
    # unfiltered from AbbVie's company-site pull.
    assert exclusion_filter.check_exclusion(
        _job("Value Proposition and Demonstration Manager - Onco Solids", organization="AbbVie")
    )["rule"] == "administrative_support_role"
    assert exclusion_filter.check_exclusion(
        _job("Senior Administrative Assistant", organization="AbbVie")
    )["rule"] == "administrative_support_role"


# --- Logging: the non-negotiable "never silently dropped" requirement ------

def test_log_exclusions_appends_full_record(isolated_data):
    job = _job("Registered Nurse", job_id="42", organization="Big Health System", location="Dallas, TX")
    exclusion = {"rule": "clinical_domain", "reason": "clinical/medical domain role (matched \"registered nurse\")"}
    exclusion_filter.log_exclusions([(job, exclusion)])

    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "Dice"
    assert entry["job_id"] == "42"
    assert entry["title"] == "Registered Nurse"
    assert entry["organization"] == "Big Health System"
    assert entry["location"] == "Dallas, TX"
    assert "clinical_domain" in entry["exclusion_reason"]
    assert "timestamp" in entry


def test_log_exclusions_is_a_no_op_on_empty_list(isolated_data):
    exclusion_filter.log_exclusions([])
    assert not exclusion_filter.EXCLUSION_LOG_PATH.exists()


def test_log_exclusions_dedupes_against_already_logged_source_job_id(isolated_data):
    job = _job("Registered Nurse", job_id="1")
    exclusion = {"rule": "clinical_domain", "reason": "x"}
    exclusion_filter.log_exclusions([(job, exclusion)])
    # Same job resurfacing in a later search run (still-open posting) must
    # not create a second log entry - see log_exclusions()'s own docstring
    # on unbounded growth.
    exclusion_filter.log_exclusions([(job, exclusion)])

    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert len(entries) == 1


def test_log_exclusions_accumulates_distinct_jobs(isolated_data):
    exclusion_filter.log_exclusions([
        (_job("Registered Nurse", job_id="1"), {"rule": "clinical_domain", "reason": "a"}),
        (_job("Sr. Systems Engineer", job_id="2"), {"rule": "seniority_mismatch", "reason": "b"}),
    ])
    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    assert len(entries) == 2
    assert [e["job_id"] for e in entries] == ["1", "2"]


# --- list_exclusions(): 30-day default view, full-history "show all" -------

def _seed_entry(timestamp_iso, job_id="1"):
    """Writes one exclusion-log entry directly (bypassing log_exclusions()'s
    "now" timestamp) so tests can control exactly how old an entry is."""
    entries = read_json(exclusion_filter.EXCLUSION_LOG_PATH, default=[])
    entries.append({
        "timestamp": timestamp_iso,
        "source": "Dice",
        "job_id": job_id,
        "title": "Registered Nurse",
        "organization": "Big Health System",
        "location": "Dallas, TX",
        "exclusion_reason": "clinical_domain: x",
    })
    write_json(exclusion_filter.EXCLUSION_LOG_PATH, entries)


def test_list_exclusions_default_excludes_entries_older_than_30_days(isolated_data):
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=45)).isoformat(), job_id="old")
    _seed_entry((now - timedelta(days=5)).isoformat(), job_id="recent")

    result = exclusion_filter.list_exclusions()

    assert [e["job_id"] for e in result] == ["recent"]


def test_list_exclusions_days_back_none_returns_full_history(isolated_data):
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=45)).isoformat(), job_id="old")
    _seed_entry((now - timedelta(days=5)).isoformat(), job_id="recent")

    result = exclusion_filter.list_exclusions(days_back=None)

    assert {e["job_id"] for e in result} == {"old", "recent"}


def test_list_exclusions_custom_days_back(isolated_data):
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=10)).isoformat(), job_id="within_7")
    _seed_entry((now - timedelta(days=3)).isoformat(), job_id="within_7_too")

    result = exclusion_filter.list_exclusions(days_back=7)

    assert [e["job_id"] for e in result] == ["within_7_too"]


def test_list_exclusions_on_empty_log_returns_empty_list(isolated_data):
    assert exclusion_filter.list_exclusions() == []
    assert exclusion_filter.list_exclusions(days_back=None) == []


def test_list_exclusions_boundary_entry_just_inside_30_days_is_included(isolated_data):
    # Just inside the 30-day window (29d 23h ago, not exactly "days=30" to
    # avoid a flaky race against list_exclusions() computing its own "now"
    # a moment after this seed does) - confirms no off-by-one excludes an
    # entry that should still be in the default view.
    now = datetime.now(timezone.utc)
    _seed_entry((now - timedelta(days=29, hours=23)).isoformat(), job_id="boundary")

    result = exclusion_filter.list_exclusions(days_back=30)

    assert [e["job_id"] for e in result] == ["boundary"]
