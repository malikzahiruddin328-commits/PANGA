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


# --- Layer 3: project/program/product management track exclusion -----------

@pytest.mark.parametrize("title", [
    "Project Director",  # real title, Neil Hoosier & Associates
    "DHS PROGRAM DIRECTOR 4 - 79704",  # real title
    "VP of Product Management, Monetization",  # real title, Yelp
    "Head of Product Management – Intelligence Ventures",  # real title, SPECTRUM
    "Project Manager",
    "Senior Project Manager",
    "Program Manager",
    "IT Program Manager",
    "IT Project Manager",
    "Director, Product Management",
    "Director of Product Management, Hardware",
    "Product Director",
    "Vice President, Program Management Office",
    "Head of Program Management Office (PMO)",
])
def test_pm_track_layer_excludes_pm_pgm_prodm_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "pm_track_mismatch"


def test_pm_track_layer_excludes_even_when_seniority_layer_would_win_the_label():
    # "IT PMO Consultant..." also carries "Consultant" (an IC-tier noun
    # with no executive qualifier), so layer 1 (checked first) reports
    # seniority_mismatch - same "which label wins the race doesn't matter,
    # both layers agree to exclude" situation as "Clinical Scientist"
    # above. What matters is the job is excluded either way.
    result = exclusion_filter.check_exclusion(
        _job("IT PMO Consultant - Project Governance & Portfolio Management")
    )
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "pm_track_mismatch")


@pytest.mark.parametrize("title", [
    "Director, IT Service Continuity",  # real validated KEEP, AbbVie
    "IT Director, Vendor Management",
    "Director, Product Engineering",  # "Director" precedes "Product" - different sense
    "Chief Information Officer (CIO)",
    "Head of IT",
    "VP Information Technology",
])
def test_pm_track_layer_does_not_catch_it_leadership_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "pm_track_mismatch"


# --- Layer 4: intern/internship exclusion ------------------------------------

@pytest.mark.parametrize("title", [
    "Intern - Biotechnologist (Protein)",  # real title
    "Fall 2026 IT Intern (Incident Responder)",  # real title
    "2027 Accounting & Finance Development Program Intern (Undergraduate)",  # real title
    "Interested in an internship?",  # real title, junk/vague
    "Summer IT Intern",
])
def test_intern_layer_excludes_intern_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "intern_role"


def test_intern_layer_does_not_catch_a_role_directing_an_internship_program():
    # Real title in the live store - this role DIRECTS an internship
    # program, it isn't an intern position, and carries "Director" as an
    # executive qualifier, same exemption shape as layer 1.
    result = exclusion_filter.check_exclusion(_job("Dietitian (Dietetic Internship Director)"))
    assert result is None or result["rule"] != "intern_role"


# --- Layer 5: information-security-domain exclusion -------------------------

@pytest.mark.parametrize("title", [
    "VP, Information Security and Compliance",  # real title, Veritone Corp
    "Director of Information Security (Hybrid)",  # real title, SAGE Dining Services
    "Information Security Manager",
    "VP of Information Security",
    "Vice President, Information Security",
    "Director, IT, Information Security & Data Privacy",
    "Service Information Security Officer (SISO)",  # real title - domain officer, not chief-executive
])
def test_information_security_layer_excludes_domain_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] == "information_security_domain"


@pytest.mark.parametrize("title", [
    # "Analyst"/"Specialist" are also layer 1 IC-tier nouns with no
    # executive qualifier - same "which label wins the race doesn't matter,
    # both layers agree to exclude" situation as "Clinical Scientist" above.
    "Information Security Analyst",
    "Information Security Specialist",
    "IT Spec (Infosec), GS-2210-14, FPL 14 (DH) (Open-Continuous)",  # real title, "infosec" variant
])
def test_information_security_layer_excludes_even_when_another_layer_would_win_the_label(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is not None
    assert result["rule"] in ("seniority_mismatch", "information_security_domain")


@pytest.mark.parametrize("title", [
    "Chief Information Officer (CIO)",
    "Chief Information Officer",
    "Chief Information Security Officer",  # real title - CISO chief-executive title, must survive
    "Chief Information Security Officer, NB-2210-VIII",  # real title
    "Group Chief Information Security Officer",  # real title
    "SVP, Chief Information Security Officer",  # real title
    "VP, Infrastructure & Chief Information Security Officer",  # real title
    "Chief Information Security Officer (CISO) - AI Trainer",  # real title
    "VP/CISO, Information Security",  # real title - bare CISO abbreviation exempts it
])
def test_information_security_layer_does_not_catch_cio_or_ciso_titles(title):
    result = exclusion_filter.check_exclusion(_job(title))
    assert result is None or result["rule"] != "information_security_domain"


def test_information_security_layer_does_not_match_unrelated_titles():
    # Neither "information" nor "security" alone should trip this layer -
    # it's scoped to the literal "information security" domain phrase.
    assert exclusion_filter.check_exclusion(_job("Director, IT Service Continuity")) is None
    assert exclusion_filter.check_exclusion(_job("VP Information Technology")) is None
    assert exclusion_filter.check_exclusion(_job("Director of Security Operations")) is None


def test_information_security_layer_fires_even_when_seniority_would_have_kept_it():
    # "Director of Information Security" carries "Director", which would
    # satisfy the seniority layer's exec-qualifier check on its own - layer
    # 5 must still exclude it independently, same shape as layer 2's
    # "Medical Director" regression guard.
    result = exclusion_filter.check_exclusion(_job("Director of Information Security (Hybrid)"))
    assert result["rule"] == "information_security_domain"


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
